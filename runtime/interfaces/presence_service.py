import logging
import threading
import time
import asyncio
import random
import socket
import struct

from config.settings import Config
from utils.common import async_runner

log = logging.getLogger("elara.presence")


class PresenceService:
    def __init__(self, elara_skill, state_manager):
        self.elara = elara_skill
        self.state_manager = state_manager
        self.running = False
        self._presence = None
        self._last_seen = None
        self._last_change = None
        self._thread = None
        self._presence_lock = threading.Lock()

    def start(self):
        if not Config.PRESENCE_ENABLED:
            log.info("Presence detection disabled (PRESENCE_ENABLED=0).")
            return
        if self._thread and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PresenceThread")
        self._thread.start()
        log.info("Presence detection started.")

    def stop(self):
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _get_ips(self):
        raw = (Config.PRESENCE_IPS or "").strip()
        if not raw:
            return []
        return [ip.strip() for ip in raw.split(",") if ip.strip()]

    def _get_commands(self, raw):
        if not raw:
            return []
        return [c.strip() for c in raw.split(";") if c.strip()]

    @staticmethod
    def _icmp_checksum(data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        total = 0
        for i in range(0, len(data), 2):
            total += (data[i] << 8) + data[i + 1]
            total = (total & 0xFFFF) + (total >> 16)
        return (~total) & 0xFFFF

    @staticmethod
    def _parse_icmp_header(packet: bytes):
        if not packet or len(packet) < 8:
            return None
        start = 0
        # Handle raw IPv4 packets that include the IP header.
        if len(packet) >= 20:
            version = packet[0] >> 4
            ihl = (packet[0] & 0x0F) * 4
            if version == 4 and ihl >= 20 and len(packet) >= (ihl + 8):
                start = ihl
        if len(packet) < (start + 8):
            return None
        return struct.unpack("!BBHHH", packet[start:start + 8])

    def _ping_icmp_native(self, ip: str, timeout_sec: float) -> bool:
        proto = socket.getprotobyname("icmp")
        identifier = random.randint(0, 0xFFFF)
        sequence = random.randint(0, 0xFFFF)
        payload = struct.pack("!d", time.time()) + b"elara-presence"
        header = struct.pack("!BBHHH", 8, 0, 0, identifier, sequence)
        checksum = self._icmp_checksum(header + payload)
        packet = struct.pack("!BBHHH", 8, 0, checksum, identifier, sequence) + payload

        for sock_type in (socket.SOCK_DGRAM, socket.SOCK_RAW):
            try:
                with socket.socket(socket.AF_INET, sock_type, proto) as sock:
                    sock.settimeout(timeout_sec)
                    sock.sendto(packet, (ip, 1))
                    deadline = time.time() + timeout_sec
                    while time.time() < deadline:
                        remain = max(0.05, deadline - time.time())
                        sock.settimeout(remain)
                        try:
                            recv_packet, addr = sock.recvfrom(1024)
                        except socket.timeout:
                            break
                        if addr and str(addr[0]) != str(ip):
                            continue
                        parsed = self._parse_icmp_header(recv_packet)
                        if not parsed:
                            continue
                        icmp_type, _code, _csum, recv_id, recv_seq = parsed
                        if icmp_type != 0:
                            continue
                        if recv_seq != sequence:
                            continue
                        if recv_id not in {identifier, 0}:
                            continue
                        return True
            except PermissionError:
                continue
            except OSError:
                continue
        return False

    def _ping(self, ip: str) -> bool:
        timeout_sec = max(1.0, float(getattr(Config, "PRESENCE_PING_TIMEOUT_SEC", 2.5)))
        try:
            return self._ping_icmp_native(ip, timeout_sec)
        except Exception:
            return False

    async def _ping_async(self, ip: str, sem: asyncio.Semaphore) -> bool:
        async with sem:
            return await asyncio.to_thread(self._ping, ip)

    async def _probe_any_online(self, ips):
        max_conc = max(1, int(getattr(Config, "PRESENCE_PING_CONCURRENCY", 8)))
        sem = asyncio.Semaphore(max_conc)
        tasks = [self._ping_async(ip, sem) for ip in ips]
        if not tasks:
            return False
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for item in results:
            if item is True:
                return True
        return False

    def _set_presence(self, presence: str):
        with self._presence_lock:
            if presence == self._presence:
                return
            now = time.time()
            cooldown = max(0, int(Config.PRESENCE_COOLDOWN_SEC))
            if self._last_change and (now - self._last_change) < cooldown:
                log.info("Presence change ignored due to cooldown.")
                return
            self._presence = presence
            self._last_change = now
        try:
            self.state_manager.set_state("presence_state", presence, user_id="system")
        except Exception as e:
            log.warning(f"Failed to update presence state: {e}")
        self._trigger_presence_actions(presence)

    def _trigger_presence_actions(self, presence: str):
        if presence == "home":
            commands = self._get_commands(Config.PRESENCE_ONLINE_COMMANDS)
            label = "HOME"
        else:
            commands = self._get_commands(Config.PRESENCE_OFFLINE_COMMANDS)
            label = "AWAY"
        if not commands:
            log.info(f"Presence {label} detected, no actions configured.")
            return
        for cmd in commands:
            log.info(f"Presence {label} -> command: {cmd}")
            async_runner.run_coroutine_fire_and_forget(
                self.elara.handle_command(cmd, "presence")
            )

    def _loop(self):
        ips = self._get_ips()
        if not ips:
            log.warning("Presence enabled but PRESENCE_IPS kosong.")
            return

        interval = max(2, int(Config.PRESENCE_INTERVAL_SEC))
        if getattr(Config, "ECO_MODE", False):
            interval = int(interval * max(1.0, float(getattr(Config, "ECO_POLL_MULTIPLIER", 2.0))))
        away_after = max(30, int(Config.PRESENCE_AWAY_AFTER_SEC))

        while self.running:
            any_online = False
            try:
                any_online = bool(async_runner.run_coroutine(self._probe_any_online(ips)))
            except Exception as e:
                log.warning(f"Presence async probe failed, fallback sequential ping: {e}")
                for ip in ips:
                    if self._ping(ip):
                        any_online = True
                        break
            if any_online:
                with self._presence_lock:
                    self._last_seen = time.time()

            now = time.time()
            if any_online:
                self._set_presence("home")
            else:
                with self._presence_lock:
                    last_seen = self._last_seen
                if last_seen and (now - last_seen) < away_after:
                    pass
                else:
                    self._set_presence("away")

            time.sleep(interval)

