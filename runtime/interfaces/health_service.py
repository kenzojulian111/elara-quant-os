import json
import threading
import time
import logging
import os

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None

from config.settings import Config

log = logging.getLogger("elara.health_service")


class HealthService:
    """
    Polls a JSON source (file or HTTP) and updates LifeManager.
    Uses bounded timeout + retry and a stop event to avoid hanging threads.
    """

    def __init__(self, life_manager):
        self.life_manager = life_manager
        self.path = getattr(Config, "HEALTH_POLL_PATH", "")
        self.interval = max(60, int(getattr(Config, "HEALTH_POLL_INTERVAL", 300)))
        self.http_timeout_sec = max(1.0, float(getattr(Config, "HEALTH_HTTP_TIMEOUT_SEC", 5.0)))
        self.running = False
        self.thread = None
        self._stop_event = threading.Event()
        self._session = requests.Session()
        if Retry is not None:
            retry = Retry(
                total=2,
                connect=2,
                read=2,
                backoff_factor=0.3,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset(["GET"]),
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

    def start(self):
        if not self.path:
            log.info("HealthService disabled (HEALTH_POLL_PATH empty).")
            return
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="HealthService")
        self.thread.start()
        log.info(f"HealthService polling {self.path} every {self.interval}s.")

    def stop(self):
        self.running = False
        self._stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def _fetch_data(self):
        data = None
        if self.path.startswith("http"):
            token = getattr(Config, "HEALTH_API_TOKEN", "")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            try:
                resp = self._session.get(self.path, headers=headers, timeout=self.http_timeout_sec)
                if resp.ok:
                    data = resp.json()
            except RequestException as e:
                raise RuntimeError(f"HealthService HTTP request failed: {e}") from e
        elif os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        return data

    def _loop(self):
        error_streak = 0
        while self.running:
            try:
                data = self._fetch_data()
                if data:
                    self.life_manager.update(
                        sleep_hours=data.get("sleep_hours"),
                        energy=data.get("energy"),
                        focus_minutes=data.get("focus_minutes"),
                        stress=data.get("stress"),
                        user_id="default"
                    )
                error_streak = 0
                if self._stop_event.wait(self.interval):
                    break
            except Exception as e:
                error_streak += 1
                wait_sec = min(float(self.interval), float(5 * (2 ** min(error_streak, 6))))
                log.warning(f"HealthService error (streak={error_streak}, retry_in={wait_sec:.1f}s): {e}")
                if self._stop_event.wait(wait_sec):
                    break

