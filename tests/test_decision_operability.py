import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.coordination import decision_lifecycle_events
from trading_core.coordination.action_context import ActionContext
from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision
from trading_core.intelligence import build_decision_narrative
from trading_core.orchestration.decision_core import DecisionCore, DecisionCoreConfig
from trading_core.orchestration.live_pipeline import LivePipelineConfig, LiveTradingPipeline
from trading_core.roadmap import ArchitectureHeatmapBuilder, build_decision_topology
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


class DecisionOperabilityTests(unittest.TestCase):
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

    def test_decision_topology_renders_required_cognition_flow(self):
        topology = build_decision_topology()
        mermaid = topology.to_mermaid()

        self.assertIn("flowchart LR", mermaid)
        self.assertIn("strategy", {node.node_id for node in topology.nodes})
        self.assertIn("reasoning", {node.node_id for node in topology.nodes})
        self.assertIn("runtime -->|depends on| governance", mermaid)
        self.assertIn("reasoning -->|final explainable decision| envelope", mermaid)

    def test_decision_narrative_is_human_readable(self):
        envelope = self._ready_envelope()

        narrative = build_decision_narrative(envelope)
        text = narrative.to_text()

        self.assertIn("APPROVED ENTER_LONG for BTC/USDT", text)
        self.assertIn("Probability:", text)
        self.assertIn("Risk: approved", text)
        self.assertIn("Timeline:", text)
        self.assertIn("reasoning -> approved", text)

    def test_decision_lifecycle_events_project_replay_timeline(self):
        envelope = self._ready_envelope()

        events = decision_lifecycle_events(envelope)
        event_types = tuple(event.event_type for event in events)

        self.assertEqual(tuple(range(1, len(events) + 1)), tuple(event.sequence for event in events))
        self.assertIn("SIGNAL_RECEIVED", event_types)
        self.assertIn("RISK_APPROVED", event_types)
        self.assertIn("DECISION_APPROVED", event_types)
        self.assertIn("REASONING_SYNTHESIZED", event_types)
        self.assertIn("EXECUTION_APPROVED", event_types)
        self.assertTrue(all(event.trace_id == envelope.trace_id for event in events))

    def test_architecture_heatmap_surfaces_operability_hotspots(self):
        heatmap = ArchitectureHeatmapBuilder(ROOT).build(limit=8)
        markdown = heatmap.to_markdown(limit=4)

        self.assertGreater(heatmap.scanned_files, 0)
        self.assertTrue(heatmap.hotspots)
        self.assertIn("# Architecture Heatmap", markdown)
        self.assertTrue(any("decision" in hotspot.file_path for hotspot in heatmap.hotspots))


if __name__ == "__main__":
    unittest.main()
