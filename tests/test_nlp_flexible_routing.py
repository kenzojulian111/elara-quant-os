import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.nlp_engine import ElaraNLP


class _FakeStateManager:
    def __init__(self):
        self._data = {}

    def set_state(self, key, value, user_id="default", ttl=None):
        _ = ttl
        self._data[(user_id, key)] = value

    def get_state(self, key, user_id="default"):
        return self._data.get((user_id, key))

    def delete_state(self, key, user_id="default"):
        self._data.pop((user_id, key), None)


class NLPFlexibleRoutingTests(unittest.TestCase):
    def setUp(self):
        self.nlp = ElaraNLP(state_manager=_FakeStateManager(), providers=["groq"])
        self.nlp.groq_client = None
        self.nlp.gemini_model = None
        self.nlp.openai_client = None

    def test_flexible_phrase_routes_weather_without_llm(self):
        nlu = asyncio.run(
            self.nlp.understand(
                "tolong dong cek cuaca di bandung sekarang",
                user_id="u1",
            )
        )[0]
        self.assertEqual(nlu.intent, "get_weather")
        self.assertEqual(nlu.source, "keyword_flexible")
        self.assertGreaterEqual(nlu.confidence, 0.85)

    def test_flexible_phrase_routes_trading_status(self):
        nlu = asyncio.run(
            self.nlp.understand(
                "bro bisa cekin status trading sekarang?",
                user_id="u2",
            )
        )[0]
        self.assertEqual(nlu.intent, "trading_status")
        self.assertEqual(nlu.source, "keyword_flexible")

    def test_flexible_phrase_routes_stock_screener(self):
        nlu = asyncio.run(
            self.nlp.understand(
                "tolong cari saham yang momentum nya bagus",
                user_id="u3",
            )
        )[0]
        self.assertEqual(nlu.intent, "screen_stocks")
        self.assertEqual(nlu.source, "keyword_flexible")


if __name__ == "__main__":
    unittest.main()

