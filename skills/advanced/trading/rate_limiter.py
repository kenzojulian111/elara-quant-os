import asyncio
import threading
import time
from collections import deque
from typing import Dict


class GlobalRateLimiter:
    """Process-wide sliding-window rate limiter."""

    _instances: Dict[str, "GlobalRateLimiter"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, max_calls: int, period_sec: float):
        self.max_calls = max(1, int(max_calls))
        self.period_sec = max(1.0, float(period_sec))
        self._timestamps = deque()
        self._thread_lock = threading.Lock()

    @classmethod
    def get(cls, name: str, max_calls: int, period_sec: float) -> "GlobalRateLimiter":
        key = str(name or "default")
        with cls._instances_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls(max_calls=max_calls, period_sec=period_sec)
                cls._instances[key] = inst
            else:
                inst.max_calls = max(1, int(max_calls))
                inst.period_sec = max(1.0, float(period_sec))
            return inst

    def _drain(self, now: float):
        cutoff = now - self.period_sec
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def _wait_time(self, now: float) -> float:
        if len(self._timestamps) < self.max_calls:
            return 0.0
        oldest = self._timestamps[0]
        return max(0.0, (oldest + self.period_sec) - now)

    def _grant(self, now: float, weight: int):
        for _ in range(weight):
            self._timestamps.append(now)

    async def acquire(self, weight: int = 1):
        need = max(1, int(weight))
        while True:
            with self._thread_lock:
                now = time.monotonic()
                self._drain(now)
                if len(self._timestamps) + need <= self.max_calls:
                    self._grant(now, need)
                    return
                wait_sec = self._wait_time(now)
            await asyncio.sleep(max(0.001, wait_sec))

    def acquire_blocking(self, weight: int = 1):
        need = max(1, int(weight))
        while True:
            with self._thread_lock:
                now = time.monotonic()
                self._drain(now)
                if len(self._timestamps) + need <= self.max_calls:
                    self._grant(now, need)
                    return
                wait_sec = self._wait_time(now)
            time.sleep(max(0.001, wait_sec))

