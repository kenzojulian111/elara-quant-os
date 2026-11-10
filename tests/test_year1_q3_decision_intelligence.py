import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination.action_context import ActionContext
from trading_core.data_contracts import IndicatorSnapshot, MarketState, ReasoningComponent, StrategyDecision, TradeCandidate
from trading_core.intelligence.bayesian import BayesianSignalModel
from trading_core.intelligence.causal_graph import default_macro_causal_graph
from trading_core.intelligence.cognitive_modes import CognitiveModeEngine
from trading_core.intelligence.explainability import explain_decision
from trading_core.intelligence.probabilistic import ProbabilisticDecisionEngine
from trading_core.intelligence.regime import RegimeDetector
from trading_core.orchestration.decision_core import DecisionCore, DecisionCoreConfig
from trading_core.orchestration.live_pipeline import LivePipelineConfig, LiveTradingPipeline
from trading_core.risk.risk_engine import RiskContext, RiskEngine, RiskLimits
from trading_core.strategy.base import PositionState


class _DummyPortfolioBrain:
    def build_allocation(self, market_state):
        _ = market_state
        return []


class _StaticStrategy:
    def __init__(self, decision: StrategyDecision):
        self._decision = decision

    def decide(self, state, indicators, position):
        _ = (state, indicators, position)
        return self._decision


