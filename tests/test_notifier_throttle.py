import logging
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.utils import Notifier


class _Cfg:
    TELEGRAM_ENABLED = False
    TELEGRAM_TOKEN = ""
    TELEGRAM_CHAT_ID = ""
    NOTIFIER_TEXT_CHUNK_SIZE = 3900
    NOTIFIER_THROTTLE_WINDOW_SEC = 60
    NOTIFIER_THROTTLE_BURST = 5
    NOTIFIER_THROTTLE_MAX_KEYS = 10
    NOTIFIER_THROTTLE_CRITICAL_BYPASS = False
    NOTIFIER_THROTTLE_CRITICAL_KEYWORDS = []


class NotifierThrottleTests(unittest.TestCase):
    def test_throttle_state_is_bounded(self):
        notifier = Notifier(_Cfg(), logging.getLogger("test.notifier.throttle"))
        for i in range(200):
            notifier._throttle_decision(f"message-{i}")
        self.assertLessEqual(len(notifier._throttle_state), notifier._throttle_max_keys + 1)
        self.assertLess(len(notifier._throttle_state), 200)


if __name__ == "__main__":
    unittest.main()

