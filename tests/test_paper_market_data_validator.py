import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState
from trading_core.orchestration.paper_runner import PaperMarketDataValidator


class PaperMarketDataValidatorTests(unittest.TestCase):
    def _state(self, *, ts: datetime, **overrides) -> MarketState:
        payload = {
            "ts": ts,
            "symbol": "BTC/USDT",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 10.0,
        }
        payload.update(overrides)
        return MarketState(**payload)

    def test_accepts_ordered_valid_states(self):
        validator = PaperMarketDataValidator(max_gap_sec=120)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertIsNone(validator.validate(self._state(ts=start)))
        self.assertIsNone(validator.validate(self._state(ts=start + timedelta(seconds=60))))

    def test_rejects_non_monotonic_and_stale_market_data(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        validator = PaperMarketDataValidator(max_gap_sec=60)
        self.assertIsNone(validator.validate(self._state(ts=start)))
        self.assertEqual(
            "market_timestamp_not_monotonic",
            validator.validate(self._state(ts=start)),
        )

        validator = PaperMarketDataValidator(max_gap_sec=60)
        self.assertIsNone(validator.validate(self._state(ts=start)))
        self.assertEqual(
            "market_data_stale_gap=61.0",
            validator.validate(self._state(ts=start + timedelta(seconds=61))),
        )


if __name__ == "__main__":
    unittest.main()
