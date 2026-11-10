import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState, StrategyAllocation, StrategyDecision, StrategyProfile
from trading_core.orchestration.objective_engine import ObjectiveEngine
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline
from trading_core.strategy.base import PositionState
from utils.health import RuntimeModeAssessment


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


class _ObjectiveTestPortfolioBrain:
    def __init__(self, profiles, allocations, market_regime: str):
        self.library = _FakeLibrary(profiles)
        self._allocations = tuple(allocations)
        self._market_regime = market_regime

    def build_decision_plan(self, market_state):
        return PortfolioDecisionPlan(
            symbol=market_state.symbol,
            market_regime=self._market_regime,
            selected_ids=tuple(allocation.strategy_id for allocation in self._allocations),
            allocations=self._allocations,
            reasons=(f"market_regime={self._market_regime}", "portfolio_ready"),
        )


class TradingObjectiveEngineTests(unittest.TestCase):
    def _state(self, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 4, 17, 18, 0, tzinfo=timezone.utc),
            "symbol": "BTC/USDT",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1000.0,
            "trend": "trend",
            "volatility": "normal",
            "liquidity": "deep",
        }
        payload.update(overrides)
        return MarketState(**payload)

    def test_adaptive_objective_shifts_to_preserve_capital_under_drawdown(self):
        engine = ObjectiveEngine()
        runtime = RuntimeModeAssessment(
            mode="full",
            reasons=("runtime_full",),
            allow_new_entries=True,
            allow_background_tasks=True,
        )
        plan = PortfolioDecisionPlan(
            symbol="BTC/USDT",
            market_regime="trend",
            selected_ids=("trend_following_v1",),
            allocations=(StrategyAllocation(strategy_id="trend_following_v1", weight=1.0),),
            reasons=("market_regime=trend",),
        )

        profile = engine.assess(
            preferred_objective="adaptive",
            current_drawdown_pct=9.0,
            runtime_assessment=runtime,
            portfolio_plan=plan,
        )

        self.assertEqual("preserve_capital", profile.objective)
        self.assertIn("objective_shift=preserve_capital_drawdown", profile.reasons)

    def test_preserve_capital_blocks_marginal_new_entries(self):
        engine = ObjectiveEngine()
        runtime = RuntimeModeAssessment(
            mode="full",
            reasons=("runtime_full",),
            allow_new_entries=True,
            allow_background_tasks=True,
        )
        plan = PortfolioDecisionPlan(
            symbol="BTC/USDT",
            market_regime="trend",
            selected_ids=("trend_following_v1",),
            allocations=(StrategyAllocation(strategy_id="trend_following_v1", weight=1.0),),
            reasons=("market_regime=trend",),
        )
        objective = engine.assess(
            preferred_objective="preserve_capital",
            current_drawdown_pct=3.0,
            runtime_assessment=runtime,
            portfolio_plan=plan,
        )
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})

        bias = engine.assess_strategy(
            strategy_profile=profile,
            objective_profile=objective,
            decision_action="enter_long",
            decision_confidence=0.72,
            allocation_weight=1.0,
            market_regime=plan.market_regime,
        )

        self.assertTrue(bias.block_new_entries)
        self.assertIn("objective_preserve_capital_confidence_floor=0.80", bias.reasons)

    def test_shadow_pipeline_uses_objective_profile_before_entry(self):
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        brain = _ObjectiveTestPortfolioBrain(
            [profile],
            [StrategyAllocation(strategy_id=profile.strategy_id, weight=1.0)],
            market_regime="trend",
        )
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.72,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_objective_reasoning_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                brain,
                config=ShadowPipelineConfig(
                    ledger_path=os.path.join(tmpdir, "ledger.jsonl"),
                    preferred_objective="preserve_capital",
                ),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            receipts = pipeline.process_state(
                self._state(trend="trend", volatility="high"),
                current_drawdown_pct=3.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        objective_events = [event for event in events if event.get("event_type") == "objective"]
        self.assertTrue(objective_events)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("blocked_reasoning", latest["status"])
        self.assertIn("objective_preserve_capital_confidence_floor=0.80", latest["reasoned_decision"]["constraints"])


if __name__ == "__main__":
    unittest.main()

