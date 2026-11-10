import threading
import logging
import time

from utils.backup import run_backup
from config.settings import Config

log = logging.getLogger("elara.backup_service")


class BackupService:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False

    def trigger_backup(self):
        if self._running:
            log.info("Backup already running, skipping.")
            return
        threading.Thread(target=self._run, daemon=True, name="BackupThread").start()

    def _run(self):
        with self._lock:
            self._running = True
            try:
                attempts = max(1, int(getattr(Config, "BACKUP_RETRY_MAX_ATTEMPTS", 3)))
                base_backoff = max(1.0, float(getattr(Config, "BACKUP_RETRY_BACKOFF_SEC", 3.0)))
                ok = False
                for attempt in range(1, attempts + 1):
                    ok = bool(run_backup())
                    if ok:
                        log.info("Backup completed.")
                        break
                    if attempt < attempts:
                        wait_sec = min(300.0, base_backoff * (2 ** (attempt - 1)))
                        log.warning(
                            "Backup attempt %s/%s failed. Retrying in %.1fs.",
                            attempt,
                            attempts,
                            wait_sec,
                        )
                        time.sleep(wait_sec)
                if not ok:
                    log.info("Backup skipped or failed.")
            finally:
                self._running = False

