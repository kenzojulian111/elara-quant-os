import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState, StrategyAllocation, StrategyDecision, StrategyProfile
from trading_core.coordination.paper_audit import PaperAuditRepository
from trading_core.orchestration.paper_pipeline import PaperPipelineConfig, PaperTradingPipeline


class _StaticStrategy:
    def __init__(self, profile: StrategyProfile):
        self.profile = profile

    def decide(self, state, indicators, position):
        _ = (state, indicators, position)
        return StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("test_approved_entry",),
        )


class _Library:
    def __init__(self, profile):
        self.profiles = {profile.strategy_id: profile}


class _Portfolio:
    def __init__(self, profile):
        self.library = _Library(profile)
        self.profile = profile

    def build_allocation(self, state):
        _ = state
        return [StrategyAllocation(self.profile.strategy_id, 1.0)]


class PaperPipelineTests(unittest.TestCase):
    def test_approved_decision_persists_market_decision_and_paper_receipt(self):
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        state = MarketState(
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            symbol="BTC/USDT",
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=100.0,
            trend="trend",
            volatility="normal",
            liquidity="deep",
        )
        with tempfile.TemporaryDirectory(prefix="elara_paper_pipeline_") as tmpdir:
            pipeline = PaperTradingPipeline(
                _Portfolio(profile),
                PaperPipelineConfig(
                    ledger_path=os.path.join(tmpdir, "ledger.jsonl"),
                    paper_state_path=os.path.join(tmpdir, "state.json"),
                ),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile)
            receipts = pipeline.process_state(state)
            events = list(pipeline.ledger.read())
            replay = PaperAuditRepository(pipeline.ledger).replay(receipts[0].trace_id)
            rows = PaperAuditRepository(pipeline.ledger).recent_rows()

        self.assertEqual(1, len(receipts))
        self.assertEqual("paper_filled", receipts[0].status)
        self.assertTrue(receipts[0].execution_id.startswith("paper-"))
        self.assertIn("market_data", [event["event_type"] for event in events])
        self.assertIn("decision", [event["event_type"] for event in events])
        execution = [event for event in events if event["event_type"] == "execution"]
        self.assertEqual(1, len(execution))
        self.assertEqual(receipts[0].execution_id, execution[0]["payload"]["execution_id"])
        self.assertTrue(replay.integrity_ok, replay.integrity_reason)
        self.assertTrue(replay.found)
        self.assertEqual("BTC/USDT", replay.market_data["symbol"])
        self.assertEqual(receipts[0].execution_id, replay.execution["execution_id"])
        self.assertEqual(receipts[0].trace_id, rows[0]["trace_id"])
        self.assertEqual(receipts[0].execution_id, rows[0]["execution_id"])


if __name__ == "__main__":
    unittest.main()
