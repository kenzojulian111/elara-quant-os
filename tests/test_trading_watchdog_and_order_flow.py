import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from skills.advanced.trading.daemon_runner import _core_heartbeat_watchdog
from skills.advanced.trading.daemon_runner import _pop_commands
from skills.advanced.trading.daemon_runner import _resolve_watchdog_heartbeat_key
from skills.advanced.trading.daemon_runner import _resolve_watchdog_heartbeat_path
from skills.advanced.trading.daemon_runner import _write_startup_status
from skills.advanced.trading.exchange import ExchangeConnector
from core.events import build_trade_command_message
from core.events import core_heartbeat_key
from utils.redis_helper import get_redis


class _FakeRedis:
    def __init__(self, payload):
        self.payload = payload

    def get(self, _key):
        return self.payload


class _FakeBot:
    def __init__(self):
        self._running = True
        self._control_state = "active"
        self._control_reason = ""
        self.commands = []

    async def process_command(self, command):
        self.commands.append(dict(command))
        cmd = str(command.get("cmd") or "").strip().lower()
        reason = str(command.get("reason") or "").strip()
        if cmd in {"stop", "block", "flatten"}:
            self._control_state = "blocked"
            self._control_reason = reason or cmd
        elif cmd == "resume":
            self._control_state = "active"
            self._control_reason = ""
        return True


class _QueueRedis:
    def __init__(self):
        self._list = []

    def lpush(self, _key, value):
        self._list.insert(0, value)
        return len(self._list)

    def rpop(self, _key):
        if not self._list:
            return None
        return self._list.pop()


class _OrderCfg:
    MODE = "live"
    EXCHANGE_NAME = "binance"
    SECONDARY_EXCHANGE_NAME = ""
    EXCHANGE_FALLBACKS = []
    EXCHANGE_FORCE_SPOT_MARKETS = True
    PAPER_ALLOW_YFINANCE_FALLBACK = False
    PAPER_USE_SANDBOX = False
    EXCHANGE_TIMEOUT_MS = 3000
    EXCHANGE_RATE_LIMIT_MAX_CALLS = 1100
    EXCHANGE_RATE_LIMIT_PERIOD_SEC = 60
    EXCHANGE_API_ERROR_THRESHOLD = 3
    EXCHANGE_API_PAUSE_SEC = 3600
    EXCHANGE_ORDER_FETCH_RETRIES = 2
    EXCHANGE_ORDER_RETRY_BASE_SEC = 0.2
    EXCHANGE_ORDER_RETRY_MAX_SEC = 1.0
    ORDER_USE_LIMIT_CHASE = True
    ORDER_CHASE_TIMEOUT_SEC = 3
    ORDER_CHASE_MAX_RETRIES = 1
    ORDER_CHASE_POLL_SEC = 0.3
    EXCHANGE_MAX_TIME_DRIFT_MS = 2000
    WS_SEQUENCE_CHECK_ENABLED = True
    WS_SEQUENCE_MAX_JUMP = 50
    DELIST_STATUS_RISK_FLAGS = ["delist", "halt", "suspend", "closed"]
    API_KEY = ""
    API_SECRET = ""
    TAKER_FEE_PERCENT = Decimal("0.1")


class _FakeExchange:
    def __init__(self):
        self.cancel_calls = []
        self.limit_order_calls = []
        self.fetch_order_calls = 0

    @staticmethod
    def amount_to_precision(_symbol, amount):
        return f"{float(amount):.6f}"

    @staticmethod
    def price_to_precision(_symbol, price):
        return f"{float(price):.2f}"

    async def fetch_order_book(self, _symbol, limit=5):
        _ = limit
        return {"asks": [[100.0, 5.0]], "bids": [[99.5, 4.0]]}

    async def fetch_ticker(self, _symbol):
        return {"ask": 100.0, "bid": 99.5, "last": 99.8}

    async def create_limit_order(self, symbol, side, amount, price):
        self.limit_order_calls.append((symbol, side, amount, price))
        return {"id": "oid-1", "status": "open", "amount": amount, "price": price}

    async def fetch_order(self, order_id, symbol):
        self.fetch_order_calls += 1
        return {"id": order_id, "symbol": symbol, "status": "open", "filled": 0}

    async def cancel_order(self, order_id, symbol):
        self.cancel_calls.append((order_id, symbol))
        return {"id": order_id, "symbol": symbol, "status": "canceled"}


