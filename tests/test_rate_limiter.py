import time
import unittest
import os
import sys
import asyncio
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.rate_limiter import GlobalRateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_blocking_window_enforced(self):
        limiter = GlobalRateLimiter.get("test-rate-limiter", max_calls=2, period_sec=0.25)
        limiter.acquire_blocking()
        limiter.acquire_blocking()

        start = time.monotonic()
        limiter.acquire_blocking()
        elapsed = time.monotonic() - start

        self.assertGreaterEqual(elapsed, 0.20)

    def test_async_and_blocking_paths_share_single_critical_section(self):
        limiter = GlobalRateLimiter.get("test-rate-limiter-mixed", max_calls=1, period_sec=0.25)
        original_grant = limiter._grant

        def delayed_grant(now, weight):
            # Force an overlap window so race conditions become observable.
            time.sleep(0.05)
            original_grant(now, weight)

        setattr(limiter, "_grant", delayed_grant)

        t = threading.Thread(target=limiter.acquire_blocking, kwargs={"weight": 1}, daemon=True)
        start = time.monotonic()
        t.start()
        asyncio.run(limiter.acquire(weight=1))
        t.join(timeout=2.0)
        elapsed = time.monotonic() - start

        self.assertFalse(t.is_alive(), "blocking acquire should complete")
        self.assertGreaterEqual(elapsed, 0.20)


if __name__ == "__main__":
    unittest.main()

