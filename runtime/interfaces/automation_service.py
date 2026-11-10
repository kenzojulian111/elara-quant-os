import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

from runtime.interfaces.mqtt_service import system_state, state_lock
from utils.common import async_runner
from config.settings import Config

log = logging.getLogger("elara.automation")


class AutomationService:
    def __init__(self, elara_skill, mqtt_service):
        self.elara = elara_skill
        self.mqtt = mqtt_service
        self.running = True
        self._stop_event = threading.Event()
        self._last_ac_turbo_ts = 0.0
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(Config, "AUTOMATION_THREAD_POOL_WORKERS", 1))),
            thread_name_prefix="AutomationSvc",
        )
        self._future = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._future and not self._future.done():
                return
            self.running = True
            self._stop_event.clear()
            self._future = self._pool.submit(self._loop)
        log.info("Automation Engine Started.")

    def stop(self):
        self.running = False
        self._stop_event.set()
        fut = self._future
        if fut:
            try:
                fut.result(timeout=3)
            except Exception:
                pass
        self._future = None

    def shutdown(self):
        self.stop()
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def _loop(self):
        base_interval = max(1.0, float(getattr(Config, "AUTOMATION_POLL_SEC", 5.0)))
        if getattr(Config, "ECO_MODE", False):
            base_interval = base_interval * max(1.0, float(getattr(Config, "ECO_POLL_MULTIPLIER", 2.0)))
        ac_cooldown_sec = max(60, int(getattr(Config, "AUTOMATION_AC_TURBO_COOLDOWN_SEC", 300)))
        error_streak = 0

        while self.running:
            try:
                with state_lock:
                    metrics = dict(system_state.get("trading_metrics", {}))
                    pnl = metrics.get("total_net_pnl", 0)

                if pnl > 100:
                    # Placeholder hook for future automation actions.
                    pass

                with state_lock:
                    suhu = system_state.get("suhu", 0)
                if suhu > 33.0:
                    now = time.time()
                    if (now - self._last_ac_turbo_ts) >= ac_cooldown_sec:
                        log.info("Heat rule triggered. Sending AC turbo command.")
                        async_runner.run_coroutine_fire_and_forget(
                            self.elara.handle_command("nyalakan ac ruang_tamu suhu 18", "auto_system")
                        )
                        self._last_ac_turbo_ts = now

                error_streak = 0
                if self._stop_event.wait(base_interval):
                    break
            except Exception as e:
                error_streak += 1
                wait_sec = min(60.0, base_interval * (2 ** min(error_streak, 6)))
                log.error(f"Auto Error (streak={error_streak}, retry_in={wait_sec:.1f}s): {e}")
                if self._stop_event.wait(wait_sec):
                    break