class _BalanceExchange:
    async def fetch_balance(self):
        return {
            "free": {"USDT": "120.5", "BTC": "0.25"},
            "used": {"USDT": "1.5"},
            "total": {"USDT": "122.0", "BTC": "0.25"},
        }


class TradingWatchdogAndOrderFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._cfg_backup = {}
        patch_values = {
            "TRADER_REQUIRE_CORE_HEARTBEAT": True,
            "TRADER_CORE_HEARTBEAT_STALE_SEC": 5,
            "TRADER_CORE_HEARTBEAT_CHECK_SEC": 2,
            "TRADER_CORE_HEARTBEAT_GRACE_SEC": 5,
            "CORE_HEARTBEAT_KEY": "elara:test:core_heartbeat",
            "CORE_HEARTBEAT_PATH": "",
        }
        for key, value in patch_values.items():
            self._cfg_backup[key] = getattr(Config, key)
            setattr(Config, key, value)

    def tearDown(self):
        for key, value in self._cfg_backup.items():
            setattr(Config, key, value)

    async def test_pop_commands_from_redis_preserves_fifo_order(self):
        redis_client = _QueueRedis()
        queue_key = "elara:test:trade_command_queue"
        self._cfg_backup["TRADING_COMMAND_KEY"] = getattr(Config, "TRADING_COMMAND_KEY")
        self._cfg_backup["TRADING_COMMAND_POP_MAX_BATCH"] = getattr(
            Config, "TRADING_COMMAND_POP_MAX_BATCH", 1000
        )
        Config.TRADING_COMMAND_KEY = queue_key
        Config.TRADING_COMMAND_POP_MAX_BATCH = 10

        msg1 = build_trade_command_message("stop", "test", reason="r1")
        msg2 = build_trade_command_message("resume", "test", reason="r2")
        redis_client.lpush(queue_key, json.dumps(msg1))
        redis_client.lpush(queue_key, json.dumps(msg2))

        cmds = _pop_commands("", redis_client, "elara:")
        self.assertEqual([item["cmd"] for item in cmds], ["stop", "resume"])
        self.assertEqual(cmds[0]["reason"], "r1")

    async def test_pop_commands_respects_batch_limit(self):
        redis_client = _QueueRedis()
        queue_key = "elara:test:trade_command_queue"
        self._cfg_backup["TRADING_COMMAND_KEY"] = getattr(Config, "TRADING_COMMAND_KEY")
        self._cfg_backup["TRADING_COMMAND_POP_MAX_BATCH"] = getattr(
            Config, "TRADING_COMMAND_POP_MAX_BATCH", 1000
        )
        Config.TRADING_COMMAND_KEY = queue_key
        Config.TRADING_COMMAND_POP_MAX_BATCH = 1

        redis_client.lpush(queue_key, json.dumps(build_trade_command_message("stop", "test")))
        redis_client.lpush(queue_key, json.dumps(build_trade_command_message("resume", "test")))

        first = _pop_commands("", redis_client, "elara:")
        second = _pop_commands("", redis_client, "elara:")
        self.assertEqual([item["cmd"] for item in first], ["stop"])
        self.assertEqual([item["cmd"] for item in second], ["resume"])

    async def test_pop_commands_preserves_limit_order_execution_identity(self):
        redis_client = _QueueRedis()
        queue_key = "elara:test:trade_command_queue"
        self._cfg_backup["TRADING_COMMAND_KEY"] = getattr(Config, "TRADING_COMMAND_KEY")
        self._cfg_backup["TRADING_COMMAND_POP_MAX_BATCH"] = getattr(
            Config, "TRADING_COMMAND_POP_MAX_BATCH", 1000
        )
        Config.TRADING_COMMAND_KEY = queue_key
        Config.TRADING_COMMAND_POP_MAX_BATCH = 10

        msg = {
            "event": "trade.command.v1",
            "source": "test",
            "ts": time.time(),
            "version": 1,
            "payload": {
                "cmd": "limit_order",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.1,
                "price": 42000,
                "command_id": "cmd-1",
                "execution_id": "exec-1",
            },
        }
        redis_client.lpush(queue_key, json.dumps(msg))

        commands = _pop_commands("", redis_client, "elara:")

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["cmd"], "limit_order")
        self.assertEqual(commands[0]["symbol"], "BTC/USDT")
        self.assertEqual(commands[0]["execution_id"], "exec-1")

    async def test_core_heartbeat_watchdog_blocks_entries_when_stale(self):
        stale_payload = json.dumps({"ts": time.time() - 600}).encode("utf-8")
        redis_client = _FakeRedis(stale_payload)
        stop_event = asyncio.Event()
        bot = _FakeBot()

        task = asyncio.create_task(
            _core_heartbeat_watchdog(
                stop_event=stop_event,
                redis_client=redis_client,
                redis_prefix="elara:",
                base_dir=tempfile.gettempdir(),
                bot=bot,
            )
        )
        await asyncio.sleep(6.5)
        self.assertFalse(stop_event.is_set())
        self.assertEqual("blocked", bot._control_state)
        self.assertEqual("core_heartbeat_stale", bot._control_reason)
        self.assertEqual("block", bot.commands[-1]["cmd"])
        stop_event.set()
        await asyncio.wait_for(task, timeout=3)

    async def test_core_heartbeat_watchdog_resumes_after_recovery(self):
        redis_client = _FakeRedis(json.dumps({"ts": time.time() - 600}).encode("utf-8"))
        stop_event = asyncio.Event()
        bot = _FakeBot()

        task = asyncio.create_task(
            _core_heartbeat_watchdog(
                stop_event=stop_event,
                redis_client=redis_client,
                redis_prefix="elara:",
                base_dir=tempfile.gettempdir(),
                bot=bot,
            )
        )
        await asyncio.sleep(6.5)
        self.assertEqual("blocked", bot._control_state)
        self.assertEqual("core_heartbeat_stale", bot._control_reason)

        redis_client.payload = json.dumps({"ts": time.time()}).encode("utf-8")
        await asyncio.sleep(2.5)
        self.assertEqual("active", bot._control_state)
        self.assertEqual("", bot._control_reason)
        self.assertEqual("resume", bot.commands[-1]["cmd"])

        stop_event.set()
        await asyncio.wait_for(task, timeout=3)

    async def test_watchdog_heartbeat_key_fallback_when_blank(self):
        self._cfg_backup["CORE_HEARTBEAT_KEY"] = getattr(Config, "CORE_HEARTBEAT_KEY")
        Config.CORE_HEARTBEAT_KEY = ""
        self.assertEqual(_resolve_watchdog_heartbeat_key("elara:test:"), core_heartbeat_key("elara:test:"))

    async def test_watchdog_heartbeat_path_fallback_when_blank(self):
        self._cfg_backup["CORE_HEARTBEAT_PATH"] = getattr(Config, "CORE_HEARTBEAT_PATH")
        self._cfg_backup["CORE_HEARTBEAT_FILE_FALLBACK_ENABLED"] = getattr(
            Config, "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", True
        )
        Config.CORE_HEARTBEAT_PATH = ""
        Config.CORE_HEARTBEAT_FILE_FALLBACK_ENABLED = True
        resolved = _resolve_watchdog_heartbeat_path(tempfile.gettempdir(), True)
        self.assertEqual(resolved, os.path.join(tempfile.gettempdir(), "runtime", "core_heartbeat.json"))

    async def test_startup_status_written_to_file_fallback(self):
        self._cfg_backup["TRADING_STATUS_FILE_FALLBACK_ENABLED"] = getattr(
            Config, "TRADING_STATUS_FILE_FALLBACK_ENABLED", True
        )
        Config.TRADING_STATUS_FILE_FALLBACK_ENABLED = True

        with tempfile.TemporaryDirectory(prefix="elara_trader_status_") as tmpdir:
            status_path = os.path.join(tmpdir, "trading_status.json")
            _write_startup_status(status_path, None, "elara:test:", stage="booting")

            self.assertTrue(os.path.exists(status_path))
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertTrue(bool(payload.get("setup_in_progress")))
            self.assertEqual(str(payload.get("setup_stage") or ""), "booting")
            self.assertIn("ts", payload)
            self.assertFalse(bool(payload.get("is_running")))

    async def test_core_heartbeat_watchdog_reads_live_redis_when_available(self):
        redis_client = get_redis()
        if not redis_client:
            self.skipTest("Redis tidak tersedia (REDIS_URL belum aktif).")

        key = "elara:test:core_heartbeat:integration"
        Config.CORE_HEARTBEAT_KEY = key
        Config.CORE_HEARTBEAT_PATH = ""
        redis_client.set(key, json.dumps({"ts": time.time()}))

        stop_event = asyncio.Event()
        bot = _FakeBot()
        task = asyncio.create_task(
            _core_heartbeat_watchdog(
                stop_event=stop_event,
                redis_client=redis_client,
                redis_prefix="elara:",
                base_dir=tempfile.gettempdir(),
                bot=bot,
            )
        )
        await asyncio.sleep(2.2)
        self.assertFalse(stop_event.is_set())
        self.assertEqual("active", bot._control_state)
        stop_event.set()
        await asyncio.wait_for(task, timeout=3)
        try:
            redis_client.delete(key)
        except Exception:
            pass

    async def test_limit_chase_order_timeout_triggers_cancel(self):
        logger = logging.getLogger("test.order.timeout.cancel")
        logger.setLevel(logging.INFO)
        connector = ExchangeConnector(_OrderCfg(), logger)
        fake_exchange = _FakeExchange()
        connector.async_exchange = fake_exchange

        result = await connector.create_market_order("BTC/USDT", "buy", Decimal("1"))

        self.assertIsNone(result)
        self.assertGreaterEqual(len(fake_exchange.limit_order_calls), 1)
        self.assertGreaterEqual(len(fake_exchange.cancel_calls), 1)

    async def test_ws_sequence_gap_triggers_reset(self):
        logger = logging.getLogger("test.ws.sequence")
        connector = ExchangeConnector(_OrderCfg(), logger)

        ok1 = connector._check_ws_sequence("orderbook:BTC/USDT", {"U": 100, "u": 102})
        ok2 = connector._check_ws_sequence("orderbook:BTC/USDT", {"U": 103, "u": 104})
        gap = connector._check_ws_sequence("orderbook:BTC/USDT", {"U": 106, "u": 106})

        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertFalse(gap)

    async def test_symbol_health_marks_delist_risk(self):
        logger = logging.getLogger("test.delist.health")
        connector = ExchangeConnector(_OrderCfg(), logger)
        connector.async_exchange = type(
            "Ex",
            (),
            {
                "markets": {
                    "ABC/USDT": {
                        "active": True,
                        "info": {"status": "delisting"},
                    }
                }
            },
        )()

        health = connector.get_symbol_health("ABC/USDT")
        self.assertEqual(health.get("risk"), "delist_warning")

    async def test_fetch_balances_normalized(self):
        logger = logging.getLogger("test.balance.normalize")
        connector = ExchangeConnector(_OrderCfg(), logger)
        connector.async_exchange = _BalanceExchange()
        balances = await connector.fetch_balances()

        self.assertIsInstance(balances, dict)
        self.assertEqual(balances["USDT"]["free"], Decimal("120.5"))
        self.assertEqual(balances["USDT"]["total"], Decimal("122.0"))


if __name__ == "__main__":
    unittest.main()

