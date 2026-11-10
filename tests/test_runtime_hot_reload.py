import asyncio
import logging
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.bot import TradingBot
from skills.advanced.trading.config import TradingConfig


class _DummyDB:
    def __init__(self):
        self._runtime_settings = {}
        self.updated_rows = []
        self.trade_updates = []

    def load_open_positions(self):
        return []

    def log_equity(self, *_args, **_kwargs):
        return None

    def create_new_trade(self, _data):
        return 1

    def update_trade(self, trade_id, updates):
        self.updated_rows.append((trade_id, dict(updates)))
        return True

    def log_trade_update(self, payload):
        self.trade_updates.append(dict(payload))
        return True

    def get_all_closed_trades_with_pnl(self):
        return []

    def get_equity_curve_from_db(self):
        return []

    def get_runtime_settings(self, scope="trading"):
        if scope != "trading":
            return {}
        return dict(self._runtime_settings)

    def get_trades_with_open_exchange_orders(self, limit=200):
        return []


class RuntimeHotReloadTests(unittest.TestCase):
    def tearDown(self):
        # TradingBot logger installs file handlers; close them to avoid ResourceWarning in test runs.
        logger = logging.getLogger("TradingBot")
        for handler in list(logger.handlers):
            try:
                handler.close()
            except Exception:
                pass
            try:
                logger.removeHandler(handler)
            except Exception:
                pass

    def _build_bot(self, dbm):
        cfg = TradingConfig()
        cfg.MODE = "paper"
        cfg.TELEGRAM_ENABLED = False
        logger = logging.getLogger("test.runtime.hot_reload")
        logger.setLevel(logging.INFO)
        return TradingBot(cfg, dbm, life_manager=None, status_path=None, redis_client=None)

    def test_runtime_settings_apply_without_restart(self):
        dbm = _DummyDB()
        dbm._runtime_settings = {
            "daily_max_loss_pct": 3.5,
            "max_open_positions": 2,
            "rsi_buy_min": 50,
            "order_use_limit_chase": True,
        }
        bot = self._build_bot(dbm)
        bot._refresh_runtime_settings(force=True)

        self.assertEqual(float(bot.config.DAILY_MAX_LOSS_PCT), 3.5)
        self.assertEqual(int(bot.config.MAX_OPEN_POSITIONS), 2)
        self.assertEqual(float(bot.config.RSI_BUY_MIN), 50.0)
        self.assertTrue(bool(bot.config.ORDER_USE_LIMIT_CHASE))

    def test_open_order_sync_updates_trade_status(self):
        dbm = _DummyDB()
        bot = self._build_bot(dbm)
        bot.config.MODE = "live"
        bot._open_order_sync_interval_sec = 1

        def _rows(limit=200):
            return [
                {
                    "trade_id": 77,
                    "symbol": "BTC/USDT",
                    "exchange_order_id": "abc123",
                    "exchange_order_status": "open",
                }
            ][:limit]

        dbm.get_trades_with_open_exchange_orders = _rows

        async def _fake_fetch_order(symbol, order_id):
            self.assertEqual(symbol, "BTC/USDT")
            self.assertEqual(order_id, "abc123")
            return {"id": order_id, "status": "canceled"}

        bot.connector.fetch_order = _fake_fetch_order
        asyncio.run(bot._sync_open_orders_with_exchange(force=True))

        self.assertTrue(dbm.updated_rows)
        trade_id, updates = dbm.updated_rows[-1]
        self.assertEqual(trade_id, 77)
        self.assertEqual(updates.get("exchange_order_status"), "canceled")
        self.assertIsInstance(updates.get("exchange_order_last_sync"), datetime)
        self.assertTrue(dbm.trade_updates)
        self.assertEqual(dbm.trade_updates[-1].get("event_type"), "order_sync")

    def test_api_key_rotation_marks_reload_and_calls_connector_refresh(self):
        dbm = _DummyDB()
        dbm._runtime_settings = {
            "api_key": "rotated-key-123",
            "api_secret": "rotated-secret-456",
        }
        bot = self._build_bot(dbm)

        calls = {"update_key": None, "update_secret": None, "ensure_called": 0}

        def _fake_update_runtime_credentials(api_key=None, api_secret=None):
            if api_key:
                calls["update_key"] = api_key
            if api_secret:
                calls["update_secret"] = api_secret
            return True

        async def _fake_ensure_fresh_credentials():
            calls["ensure_called"] += 1
            return True

        bot.connector.update_runtime_credentials = _fake_update_runtime_credentials
        bot.connector.ensure_fresh_credentials = _fake_ensure_fresh_credentials

        bot._refresh_runtime_settings(force=True)

        self.assertTrue(bot._credential_reload_pending)
        self.assertEqual(calls["update_key"], "rotated-key-123")
        self.assertEqual(calls["update_secret"], "rotated-secret-456")
        self.assertTrue(str(bot._runtime_cfg_last_values.get("api_key", "")).startswith("sha256:"))
        self.assertTrue(str(bot._runtime_cfg_last_values.get("api_secret", "")).startswith("sha256:"))

        reloaded = asyncio.run(bot._maybe_reload_exchange_credentials())
        self.assertTrue(reloaded)
        self.assertEqual(calls["ensure_called"], 1)
        self.assertFalse(bot._credential_reload_pending)


if __name__ == "__main__":
    unittest.main()

