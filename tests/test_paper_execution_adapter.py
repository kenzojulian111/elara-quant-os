import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination.action_context import ActionContext
from trading_core.execution.adapter import PaperExecutionAdapter
from trading_core.data_contracts import ExecutionPlan


class PaperExecutionAdapterTests(unittest.TestCase):
    def test_duplicate_execution_id_returns_durable_original_receipt_after_restart(self):
        plan = ExecutionPlan(
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            direction="long",
            entry_price=100.0,
            size_pct=0.01,
            stop_loss=98.0,
            take_profit=105.0,
            trace_id="decision-1",
            execution_id="paper-decision-1",
        )
        with tempfile.TemporaryDirectory(prefix="elara_paper_execution_") as tmpdir:
            path = os.path.join(tmpdir, "paper-executions.json")
            first = PaperExecutionAdapter(path).execute(plan, ActionContext.create("test"))
            second = PaperExecutionAdapter(path).execute(plan, ActionContext.create("test-retry"))

        self.assertEqual("paper_filled", first.status)
        self.assertEqual("paper", first.mode)
        self.assertEqual(first.execution_id, second.execution_id)
        self.assertEqual(first.timestamp, second.timestamp)
        self.assertEqual(first.action_id, second.action_id)


if __name__ == "__main__":
    unittest.main()
