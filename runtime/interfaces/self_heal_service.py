import logging
import threading
import time
from typing import Optional

from config.settings import Config
from utils.self_ops import SelfOpsManager

log = logging.getLogger("elara.self_heal")


class SelfHealService:
    def __init__(self, manager: Optional[SelfOpsManager] = None):
        self.manager = manager or SelfOpsManager()
        self.running = False
        self.thread = None
        self.interval = max(30, int(getattr(Config, "SELF_HEAL_INTERVAL_SEC", 120)))
        self.heal_now_min_interval_sec = max(1, int(getattr(Config, "SELF_HEAL_NOW_MIN_INTERVAL_SEC", 20)))
        self.heal_now_async_default = bool(getattr(Config, "SELF_HEAL_NOW_ASYNC_DEFAULT", True))
        self._last_error = ""
        self._last_heal_now_ts = 0.0
        self._heal_now_thread = None
        self._heal_now_lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self):
        if not getattr(Config, "SELF_HEAL_ENABLED", True):
            log.info("Self-heal disabled (SELF_HEAL_ENABLED=0).")
            return
        if self.thread and self.thread.is_alive():
            return
        self._stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="SelfHealService")
        self.thread.start()
        log.info(f"Self-heal started (interval={self.interval}s).")

    def stop(self):
        self.running = False
        self._stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def is_healthy(self):
        if not getattr(Config, "SELF_HEAL_ENABLED", True):
            return True
        if not self.running:
            return True
        return self.thread is not None and self.thread.is_alive()

    def _loop(self):
        while self.running:
            try:
                report = self.manager.heal_once()
                if report.get("warnings"):
                    log.warning(f"Self-heal warnings: {report['warnings']}")
            except Exception as e:
                self._last_error = str(e)
                log.warning(f"Self-heal loop error: {e}")
            if self._stop_event.wait(self.interval):
                break

    def _heal_now_worker(self):
        try:
            report = self.manager.heal_once()
            if report.get("warnings"):
                log.warning(f"Self-heal (manual) warnings: {report['warnings']}")
        except Exception as e:
            self._last_error = str(e)
            log.warning(f"Self-heal manual run failed: {e}")
        finally:
            with self._heal_now_lock:
                self._heal_now_thread = None

    def heal_now(self, wait: Optional[bool] = None):
        now = time.time()
        elapsed = now - float(self._last_heal_now_ts or 0.0)
        if elapsed < self.heal_now_min_interval_sec:
            return {
                "ok": False,
                "rate_limited": True,
                "retry_after_sec": int(self.heal_now_min_interval_sec - elapsed),
                "message": "heal_now rate limited",
            }

        self._last_heal_now_ts = now
        should_wait = self.heal_now_async_default if wait is None else bool(wait)
        if should_wait:
            return self.manager.heal_once()

        with self._heal_now_lock:
            if self._heal_now_thread and self._heal_now_thread.is_alive():
                return {
                    "ok": True,
                    "queued": True,
                    "started": False,
                    "message": "heal_now already running",
                }
            self._heal_now_thread = threading.Thread(
                target=self._heal_now_worker,
                daemon=True,
                name="SelfHealNow",
            )
            self._heal_now_thread.start()
        return {
            "ok": True,
            "queued": True,
            "started": True,
            "message": "heal_now scheduled",
        }

    def deploy_now(self, pull: bool = True, install: bool = False, restart: bool = False):
        return self.manager.deploy_in_background(pull=pull, install=install, restart=restart)

    def status(self):
        return {
            "running": self.running,
            "healthy": self.is_healthy(),
            "interval_sec": self.interval,
            "last_error": self._last_error,
            "last_heal_report": self.manager.last_heal_report,
            "last_config_drift": getattr(self.manager, "last_config_drift", {}),
            "last_deploy_report": self.manager.last_deploy_report,
            "deploy_running": self.manager.deploy_running(),
            "heal_now_running": bool(self._heal_now_thread and self._heal_now_thread.is_alive()),
        }

