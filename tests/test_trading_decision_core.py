import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination.action_context import ActionContext
from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision
from trading_core.orchestration.decision_core import DecisionCore, DecisionCoreConfig
from trading_core.orchestration.live_pipeline import LivePipelineConfig, LiveTradingPipeline
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


class TradingDecisionCoreTests(unittest.TestCase):
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

    def test_ready_decision_includes_trace_and_risk_reason(self):
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.82,
            stop_loss=97.0,
            take_profit=109.0,
            reasons=("trend_alignment", "momentum_confirmed"),
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

        self.assertEqual("ready", envelope.status)
        self.assertTrue(envelope.trace_id)
        self.assertIsNotNone(envelope.execution_plan)
        self.assertIsNotNone(envelope.risk)
        self.assertTrue(envelope.risk.approved)
        self.assertEqual("size_capped_to_limit", envelope.risk.reason)
        self.assertAlmostEqual(0.02, envelope.execution_plan.size_pct)
        self.assertIn("trend_alignment", envelope.reasons)
        self.assertIn("size_capped_to_limit", envelope.reasons)
        self.assertIsNotNone(envelope.reasoned_decision)
        self.assertTrue(envelope.reasoned_decision.approved)
        self.assertEqual("enter_long", envelope.reasoned_decision.action)
        self.assertGreater(envelope.reasoned_decision.confidence, 0.6)
        self.assertIn("trend_alignment", envelope.reasoned_decision.explanation)
        self.assertEqual("signal", envelope.pipeline_trace[0].step)
        self.assertEqual("risk_gate", envelope.pipeline_trace[-3].step)
        self.assertEqual("decision", envelope.pipeline_trace[-2].step)
        self.assertEqual("ready", envelope.pipeline_trace[-2].status)
        self.assertEqual("reasoning", envelope.pipeline_trace[-1].step)
        self.assertEqual("approved", envelope.pipeline_trace[-1].status)

    def test_low_confidence_entry_is_blocked_before_risk(self):
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.2,
            stop_loss=97.0,
            take_profit=109.0,
            reasons=("weak_edge",),
        )
        core = self._decision_core()
        envelope = core.evaluate(
            context=ActionContext.create("unit_test"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(decision),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=1.0,
            current_drawdown_pct=2.0,
        )

        self.assertEqual("blocked_low_confidence", envelope.status)
        self.assertIsNone(envelope.risk)
        self.assertIsNone(envelope.execution_plan)
        self.assertIn("confidence_below_min_entry", envelope.reasons)
        self.assertIsNotNone(envelope.reasoned_decision)
        self.assertFalse(envelope.reasoned_decision.approved)
        self.assertEqual("hold", envelope.reasoned_decision.action)
        self.assertIn("confidence_below_min_entry", envelope.reasoned_decision.constraints)
        self.assertEqual("confidence_gate", envelope.pipeline_trace[-2].step)
        self.assertEqual("blocked", envelope.pipeline_trace[-2].status)
        self.assertEqual("reasoning", envelope.pipeline_trace[-1].step)
        self.assertEqual("blocked", envelope.pipeline_trace[-1].status)

    def test_risk_blocked_decision_carries_explainable_record(self):
        decision = StrategyDecision(
            action="enter_short",
            confidence=0.75,
            stop_loss=104.0,
            take_profit=95.0,
            reasons=("range_breakdown",),
        )
        core = self._decision_core()
        context = ActionContext.create("unit_test")
        envelope = core.evaluate(
            context=context,
            state=self._state(),
            strategy_id="mean_reversion_v1",
            strategy=_StaticStrategy(decision),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=1.0,
            current_drawdown_pct=12.0,
        )
        record = core.to_decision_record(envelope, context)

        self.assertEqual("blocked_risk", envelope.status)
        self.assertIsNotNone(envelope.risk)
        self.assertFalse(envelope.risk.approved)
        self.assertEqual("drawdown_limit_reached", envelope.risk.reason)
        self.assertEqual(envelope.trace_id, record.trace_id)
        self.assertEqual("drawdown_limit_reached", record.risk_reason)
        self.assertFalse(record.risk_approved)
        self.assertTrue(record.pipeline_trace)
        self.assertIsNotNone(record.reasoned_decision)
        self.assertFalse(record.reasoned_decision.approved)
        self.assertEqual("hold", record.reasoned_decision.action)
        self.assertIn("drawdown_limit_reached", record.reasoned_decision.explanation)
        self.assertEqual("risk_gate", record.pipeline_trace[-3].step)
        self.assertEqual("reasoning", record.pipeline_trace[-1].step)


if __name__ == "__main__":
    unittest.main()

