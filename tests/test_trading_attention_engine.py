import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState, StrategyAllocation, StrategyDecision, StrategyProfile
from trading_core.execution.adapter import ExecutionReceipt
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline
from trading_core.strategy.base import PositionState


class _StaticStrategy:
    def __init__(self, profile: StrategyProfile, decision: StrategyDecision):
        self.profile = profile
        self._decision = decision

    def decide(self, state, indicators, position):
        _ = (state, indicators, position)
        return self._decision


class _FakeLibrary:
    def __init__(self, profiles):
        self.profiles = {profile.strategy_id: profile for profile in profiles}


class _AttentionTestPortfolioBrain:
    def __init__(self, profiles, allocations):
        self.library = _FakeLibrary(profiles)
        self._allocations = tuple(allocations)

    def build_decision_plan(self, market_state):
        return PortfolioDecisionPlan(
            symbol=market_state.symbol,
            market_regime="range",
            selected_ids=tuple(allocation.strategy_id for allocation in self._allocations),
            allocations=self._allocations,
            reasons=("market_regime=range", "portfolio_ready"),
        )


class TradingAttentionEngineTests(unittest.TestCase):
    def _state(self, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
            "symbol": "BTC/USDT",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
            "trend": "range",
            "volatility": "normal",
            "liquidity": "deep",
        }
        payload.update(overrides)
        return MarketState(**payload)

    def test_shadow_pipeline_limits_new_entries_under_simple_attention(self):
        lead = StrategyProfile("mr_fast", "MR Fast", tags={"mean_reversion"})
        lag = StrategyProfile("mr_slow", "MR Slow", tags={"mean_reversion"})
        brain = _AttentionTestPortfolioBrain(
            [lead, lag],
            [
                StrategyAllocation(strategy_id="mr_fast", weight=0.8),
                StrategyAllocation(strategy_id="mr_slow", weight=0.2),
            ],
        )

        with tempfile.TemporaryDirectory(prefix="elara_attention_simple_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                brain,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies["mr_fast"] = _StaticStrategy(lead, StrategyDecision(action="hold", confidence=0.0, reasons=("wait_fast",)))
            pipeline.strategies["mr_slow"] = _StaticStrategy(lag, StrategyDecision(action="hold", confidence=0.0, reasons=("wait_slow",)))

            receipts = pipeline.process_state(self._state())
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        attention_events = [event for event in events if event.get("event_type") == "attention"]
        self.assertTrue(attention_events)
        attention = attention_events[0]["payload"]
        self.assertEqual("simple", attention["mode"])
        self.assertEqual(["mr_fast"], attention["focused_strategy_ids"])
        self.assertEqual(["mr_slow"], attention["skipped_strategy_ids"])

        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertEqual(1, len(decision_events))
        self.assertEqual("mr_fast", decision_events[0]["payload"]["strategy_id"])
        trace = decision_events[0]["payload"]["pipeline_trace"]
        tail_steps = [step["step"] for step in trace[-6:]]
        self.assertEqual(["attention", "objective", "temporal", "runtime_mode", "governance", "reasoning"], tail_steps)

    def test_shadow_pipeline_keeps_active_positions_in_focus_even_in_simple_mode(self):
        lead = StrategyProfile("mr_fast", "MR Fast", tags={"mean_reversion"})
        lag = StrategyProfile("mr_slow", "MR Slow", tags={"mean_reversion"})
        brain = _AttentionTestPortfolioBrain(
            [lead, lag],
            [
                StrategyAllocation(strategy_id="mr_fast", weight=0.8),
                StrategyAllocation(strategy_id="mr_slow", weight=0.2),
            ],
        )

        with tempfile.TemporaryDirectory(prefix="elara_attention_positions_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                brain,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies["mr_fast"] = _StaticStrategy(lead, StrategyDecision(action="hold", confidence=0.0, reasons=("wait_fast",)))
            pipeline.strategies["mr_slow"] = _StaticStrategy(lag, StrategyDecision(action="exit", confidence=0.7, reasons=("exit_slow",)))
            pipeline.positions["mr_slow"] = PositionState(
                direction="long",
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                units=None,
            )

            receipts = pipeline.process_state(self._state())
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        strategy_ids = [event["payload"]["strategy_id"] for event in decision_events]
        self.assertEqual({"mr_fast", "mr_slow"}, set(strategy_ids))
        attention_events = [event for event in events if event.get("event_type") == "attention"]
        self.assertTrue(attention_events)
        attention = attention_events[0]["payload"]
        self.assertIn("mr_slow", attention["forced_strategy_ids"])
        self.assertEqual([], attention["skipped_strategy_ids"])


if __name__ == "__main__":
    unittest.main()

