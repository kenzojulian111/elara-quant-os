# interfaces/mqtt_service.py
import asyncio
import time
import json
import logging
import copy
import threading
from collections import deque

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion
    MQTT_AVAILABLE = True
except Exception:
    MQTT_AVAILABLE = False
    mqtt = None

from config.settings import Config
from utils.common import async_runner
from utils.health import snapshot as health_snapshot

log = logging.getLogger("elara.mqtt")

# State Global yang cocok dengan Dashboard HTML kamu
system_state = {
    "arus": 0.0,
    "suhu": 0.0,
    "kwhToday": 0.0,
    "rupiahToday": 0,
    "logs": deque(maxlen=max(20, int(getattr(Config, "MQTT_LOG_MAXLEN", 120)))),
    "devices": {}, # Akan diisi otomatis dari config.json
    "trading_metrics": {
        "total_net_pnl": 0.0,
        "win_rate": 0.0,
        "equity": 0.0,
        "cash": 0.0,
        "equity_history": [],
        "pnl_history": []
    },
    "log_flood": False,
}

# Mutable global state is shared by MQTT callback threads and web handlers.
state_lock = threading.Lock()
_log_events = deque()
_flood_until = 0.0

def _push_log(msg: str):
    # Format log sesuai selera dashboard
    # system_state["logs"].append(f"{datetime.now().strftime('%H:%M:%S')} - {msg}")
    # Dashboard kamu memparsing log, jadi kita kirim raw message atau format simpel
    sample_rate = float(getattr(Config, "MQTT_LOG_SAMPLE_RATE", 1.0) or 1.0)
    text = str(msg or "")
    is_important = any(marker in text.lower() for marker in ("error", "failed", "warn", "fallback_alert"))
    if sample_rate < 1.0 and not is_important:
        import random
        if random.random() > max(0.0, sample_rate):
            return
    with state_lock:
        system_state["logs"].append(text)
    log.info(msg)
    now = time.time()
    with state_lock:
        _log_events.append(now)
        # buang event lebih lama dari 60s
        cutoff = now - 60
        while _log_events and _log_events[0] < cutoff:
            _log_events.popleft()
        rate = len(_log_events)
        if rate > getattr(Config, "LOG_FLOOD_PER_MIN", 500):
            system_state["log_flood"] = True
        elif rate < getattr(Config, "LOG_FLOOD_PER_MIN", 500) // 2:
            system_state["log_flood"] = False

class DeviceStateManager:
    """Mengelola status on/off, suhu, brightness device."""
    def __init__(self, config):
        self.config = config
        self._init_devices()

    def _init_devices(self):
        # Inisialisasi state devices berdasarkan config.json
        raw_devs = self.config.get("devices", {})
        with state_lock:
            for type, rooms in raw_devs.items():
                if type not in system_state["devices"]:
                    system_state["devices"][type] = {}
                
                for room in rooms:
                    # Default state
                    system_state["devices"][type][room] = {
                        "power": False,
                        "temp": 24,         # Khusus AC
                        "brightness": 80,   # Khusus Lampu
                        "volume": 30,       # Khusus Speaker/TV
                        "channel": 1,       # Khusus TV
                        "locked": True,     # Khusus Pintu
                        "open": False,      # Khusus Gorden
                        "active": False     # Khusus CCTV
                    }

    def update_device(self, type, room, key, value):
        with state_lock:
            if type in system_state["devices"] and room in system_state["devices"][type]:
                system_state["devices"][type][room][key] = value
                return True
        return False

