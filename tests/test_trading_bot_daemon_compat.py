import asyncio
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.bot import TradingBot
from skills.advanced.trading.config import TradingConfig
from config.settings import Config


class _DummyDB:
    def __init__(self):
        self.runtime_settings = {}
        self.trades = []
        self.execution_claims = {}
        self.execution_statuses = []

    def get_runtime_settings(self, scope="trading"):
        _ = scope
        return {}

    def get_runtime_setting(self, key, default=None, scope="trading"):
        return self.runtime_settings.get((scope, key), default)

    def set_runtime_setting(self, key, value, scope="trading", updated_by="system"):
        _ = updated_by
        self.runtime_settings[(scope, key)] = value
        return True

    def get_trading_metrics(self):
        return {
            "equity": 1000,
            "cash": 1000,
            "open_positions": 0,
        }

    def get_trades_with_open_exchange_orders(self, limit=200):
        _ = limit
        return []

    def create_new_trade(self, trade_data):
        self.trades.append(dict(trade_data))
        return len(self.trades)

    def log_trade_update(self, update_data):
        return None

    def claim_trade_execution(
        self,
        execution_id,
        *,
        command_id="",
        decision_id="",
        client_order_id="",
        cmd="",
        payload=None,
    ):
        key = str(execution_id or "").strip() or f"client_order_id:{client_order_id}"
        client_key = str(client_order_id or "").strip()
        if key in self.execution_claims:
            return False
        if client_key and any(row.get("client_order_id") == client_key for row in self.execution_claims.values()):
            return False
        self.execution_claims[key] = {
            "execution_id": key,
            "command_id": command_id,
            "decision_id": decision_id,
            "client_order_id": client_key,
            "cmd": cmd,
            "payload": dict(payload or {}),
        }
        return True

    def mark_trade_execution_status(self, execution_id, status, *, payload=None):
        self.execution_statuses.append(
            {
                "execution_id": execution_id,
                "status": status,
                "payload": dict(payload or {}),
            }
        )
        return True


class _FakeExchange:
    def __init__(self):
        self.orders = []
        self.markets = {}

    async def create_limit_order(self, symbol, side, amount, price):
        self.orders.append((symbol, side, amount, price))
        return {"id": f"oid-{len(self.orders)}", "status": "open"}


class TradingBotDaemonCompatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._runtime_event_store_enabled = getattr(Config, "RUNTIME_EVENT_STORE_ENABLED", True)
        Config.RUNTIME_EVENT_STORE_ENABLED = False

    def tearDown(self):
        Config.RUNTIME_EVENT_STORE_ENABLED = self._runtime_event_store_enabled

    async def test_setup_and_run_live_publish_status_file(self):
        cfg = TradingConfig()
        cfg.MODE = "paper"

        with tempfile.TemporaryDirectory(prefix="elara_bot_status_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            bot = TradingBot(cfg, _DummyDB(), status_path=status_path, redis_client=None)
            bot._status_interval_sec = 0.05

            ok = await bot.setup()
            self.assertTrue(ok)

            task = asyncio.create_task(bot.run_live())
            await asyncio.sleep(0.12)

            self.assertTrue(os.path.exists(status_path))
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertTrue(bool(payload.get("is_running")))
            self.assertTrue(bool(payload.get("simulation_fallback")))
            self.assertEqual(str(payload.get("setup_stage") or ""), "running")

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            await bot.shutdown()
            with open(status_path, "r", encoding="utf-8") as f:
                stopped = json.load(f)
            self.assertFalse(bool(stopped.get("is_running")))

    async def test_live_mode_requires_credentials(self):
        cfg = TradingConfig()
        cfg.MODE = "live"
        cfg.API_KEY = ""
        cfg.API_SECRET = ""

        with tempfile.TemporaryDirectory(prefix="elara_bot_live_status_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            bot = TradingBot(cfg, _DummyDB(), status_path=status_path, redis_client=None)

            ok = await bot.setup()
            self.assertFalse(ok)
            self.assertIn("live mode requires", bot.last_setup_error)

    async def test_process_command_updates_control_state(self):
        cfg = TradingConfig()
        cfg.MODE = "paper"

        with tempfile.TemporaryDirectory(prefix="elara_bot_command_status_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            bot = TradingBot(cfg, _DummyDB(), status_path=status_path, redis_client=None)

            self.assertTrue(await bot.setup())
            self.assertTrue(await bot.process_command({"cmd": "stop", "reason": "manual_stop"}))
            self.assertEqual(bot._control_state, "blocked")

            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload.get("control_state"), "blocked")
            self.assertEqual(payload.get("last_command"), "stop")

            self.assertTrue(await bot.process_command({"cmd": "resume"}))
            self.assertEqual(bot._control_state, "active")

            with open(status_path, "r", encoding="utf-8") as f:
                resumed = json.load(f)
            self.assertEqual(resumed.get("control_state"), "active")
            self.assertEqual(resumed.get("last_command"), "resume")

    async def test_limit_order_command_is_idempotent_by_execution_id(self):
        cfg = TradingConfig()
        cfg.MODE = "live"

        with tempfile.TemporaryDirectory(prefix="elara_bot_limit_order_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            db = _DummyDB()
            bot = TradingBot(cfg, db, status_path=status_path, redis_client=None)
            fake_exchange = _FakeExchange()
            bot.connector.async_exchange = fake_exchange
            bot.simulation_fallback = False

            command = {
                "cmd": "limit_order",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": "0.1",
                "price": "42000",
                "execution_id": "exec-123",
            }

            self.assertTrue(await bot.process_command(command))
            self.assertTrue(await bot.process_command(command))

            self.assertEqual(len(fake_exchange.orders), 1)
            self.assertEqual(len(db.trades), 1)
            self.assertEqual(len(db.execution_claims), 1)
            self.assertEqual(db.execution_statuses[-1]["status"], "submitted")

    async def test_limit_order_command_without_ids_gets_stable_execution_journal_claim(self):
        cfg = TradingConfig()
        cfg.MODE = "paper"

        with tempfile.TemporaryDirectory(prefix="elara_bot_limit_order_ids_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            db = _DummyDB()
            bot = TradingBot(cfg, db, status_path=status_path, redis_client=None)

            command = {
                "cmd": "limit_order",
                "symbol": "ETH/USDT",
                "side": "sell",
                "amount": "0.2",
                "price": "3100",
            }

            self.assertTrue(await bot.process_command(command))
            self.assertTrue(await bot.process_command(command))

            self.assertEqual(len(db.execution_claims), 1)
            claim = next(iter(db.execution_claims.values()))
            self.assertTrue(claim["execution_id"].startswith("exec-"))
            self.assertTrue(claim["client_order_id"].startswith("elara-"))
            self.assertEqual(db.execution_statuses[-1]["status"], "simulated")


if __name__ == "__main__":
    unittest.main()

