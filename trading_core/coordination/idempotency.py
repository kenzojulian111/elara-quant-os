from __future__ import annotations

import time
from typing import Dict

from utils.redis_helper import get_redis


class IdempotencyCache:
    def __init__(self, ttl_sec: int = 600):
        self.ttl_sec = max(30, int(ttl_sec))
        self._memory: Dict[str, float] = {}

    def seen(self, key: str) -> bool:
        key = str(key or "").strip()
        if not key:
            return False
        now = time.time()
        redis_client = get_redis()
        if redis_client:
            try:
                cache_key = f"elara:idempotency:{key}"
                created = redis_client.set(cache_key, "1", nx=True, ex=self.ttl_sec)
                return not bool(created)
            except Exception:
                pass
        self._cleanup(now)
        if key in self._memory and self._memory[key] > now:
            return True
        self._memory[key] = now + self.ttl_sec
        return False

    def _cleanup(self, now: float) -> None:
        expired = [k for k, exp in self._memory.items() if exp <= now]
        for key in expired:
            self._memory.pop(key, None)

