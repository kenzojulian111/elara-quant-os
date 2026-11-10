import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.events import parse_trade_command_message


class TradeCommandParsingTests(unittest.TestCase):
    def test_legacy_limit_order_payload_is_accepted(self):
        parsed = parse_trade_command_message(
            {
                "cmd": "limit_order",
                "source": "tool_call",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.1,
                "price": 42000,
            }
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("cmd"), "limit_order")
        self.assertEqual(parsed.get("symbol"), "BTC/USDT")
        self.assertEqual(parsed.get("side"), "buy")

    def test_invalid_priority_falls_back_to_default(self):
        parsed = parse_trade_command_message(
            {
                "cmd": "stop",
                "priority": "high",
            }
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("priority"), 99)

    def test_command_envelope_preserves_execution_identity(self):
        parsed = parse_trade_command_message(
            {
                "event": "trade.command.v1",
                "source": "unit",
                "ts": 1.0,
                "payload": {
                    "cmd": "stop",
                    "command_id": "cmd-1",
                    "decision_id": "decision-1",
                    "execution_id": "exec-1",
                },
            }
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("command_id"), "cmd-1")
        self.assertEqual(parsed.get("decision_id"), "decision-1")
        self.assertEqual(parsed.get("execution_id"), "exec-1")


if __name__ == "__main__":
    unittest.main()

