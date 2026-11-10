from __future__ import annotations

import hashlib
import time
import logging


class Notifier:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.log = logger
        self._throttle_window_sec = max(
            1, int(getattr(cfg, "NOTIFIER_THROTTLE_WINDOW_SEC", 60) or 60)
        )
        self._throttle_burst = max(1, int(getattr(cfg, "NOTIFIER_THROTTLE_BURST", 5) or 5))
        self._throttle_max_keys = max(
            1, int(getattr(cfg, "NOTIFIER_THROTTLE_MAX_KEYS", 10) or 10)
        )
        self._throttle_state: dict[str, list[float]] = {}

    def _throttle_decision(self, message: str) -> bool:
        now = time.time()
        key = hashlib.sha1(str(message or "").encode("utf-8")).hexdigest()[:16]
        rows = [
            ts for ts in self._throttle_state.get(key, []) if (now - ts) <= self._throttle_window_sec
        ]
        allowed = len(rows) < self._throttle_burst
        if allowed:
            rows.append(now)
        self._throttle_state[key] = rows

        if len(self._throttle_state) > self._throttle_max_keys:
            ordered = sorted(
                self._throttle_state.items(),
                key=lambda item: item[1][-1] if item[1] else 0.0,
            )
            while len(ordered) > self._throttle_max_keys:
                old_key, _rows = ordered.pop(0)
                self._throttle_state.pop(old_key, None)
        return allowed


def setup_logger(cfg=None, name: str = "elara.backtest"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

