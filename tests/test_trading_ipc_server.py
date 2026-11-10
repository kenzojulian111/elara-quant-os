import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.events import trade_command_queue_key, trader_status_key
from skills.advanced.trading import ipc_server


class TradingIpcServerTests(unittest.TestCase):
    def test_resolve_status_key_fallback(self):
        with patch.object(ipc_server.Config, "TRADING_STATUS_KEY", ""):
            key = ipc_server._resolve_status_key("elara:test:")
        self.assertEqual(key, trader_status_key("elara:test:"))

    def test_resolve_command_key_fallback(self):
        with patch.object(ipc_server.Config, "TRADING_COMMAND_KEY", ""):
            key = ipc_server._resolve_command_key("elara:test:")
        self.assertEqual(key, trade_command_queue_key("elara:test:"))

    def test_ipc_loopback_allowed_when_token_configured(self):
        handler = object.__new__(ipc_server._Handler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {}
        with patch.object(ipc_server.Config, "TRADING_IPC_TOKEN", "secret"):
            self.assertTrue(handler._authorize_request())

    def test_build_payload_standard_command_envelope(self):
        payload = ipc_server._build_ipc_command_payload(
            {
                "cmd": "stop",
                "source": "ipc_test",
                "reason": "manual",
                "user_id": "u1",
                "priority": "3",
            }
        )
        self.assertEqual(payload["event"], "trade.command.v1")
        self.assertEqual(payload["payload"]["cmd"], "stop")
        self.assertEqual(payload["payload"]["priority"], 3)

    def test_build_payload_limit_order_keeps_optional_fields(self):
        payload = ipc_server._build_ipc_command_payload(
            {
                "cmd": "limit_order",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.1,
                "price": 50000,
                "execution_id": "exec-1",
            }
        )
        self.assertEqual(payload["payload"]["cmd"], "limit_order")
        self.assertEqual(payload["payload"]["symbol"], "BTC/USDT")
        self.assertEqual(payload["payload"]["side"], "buy")
        self.assertEqual(payload["payload"]["amount"], 0.1)
        self.assertEqual(payload["payload"]["price"], 50000)
        self.assertEqual(payload["payload"]["execution_id"], "exec-1")

    def test_build_payload_accepts_command_envelope(self):
        payload = ipc_server._build_ipc_command_payload(
            {
                "event": "trade.command.v1",
                "source": "web_api",
                "ts": 1.0,
                "version": 1,
                "payload": {
                    "cmd": "stop",
                    "reason": "manual",
                    "command_id": "cmd-1",
                    "execution_id": "exec-1",
                },
            }
        )

        self.assertEqual(payload["payload"]["cmd"], "stop")
        self.assertEqual(payload["payload"]["command_id"], "cmd-1")
        self.assertEqual(payload["payload"]["execution_id"], "exec-1")

    def test_build_payload_invalid_command_raises(self):
        with self.assertRaises(ValueError):
            ipc_server._build_ipc_command_payload({"cmd": "invalid"})

    def test_decode_json_payload_accepts_bytes_and_rejects_bad_json(self):
        payload = ipc_server._decode_json_payload(b'{"is_running": true}')
        self.assertTrue(payload["is_running"])
        self.assertEqual({}, ipc_server._decode_json_payload("{bad-json"))

    def test_record_status_metrics_exports_trader_status(self):
        ipc_server._record_status_metrics(
            {
                "ts": time.time(),
                "is_running": True,
                "control_state": "active",
                "open_positions": 1,
            },
            source="unit_ipc",
        )

        rendered = ipc_server.OBSERVABILITY_REGISTRY.render_prometheus()

        self.assertIn('elara_trader_ipc_up{source="unit_ipc"} 1', rendered)
        self.assertIn('elara_trader_running{source="unit_ipc"} 1', rendered)
        self.assertIn('elara_trader_open_positions{source="unit_ipc"} 1', rendered)


if __name__ == "__main__":
    unittest.main()

