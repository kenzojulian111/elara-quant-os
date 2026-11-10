import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if "numpy" not in sys.modules:
    class _Array(list):
        def mean(self):
            return sum(self) / len(self) if self else 0.0

        def std(self, ddof=0):
            if not self:
                return 0.0
            denom = max(1, len(self) - int(ddof))
            avg = self.mean()
            variance = sum((value - avg) ** 2 for value in self) / denom
            return variance ** 0.5

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.array = lambda values, dtype=float: _Array(dtype(value) for value in values)
    sys.modules["numpy"] = fake_numpy

from trading_core.data_contracts import MarketState, StrategyDecision, StrategyPerformance, StrategyProfile
from trading_core.library.strategy_library import StrategyLibrary
from trading_core.orchestration.portfolio_brain import PortfolioBrain
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline


class _StaticStrategy:
    def __init__(self, profile: StrategyProfile, decision: StrategyDecision):
        self.profile = profile
        self._decision = decision

    def decide(self, state, indicators, position):
        _ = (state, indicators, position)
        return self._decision


class PortfolioBrainMetaDecisionTests(unittest.TestCase):
    def _state(self, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
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

    def _library(self, profiles, performance=None) -> StrategyLibrary:
        library = StrategyLibrary()
        for profile in profiles:
            library.register_profile(profile)
        for perf in performance or ():
            library.update_performance(perf)
        return library

    def test_meta_decision_prefers_regime_matched_strategy(self):
        trend_profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        range_profile = StrategyProfile("mean_reversion_v1", "Mean Reversion", tags={"mean_reversion"})
        brain = PortfolioBrain(self._library([trend_profile, range_profile]))

        plan = brain.build_decision_plan(self._state(trend="trend", volatility="normal"))

        self.assertEqual("trend", plan.market_regime)
        self.assertEqual(("trend_following_v1",), plan.selected_ids)
        self.assertIn("mean_reversion_v1", plan.suppressed_ids)
        self.assertEqual("unproven", plan.review_status["trend_following_v1"])
        self.assertIn("meta_decision_selected_regime_matched_strategies", plan.reasons)

    def test_self_review_blocks_hard_drawdown_and_falls_back_to_eligible_strategy(self):
        trend_profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        range_profile = StrategyProfile("mean_reversion_v1", "Mean Reversion", tags={"mean_reversion"})
        performance = [
            StrategyPerformance(
                strategy_id="trend_following_v1",
                trades=12,
                pnl=-180.0,
                max_drawdown_pct=14.0,
                sharpe=-0.4,
                win_rate=0.31,
            ),
            StrategyPerformance(
                strategy_id="mean_reversion_v1",
                trades=12,
                pnl=90.0,
                max_drawdown_pct=3.0,
                sharpe=0.9,
                win_rate=0.56,
            ),
        ]
        brain = PortfolioBrain(self._library([trend_profile, range_profile], performance))

        plan = brain.build_decision_plan(self._state(trend="trend", volatility="high"))

        self.assertEqual(("mean_reversion_v1",), plan.selected_ids)
        self.assertIn("trend_following_v1", plan.blocked_ids)
        self.assertEqual("blocked", plan.review_status["trend_following_v1"])
        self.assertIn("fallback_selected_all_eligible_strategies", plan.reasons)

    def test_self_review_downweights_degraded_strategy_against_strong_peer(self):
        lead_profile = StrategyProfile("trend_following_v1", "Trend A", tags={"trend"})
        lag_profile = StrategyProfile("trend_following_v2", "Trend B", tags={"trend"})
        performance = [
            StrategyPerformance(
                strategy_id="trend_following_v1",
                trades=14,
                pnl=220.0,
                max_drawdown_pct=2.0,
                sharpe=1.4,
                win_rate=0.67,
            ),
            StrategyPerformance(
                strategy_id="trend_following_v2",
                trades=14,
                pnl=-20.0,
                max_drawdown_pct=9.0,
                sharpe=0.1,
                win_rate=0.42,
            ),
        ]
        brain = PortfolioBrain(self._library([lead_profile, lag_profile], performance))

        plan = brain.build_decision_plan(self._state(trend="trend", volatility="high"))
        weights = {allocation.strategy_id: allocation.weight for allocation in plan.allocations}

        self.assertEqual("strong", plan.review_status["trend_following_v1"])
        self.assertEqual("degraded", plan.review_status["trend_following_v2"])
        self.assertGreater(weights["trend_following_v1"], weights["trend_following_v2"])

    def test_shadow_pipeline_logs_portfolio_plan(self):
        trend_profile = StrategyProfile("trend_following_v1", "Trend", tags={"trend"})
        brain = PortfolioBrain(self._library([trend_profile]))

        with tempfile.TemporaryDirectory(prefix="elara_portfolio_plan_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                brain,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[trend_profile.strategy_id] = _StaticStrategy(
                trend_profile,
                StrategyDecision(action="hold", confidence=0.2, reasons=("no_actionable_edge",)),
            )

            receipts = pipeline.process_state(self._state(trend="trend", volatility="normal"))
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        portfolio_events = [event for event in events if event.get("event_type") == "portfolio"]
        self.assertTrue(portfolio_events)
        self.assertEqual("trend", portfolio_events[0]["payload"]["market_regime"])


if __name__ == "__main__":
    unittest.main()

