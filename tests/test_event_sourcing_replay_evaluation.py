import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination import DecisionEventSourcingWriter, replay_decision_events
from trading_core.coordination.event_stream import EventStream
from trading_core.coordination.action_context import ActionContext
from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision
from trading_core.evaluation import ReplayDecisionEvaluator
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


class EventSourcingReplayEvaluationTests(unittest.TestCase):
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

    def _ready_envelope(self):
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.82,
            stop_loss=97.0,
            take_profit=109.0,
            reasons=("trend_alignment", "momentum_confirmed"),
        )
        live_pipeline = LiveTradingPipeline(
            _DummyPortfolioBrain(),
            LivePipelineConfig(max_risk_per_trade_pct=0.02, max_drawdown_pct=10.0),
        )
        core = DecisionCore(
            live_pipeline,
            DecisionCoreConfig(risk_per_trade_pct=0.02, min_entry_confidence=0.5),
        )
        return core.evaluate(
            context=ActionContext.create("unit_test"),
            state=self._state(),
            strategy_id="trend_following_v1",
            strategy=_StaticStrategy(decision),
            indicators=self._indicators(),
            position=PositionState(),
            allocation_weight=2.0,
            current_drawdown_pct=4.0,
        )

    def test_decision_event_sourcing_writes_replayable_lifecycle(self):
        envelope = self._ready_envelope()
        with tempfile.TemporaryDirectory(prefix="elara_event_sourcing_") as tmpdir:
            stream = EventStream(os.path.join(tmpdir, "events.jsonl"), stream="decision_lifecycle")
            result = DecisionEventSourcingWriter(stream).append_envelope(envelope)
            records = replay_decision_events(stream, trace_id=envelope.trace_id)
            report = ReplayDecisionEvaluator().evaluate_records(records)
            ok, reason = stream.verify_hash_chain()

        self.assertTrue(ok, reason)
        self.assertGreater(result.written, 0)
        self.assertEqual("ok", report.status, report.to_dict())
        self.assertEqual((envelope.trace_id,), report.trace_ids)
        self.assertIn("REASONING_SYNTHESIZED", [record.event_type for record in records])

    def test_replay_evaluator_flags_missing_required_event(self):
        with tempfile.TemporaryDirectory(prefix="elara_event_eval_bad_") as tmpdir:
            stream = EventStream(os.path.join(tmpdir, "events.jsonl"), stream="decision_lifecycle")
            stream.append("DECISION_APPROVED", {"step": "decision"}, stream="decision_lifecycle", trace_id="trace-1")
            report = ReplayDecisionEvaluator().evaluate_records(tuple(stream.read_all()))

        self.assertEqual("critical", report.status)
        self.assertGreater(report.critical_count, 0)


if __name__ == "__main__":
    unittest.main()
