import os
import sys
import unittest

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.openbb_adapter import _invoke_historical


class OpenBBAdapterInvocationTests(unittest.TestCase):
    def test_invoke_historical_supports_positional_symbol_fallback(self):
        calls = []

        def fake_provider(symbol, start_date=None, interval=None):
            calls.append((symbol, start_date, interval))
            return pd.DataFrame(
                {
                    "date": ["2026-01-01", "2026-01-02"],
                    "open": [10.0, 11.0],
                    "high": [11.0, 12.0],
                    "low": [9.0, 10.0],
                    "close": [10.5, 11.5],
                    "volume": [100, 120],
                }
            )

        attempts = [
            {"symbol": "AAPL", "start_date": "2025-12-01", "interval": "1d"},
        ]
        frame = _invoke_historical(fake_provider, "AAPL", attempts)

        self.assertIsNotNone(frame)
        self.assertFalse(frame.empty)
        self.assertEqual(1, len(calls))
        self.assertEqual(("AAPL", "2025-12-01", "1d"), calls[0])

    def test_invoke_historical_positional_fallback_drops_alt_symbol_keys(self):
        calls = []

        def fake_provider(symbol, start_date=None):
            calls.append((symbol, start_date))
            return {"results": [{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]}

        attempts = [
            {"ticker": "MSFT", "start_date": "2025-12-01"},
            {"tickers": "MSFT", "start_date": "2025-12-02"},
        ]

        frame = _invoke_historical(fake_provider, "MSFT", attempts)

        self.assertIsNotNone(frame)
        self.assertFalse(frame.empty)
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual("MSFT", calls[0][0])


if __name__ == "__main__":
    unittest.main()

