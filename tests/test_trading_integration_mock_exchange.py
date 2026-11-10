import logging
import unittest

import pandas as pd

from skills.advanced.trading.data_manager import DataManager


def _make_frame(rows: int = 1200) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC")
    data = {
        "open": [100.0 + (i * 0.1) for i in range(rows)],
        "high": [100.5 + (i * 0.1) for i in range(rows)],
        "low": [99.5 + (i * 0.1) for i in range(rows)],
        "close": [100.2 + (i * 0.1) for i in range(rows)],
        "volume": [1000 + i for i in range(rows)],
    }
    return pd.DataFrame(data, index=idx)


class _MockConfig:
    STRATEGY = "swing"
    ASSET_SYMBOL = "BTC/USDT"
    EMA_TREND_PERIOD = 200
    ADX_PERIOD = 14
    STRATEGY_CONFIGS = {
        "swing": {
            "htf_timeframe": "1d",
            "ltf_timeframe": "1h",
            "analyzers_enabled": {"ml": True},
            "params": {
                "ema_short": 12,
                "ema_long": 26,
                "rsi": 14,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "atr": 14,
                "volume_ma": 20,
            },
        }
    }


class _MockExchange:
    def __init__(self):
        self.batch_called = False
        self.htf = _make_frame(300)
        self.ltf = _make_frame(1200)

    async def fetch_ohlcv_batch(self, requests, concurrency=2):
        self.batch_called = True
        return {"htf": self.htf, "ltf": self.ltf}


class _MockExchangeTimeout(_MockExchange):
    async def fetch_ohlcv_batch(self, requests, concurrency=2):
        self.batch_called = True
        raise TimeoutError("mock exchange timeout")


class _MockExchangeReject(_MockExchange):
    async def fetch_ohlcv_batch(self, requests, concurrency=2):
        self.batch_called = True
        # Simulate exchange returning reject/empty data payload.
        return {"htf": self.htf.iloc[0:0], "ltf": self.ltf}


class TradingIntegrationBatchFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_manager_uses_batch_fetch_and_generates_ml_features(self):
        manager = DataManager(_MockConfig(), logging.getLogger("test.data_manager"))
        exchange = _MockExchange()

        ok = await manager.load_all_data(exchange)

        self.assertTrue(ok)
        self.assertTrue(exchange.batch_called)
        self.assertIsNotNone(manager.htf_data)
        self.assertIsNotNone(manager.ltf_data)
        self.assertIsNotNone(manager.ml_features)
        self.assertGreater(len(manager.ml_features), 0)

    async def test_data_manager_handles_batch_timeout(self):
        manager = DataManager(_MockConfig(), logging.getLogger("test.data_manager.timeout"))
        exchange = _MockExchangeTimeout()

        ok = await manager.load_all_data(exchange)

        self.assertFalse(ok)
        self.assertTrue(exchange.batch_called)

    async def test_data_manager_handles_batch_reject_empty_htf(self):
        manager = DataManager(_MockConfig(), logging.getLogger("test.data_manager.reject"))
        exchange = _MockExchangeReject()

        ok = await manager.load_all_data(exchange)

        self.assertFalse(ok)
        self.assertTrue(exchange.batch_called)


if __name__ == "__main__":
    unittest.main()

