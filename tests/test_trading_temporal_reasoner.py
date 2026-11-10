import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState, StrategyAllocation, StrategyDecision, StrategyProfile
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline
from trading_core.orchestration.temporal_reasoner import TemporalConfig, TemporalReasoner
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


class _TemporalTestPortfolioBrain:
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


class TradingTemporalReasonerTests(unittest.TestCase):
    def _state(self, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "symbol": "BTC/USDT",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1000.0,
            "trend": "range",
            "volatility": "normal",
            "liquidity": "deep",
        }
        payload.update(overrides)
        return MarketState(**payload)

    def test_persistent_range_blocks_trend_entries(self):
        engine = TemporalReasoner(
            TemporalConfig(
                regime_window=4,
                persistent_regime_streak=3,
                persistent_regime_ratio=0.67,
            )
        )
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        plan = PortfolioDecisionPlan(
            symbol="BTC/USDT",
            market_regime="range",
            selected_ids=(profile.strategy_id,),
            allocations=(StrategyAllocation(strategy_id=profile.strategy_id, weight=1.0),),
            reasons=("market_regime=range",),
        )

        context = None
        for _ in range(3):
            context = engine.observe_state(self._state(trend="range", volatility="normal"), portfolio_plan=plan)

        bias = engine.assess_strategy(profile.strategy_id, profile, context)

        self.assertIsNotNone(context)
        self.assertEqual("range", context.dominant_regime)
        self.assertTrue(bias.block_new_entries)
        self.assertIn("temporal_regime_block=trend_in_persistent_range", bias.reasons)

    def test_loss_streak_blocks_new_entries(self):
        engine = TemporalReasoner(
            TemporalConfig(
                loss_memory=4,
                loss_streak_penalty_threshold=2,
                loss_streak_block_threshold=3,
            )
        )
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        plan = PortfolioDecisionPlan(
            symbol="BTC/USDT",
            market_regime="trend",
            selected_ids=(profile.strategy_id,),
            allocations=(StrategyAllocation(strategy_id=profile.strategy_id, weight=1.0),),
            reasons=("market_regime=trend",),
        )
        for _ in range(3):
            engine.record_trade_outcome(profile.strategy_id, pnl_pct=-0.02, reason="stop_loss_hit")

        context = engine.observe_state(self._state(trend="trend", volatility="high"), portfolio_plan=plan)
        bias = engine.assess_strategy(profile.strategy_id, profile, context)

        self.assertEqual(3, bias.loss_streak)
        self.assertTrue(bias.block_new_entries)
        self.assertIn("temporal_loss_streak_block=3", bias.reasons)

    def test_shadow_pipeline_uses_temporal_memory_before_allowing_entry(self):
        profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        brain = _TemporalTestPortfolioBrain(
            [profile],
            [StrategyAllocation(strategy_id=profile.strategy_id, weight=1.0)],
            market_regime="range",
        )
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_temporal_reasoning_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                brain,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)

            pipeline.process_state(
                self._state(trend=None, volatility="normal"),
                current_drawdown_pct=1.0,
                operator_allows_trading=False,
                operator_reason="warmup",
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            pipeline.process_state(
                self._state(trend=None, volatility="normal"),
                current_drawdown_pct=1.0,
                operator_allows_trading=False,
                operator_reason="warmup",
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            receipts = pipeline.process_state(
                self._state(trend=None, volatility="normal"),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        temporal_events = [event for event in events if event.get("event_type") == "temporal"]
        self.assertTrue(temporal_events)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("blocked_reasoning", latest["status"])
        self.assertIn("temporal_regime_block=trend_in_persistent_range", latest["reasoned_decision"]["constraints"])


if __name__ == "__main__":
    unittest.main()