class MQTTService:
    def __init__(self, socketio, device_config, state_manager=None, db_manager=None):
        self.socketio = socketio
        self.device_config = device_config
        self.client = None
        self.device_manager = DeviceStateManager(device_config)
        self.state_manager = state_manager
        self.db_manager = db_manager
        self._sync_devices_state()
        self._broadcast_task = None
        self._mqtt_loop_task = None
        self._last_metrics_ts = 0
        self._metrics_interval_sec = 10
        self._running = False
        self._reconnect_backoff = 1.0
        self._max_reconnect_backoff = max(5.0, float(getattr(Config, "MQTT_RECONNECT_BACKOFF_MAX_SEC", 60.0)))

    def _sync_devices_state(self):
        if not self.state_manager:
            return
        try:
            with state_lock:
                snapshot = copy.deepcopy(system_state["devices"])
            self.state_manager.set_state("devices_state", snapshot, user_id="system")
        except Exception as e:
            log.warning(f"Gagal sync device state ke StateManager: {e}")

    def start(self):
        self._running = True
        if not MQTT_AVAILABLE:
            log.warning("Paho MQTT lib not installed. MQTT sensor updates disabled.")
            self._start_broadcast_loop()
            return

        try:
            if self.client and getattr(self.client, "is_connected", lambda: False)():
                self._start_broadcast_loop()
                self._start_mqtt_loop()
                return
            self.client = mqtt.Client(CallbackAPIVersion.VERSION2)
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.on_disconnect = self._on_disconnect
            
            log.info(f"Connecting to MQTT {Config.MQTT_HOST}...")
            self.client.connect(Config.MQTT_HOST, Config.MQTT_PORT, 60)

            # Start async loops for MQTT and UI broadcast
            self._start_mqtt_loop()
            self._start_broadcast_loop()
            
        except Exception as e:
            log.error(f"MQTT Start Error: {e}")

    def _start_broadcast_loop(self):
        if self._broadcast_task and not self._broadcast_task.done():
            return
        self._broadcast_task = async_runner.run_coroutine_fire_and_forget(self._broadcast_loop())

    def _start_mqtt_loop(self):
        if self._mqtt_loop_task and not self._mqtt_loop_task.done():
            return
        self._mqtt_loop_task = async_runner.run_coroutine_fire_and_forget(self._mqtt_loop())

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        log.info(f"[MQTT] Connected RC={rc}")
        self._reconnect_backoff = 1.0
        client.subscribe([(Config.TOPIK_ARUS, 0), (Config.TOPIK_SUHU, 0)])

    def _on_disconnect(self, client, userdata, rc, properties=None):
        if not self._running:
            return
        log.warning(f"[MQTT] Disconnected RC={rc}. Reconnect loop active.")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode('utf-8', errors='ignore')
            topic = msg.topic
            
            if topic == Config.TOPIK_ARUS:
                val = float(payload)
                with state_lock:
                    system_state["arus"] = val
                    
                    # Hitung kWh & Rupiah sederhana
                    # Asumsi interval kirim 1 detik, 220V
                    power_kw = (220 * val) / 1000
                    kwh_increment = power_kw / 3600 
                    system_state["kwhToday"] += kwh_increment
                    system_state["rupiahToday"] = int(system_state["kwhToday"] * 1700) # Tarif per kWh

            elif topic == Config.TOPIK_SUHU:
                with state_lock:
                    system_state["suhu"] = float(payload)

        except Exception as e:
            log.error(f"MQTT Msg Error: {e}")

    async def _mqtt_loop(self):
        while self._running and self.client:
            try:
                await asyncio.to_thread(self.client.loop, timeout=1.0)
                is_connected = False
                try:
                    is_connected = bool(await asyncio.to_thread(self.client.is_connected))
                except Exception:
                    is_connected = False
                if not is_connected:
                    try:
                        await asyncio.to_thread(self.client.reconnect)
                        self._reconnect_backoff = 1.0
                    except Exception as e:
                        wait_sec = min(self._max_reconnect_backoff, self._reconnect_backoff)
                        log.warning(f"MQTT reconnect failed, retry in {wait_sec:.1f}s: {e}")
                        await asyncio.sleep(wait_sec)
                        self._reconnect_backoff = min(self._max_reconnect_backoff, self._reconnect_backoff * 2)
            except Exception as e:
                log.error(f"MQTT loop error: {e}")
                wait_sec = min(self._max_reconnect_backoff, self._reconnect_backoff)
                await asyncio.sleep(wait_sec)
                self._reconnect_backoff = min(self._max_reconnect_backoff, self._reconnect_backoff * 2)

    async def _broadcast_loop(self):
        """Loop untuk update UI tiap detik"""
        base = max(0.5, float(getattr(Config, "MQTT_BROADCAST_INTERVAL_SEC", 1.0)))
        if getattr(Config, "ECO_MODE", False):
            base = base * max(1.0, float(getattr(Config, "ECO_POLL_MULTIPLIER", 2.0)))
        while self._running:
            await self._emit_update()
            await asyncio.sleep(base)

    def stop(self):
        self._running = False
        for task in (self._mqtt_loop_task, self._broadcast_task):
            if task and not task.done():
                task.cancel()
        if self.client:
            try:
                self.client.disconnect()
            except Exception as e:
                log.debug(f"MQTT disconnect warning: {e}")

    def is_healthy(self):
        if not self._running:
            return True
        if not MQTT_AVAILABLE:
            return self._broadcast_task is None or not self._broadcast_task.done()
        if not self.client:
            return False
        if self._broadcast_task and self._broadcast_task.done():
            return False
        if self._mqtt_loop_task and self._mqtt_loop_task.done():
            return False
        try:
            if hasattr(self.client, "is_connected") and not self.client.is_connected():
                return False
        except Exception:
            return False
        return True

    async def _emit_update(self):
        # Skip emit saat log flood untuk mengurangi beban UI
        now = time.time()
        with state_lock:
            is_flood = system_state.get("log_flood", False)
        if is_flood:
            if now - getattr(self, "_last_flood_warn", 0) > 10:
                log.warning("Log flood detected, throttling dashboard broadcast.")
                self._last_flood_warn = now
            await asyncio.sleep(1.0)
            return

        self._update_trading_metrics()
        if self.socketio:
            # Kirim SELURUH state agar dashboard merender ulang
            with state_lock:
                snapshot = {
                    "suhu": system_state["suhu"],
                    "arus": system_state["arus"],
                    "kwhToday": round(system_state["kwhToday"], 3),
                    "rupiahToday": system_state["rupiahToday"],
                    "logs": list(system_state["logs"]),
                    "devices": system_state["devices"],
                    "trading_metrics": system_state["trading_metrics"],
                    "health": health_snapshot(),
                    "log_flood": system_state.get("log_flood", False),
                    "presence": self._get_presence_state()
                }
            self.socketio.emit('status_update', snapshot)
            
            # Emit data chart terpisah kalau perlu
            # self.socketio.emit('power_daily', {'series': ...})

    def publish(self, topic, payload):
        if self.client:
            try:
                self.client.publish(topic, payload)
                return True
            except Exception as e:
                log.error(f"MQTT publish error: {e}")
                return False
        log.info("MQTT publish skipped (client not available).")
        return False

    # Fungsi untuk dipanggil dari Web/Command untuk update state device
    def control_device(self, type, room, action, value=None):
        # Update state internal
        with state_lock:
            devs = system_state["devices"]
            if type in devs and room in devs[type]:
                target = devs[type][room]
                
                if action == "on": target["power"] = True
                elif action == "off": target["power"] = False
                elif action == "set" and value is not None:
                    # Mapping properti berdasarkan tipe
                    if type == "ac": target["temp"] = int(value)
                    elif type == "lampu": target["brightness"] = int(value)
                    # ... dst
        
        self._sync_devices_state()
        # Log & Publish MQTT (kalau ada)
        _push_log(f"Control: {type} {room} -> {action} {value if value else ''}")
        # self.client.publish(...)
        async_runner.run_coroutine_fire_and_forget(self._emit_update())  # Paksa update UI langsung

    def _get_presence_state(self):
        if not self.state_manager:
            return "unknown"
        try:
            return self.state_manager.get_state("presence_state", user_id="system") or "unknown"
        except Exception:
            return "unknown"

    def _update_trading_metrics(self):
        if not self.db_manager:
            return
        now = time.time()
        if (now - self._last_metrics_ts) < self._metrics_interval_sec:
            return
        self._last_metrics_ts = now
        try:
            metrics = self.db_manager.get_trading_metrics()
            if metrics:
                with state_lock:
                    system_state["trading_metrics"].update(metrics)
        except Exception:
            return

