import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination.action_context import ActionContext
from trading_core.data_contracts import (
    DecisionEnvelope,
    DecisionTraceStep,
    IndicatorSnapshot,
    MarketState,
    ReasonedDecision,
    ReasoningComponent,
    StrategyDecision,
)
from trading_core.orchestration.decision_core import DecisionCore, DecisionCoreConfig
from trading_core.orchestration.live_pipeline import LivePipelineConfig, LiveTradingPipeline
from trading_core.roadmap import CognitiveCoherenceChecker
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


class CognitiveCoherenceTests(unittest.TestCase):
    def _state(self) -> MarketState:
        return MarketState(
            ts=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
            symbol="BTC/USDT",
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1000.0,
        )

    def _indicators(self) -> IndicatorSnapshot:
        return IndicatorSnapshot(
            ema_fast=101.0,
            ema_slow=99.0,
            rsi=58.0,
            atr=2.0,
            atr_pct=1.5,
            macd=0.8,
            macd_signal=0.5,
            macd_hist=0.3,
            adx=24.0,
            bb_upper=104.0,
            bb_lower=96.0,
            bb_mid=100.0,
            bb_width=0.02,
        )

    def _decision_core(self) -> DecisionCore:
        live_pipeline = LiveTradingPipeline(
            _DummyPortfolioBrain(),
            LivePipelineConfig(max_risk_per_trade_pct=0.02, max_drawdown_pct=10.0),
        )
        return DecisionCore(
            live_pipeline,
            DecisionCoreConfig(risk_per_trade_pct=0.02, min_entry_confidence=0.5),
        )

    def test_static_cognitive_layers_have_owner_modules(self):
        report = CognitiveCoherenceChecker(ROOT).build_static_report()

        self.assertEqual("ok", report.status)
        self.assertEqual(0, report.critical_count)
        self.assertGreaterEqual(report.metrics["layer_count"], 8)
        self.assertIn("reasoning", {layer.name for layer in report.layers})

    def test_ready_decision_satisfies_cognitive_coherence_contract(self):
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.82,
            stop_loss=97.0,
            take_profit=109.0,
            reasons=("trend_alignment", "momentum_confirmed"),
        )
        envelope = self._decision_core().evaluate(
            context=ActionContext.create("unit_test"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(decision),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=2.0,
            current_drawdown_pct=4.0,
        )

        report = CognitiveCoherenceChecker(ROOT).evaluate_envelope(envelope)

        self.assertEqual("ok", report.status)
        self.assertEqual("reasoning", envelope.pipeline_trace[-1].step)
        self.assertIn("strategy", report.metrics["component_sources"])
        self.assertIn("risk", report.metrics["component_sources"])

    def test_reasoner_replaces_prior_reasoning_trace(self):
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.82,
            stop_loss=97.0,
            take_profit=109.0,
            reasons=("trend_alignment",),
        )
        core = self._decision_core()
        envelope = core.evaluate(
            context=ActionContext.create("unit_test"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(decision),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=2.0,
            current_drawdown_pct=4.0,
        )

        resynthesized = core.reasoner.synthesize(envelope)
        reasoning_steps = tuple(step for step in resynthesized.pipeline_trace if step.step == "reasoning")

        self.assertEqual(1, len(reasoning_steps))
        self.assertEqual("reasoning", resynthesized.pipeline_trace[-1].step)

    def test_broken_envelope_is_critical(self):
        envelope = DecisionEnvelope(
            trace_id="",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="ready",
            decision="enter_long",
            confidence=0.9,
        )

        report = CognitiveCoherenceChecker(ROOT).evaluate_envelope(envelope)
        codes = {finding.code for finding in report.findings}

        self.assertEqual("critical", report.status)
        self.assertIn("missing_trace_id", codes)
        self.assertIn("missing_pipeline_trace", codes)
        self.assertIn("missing_signal_trace", codes)
        self.assertIn("missing_reasoned_decision", codes)

    def test_approved_entry_without_execution_plan_is_critical(self):
        reasoned = ReasonedDecision(
            action="enter_long",
            approved=True,
            confidence=0.8,
            summary="enter_long approved",
            explanation="Strategy supports entry.",
            reasons=("trend_alignment",),
            probability=0.8,
            uncertainty=0.2,
            expected_utility=0.4,
            component_scores={
                "probability": 0.8,
                "uncertainty": 0.2,
                "expected_utility": 0.4,
                "probability_model": "bayesian_v1",
                "bayesian_posterior": {"enter_long": 0.8},
            },
            components=(
                ReasoningComponent(
                    source="strategy",
                    label="enter_long",
                    confidence=0.8,
                    weight=0.45,
                    verdict="support",
                    reasons=("trend_alignment",),
                ),
            ),
        )
        envelope = DecisionEnvelope(
            trace_id="trace-1",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="ready",
            decision="enter_long",
            confidence=0.8,
            reasons=("trend_alignment",),
            pipeline_trace=(
                DecisionTraceStep(step="signal", status="observed", reasons=("trend_alignment",)),
                DecisionTraceStep(step="reasoning", status="approved", reasons=("trend_alignment",)),
            ),
            reasoned_decision=reasoned,
        )

        report = CognitiveCoherenceChecker(ROOT).evaluate_envelope(envelope)
        codes = {finding.code for finding in report.findings}

        self.assertEqual("critical", report.status)
        self.assertIn("approved_entry_without_execution_plan", codes)


if __name__ == "__main__":
    unittest.main()