class Year1Q3DecisionIntelligenceTests(unittest.TestCase):
    def _state(self, close=101.0, volume=1000.0, offset=0, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=offset),
            "symbol": "BTC/USDT",
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": volume,
            "trend": "trend",
            "volatility": "normal",
            "liquidity": "deep",
        }
        payload.update(overrides)
        return MarketState(**payload)

    def _indicators(self) -> IndicatorSnapshot:
        return IndicatorSnapshot(ema_fast=101, ema_slow=99, rsi=58, atr=2, atr_pct=1.5)

    def _decision_core(self) -> DecisionCore:
        return DecisionCore(
            LiveTradingPipeline(
                _DummyPortfolioBrain(),
                LivePipelineConfig(max_risk_per_trade_pct=0.02, max_drawdown_pct=10.0),
            ),
            DecisionCoreConfig(risk_per_trade_pct=0.02, min_entry_confidence=0.5),
        )

    def test_regime_detector_classifies_trend_volatility_and_liquidity(self):
        states = [
            self._state(close=100 + idx * 2, volume=1000, offset=idx)
            for idx in range(6)
        ]

        assessment = RegimeDetector().assess(states)
        enriched = RegimeDetector().apply_to_state(states[-1], assessment)

        self.assertEqual("trend_up", assessment.trend)
        self.assertEqual("deep", assessment.liquidity)
        self.assertGreater(assessment.confidence, 0.3)
        self.assertEqual("trend_up", enriched.trend)

    def test_decision_reasoner_adds_probability_fields(self):
        envelope = self._decision_core().evaluate(
            context=ActionContext.create("q3_test"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(
                StrategyDecision(
                    action="enter_long",
                    confidence=0.85,
                    stop_loss=98.0,
                    take_profit=110.0,
                    reasons=("trend_alignment",),
                )
            ),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=1.0,
            current_drawdown_pct=1.0,
        )

        assessment = ProbabilisticDecisionEngine().assess(envelope)

        self.assertIsNotNone(envelope.reasoned_decision)
        self.assertGreater(envelope.reasoned_decision.probability, 0.0)
        self.assertGreaterEqual(envelope.reasoned_decision.uncertainty, 0.05)
        self.assertIn("probability", envelope.reasoned_decision.component_scores)
        self.assertEqual("bayesian_beta", envelope.reasoned_decision.component_scores["probability_model"])
        self.assertIn("bayesian_posterior", envelope.reasoned_decision.component_scores)
        self.assertEqual(envelope.reasoned_decision.probability, assessment.probability)

    def test_explainability_report_reconstructs_pipeline(self):
        envelope = self._decision_core().evaluate(
            context=ActionContext.create("q3_explain"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(
                StrategyDecision(
                    action="enter_long",
                    confidence=0.85,
                    stop_loss=98.0,
                    take_profit=110.0,
                    reasons=("trend_alignment",),
                )
            ),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=1.0,
            current_drawdown_pct=1.0,
        )

        report = explain_decision(envelope).to_dict()

        self.assertEqual(envelope.trace_id, report["trace_id"])
        self.assertEqual("enter_long", report["requested_action"])
        self.assertTrue(report["pipeline"])
        self.assertGreater(report["probability"], 0.0)
        self.assertEqual("bayesian_beta", report["probability_model"])
        self.assertGreaterEqual(report["posterior"]["evidence_count"], 1)

    def test_bayesian_signal_model_updates_prior_and_tracks_conflict(self):
        model = BayesianSignalModel()
        supportive = (
            ReasoningComponent(
                source="strategy",
                label="trend",
                confidence=0.85,
                weight=0.45,
                verdict="support",
                reasons=("trend_alignment",),
            ),
            ReasoningComponent(
                source="risk",
                label="approved",
                confidence=0.75,
                weight=0.20,
                verdict="support",
                reasons=("risk_budget_ok",),
            ),
        )
        conflicted = (
            *supportive,
            ReasoningComponent(
                source="runtime",
                label="stale_feed",
                confidence=0.90,
                weight=0.20,
                verdict="block",
                reasons=("feed_stale",),
            ),
        )

        support_posterior = model.infer(supportive, prior=0.5)
        conflict_posterior = model.infer(conflicted, prior=0.5)

        self.assertGreater(support_posterior.probability, 0.5)
        self.assertGreater(conflict_posterior.conflict, support_posterior.conflict)
        self.assertGreater(conflict_posterior.uncertainty, support_posterior.uncertainty)

    def test_causal_graph_propagates_macro_evidence(self):
        graph = default_macro_causal_graph()

        result = graph.infer({"rates": 0.9, "liquidity": 0.2, "risk_sentiment": 0.25}, steps=3)

        self.assertLess(result.beliefs["crypto_risk"], 0.5)
        self.assertLess(result.beliefs["btc_trend"], 0.5)
        self.assertTrue(result.active_paths)
        self.assertIn("active_paths", result.reasons[-1])

    def test_contextual_risk_reduces_size_under_uncertainty_and_thin_liquidity(self):
        engine = RiskEngine(RiskLimits(max_risk_per_trade_pct=0.02))
        candidate = TradeCandidate(
            strategy_id="trend",
            symbol="BTC/USDT",
            direction="long",
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            size_pct=0.02,
        )

        decision = engine.evaluate_contextual(
            candidate,
            current_drawdown_pct=1.0,
            context=RiskContext(uncertainty=0.5, liquidity="thin", volatility="high"),
        )

        self.assertTrue(decision.approved)
        self.assertLess(decision.size_pct, 0.02)
        self.assertEqual("contextual_size_adjusted", decision.reason)
        self.assertIn("liquidity_size_multiplier=0.5000", decision.reasons)

    def test_cognitive_modes_shift_under_runtime_and_market_conditions(self):
        engine = CognitiveModeEngine()

        crisis = engine.assess(
            runtime_mode="safe",
            system_health_score=30,
            current_drawdown_pct=15,
            market_volatility="extreme",
        )
        opportunistic = engine.assess(
            runtime_mode="full",
            system_health_score=95,
            current_drawdown_pct=1,
            market_volatility="normal",
        )

        self.assertEqual("crisis", crisis.mode)
        self.assertFalse(crisis.allow_new_entries)
        self.assertEqual("opportunistic", opportunistic.mode)
        self.assertTrue(opportunistic.allow_new_entries)


if __name__ == "__main__":
    unittest.main()
