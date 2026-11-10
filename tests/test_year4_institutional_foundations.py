from __future__ import annotations

import unittest
from pathlib import Path

from trading_core.cognition import CognitiveOpinion, MultiAgentValidator
from trading_core.coordination import (
    DecisionProjector,
    EventRecord,
    InMemoryExternalBusAdapter,
    SnapshotReplayEngine,
)
from trading_core.execution import SmartExecutionRouter, VenueQuote
from trading_core.governance import GovernanceEngineV2, GovernanceV2Context
from trading_core.portfolio import InstitutionalExposure, InstitutionalPortfolioCognition
from trading_core.research import ReplayNativeResearchEvaluator, ResearchCandidate
from trading_core.roadmap import Year34ReadinessChecker
from trading_core.runtime import RustRuntimeGateway
from trading_core.simulation import BlackSwanScenario, BlackSwanSimulator, LatencyLab, LatencyStage
from trading_core.world_model import CapitalFlowObservation, CapitalFlowSimulator, default_regime_graph


class Year4InstitutionalFoundationTests(unittest.TestCase):
    def test_external_bus_cqrs_and_snapshot_replay(self) -> None:
        bus = InMemoryExternalBusAdapter()
        bus.publish("SIGNAL_RECEIVED", {"symbol": "BTCUSDT"})
        projector = DecisionProjector()
        records = (
            EventRecord(event_id="1", event_type="SIGNAL_RECEIVED", payload={}, trace_id="trace-1", sequence=1),
            EventRecord(event_id="2", event_type="EXECUTION_APPROVED", payload={}, trace_id="trace-1", sequence=2),
        )
        model = projector.replay(records)[0]
        snapshot = SnapshotReplayEngine().capture(sequence=0, state={"runtime": "green"})
        restored = SnapshotReplayEngine().restore(snapshot, records)
        self.assertEqual(bus.depth, 1)
        self.assertEqual(model.status, "approved")
        self.assertEqual(restored.applied_events, 2)
        self.assertEqual(restored.state["last_event"], "EXECUTION_APPROVED")

    def test_world_portfolio_and_governance_pipeline(self) -> None:
        flow = CapitalFlowSimulator().infer(
            (CapitalFlowObservation("wallet", "exchange-binance", "BTC", 1_000_000),)
        )
        regime = default_regime_graph().infer("risk_off")
        portfolio = InstitutionalPortfolioCognition().assess(
            (InstitutionalExposure("BTCUSDT", 10_000),),
            stress_loss_by_symbol={"BTCUSDT": 0.30},
        )
        decision = GovernanceEngineV2().evaluate(
            GovernanceV2Context(
                runtime_mode="yellow",
                uncertainty=0.20,
                portfolio_drawdown_probability=portfolio.drawdown_probability,
                signed_artifact=True,
            )
        )
        self.assertEqual(flow.risk_state, "risk_off")
        self.assertEqual(regime.likely_next, "risk_off")
        self.assertTrue(portfolio.hedge_required)
        self.assertFalse(decision.allowed)

    def test_runtime_routing_and_cognition_validation(self) -> None:
        handshake = RustRuntimeGateway().negotiate(
            protocol_version=1,
            capabilities=("event_stream_v1", "replay_executor_v1"),
        )
        route = SmartExecutionRouter().route(
            quantity=2,
            quotes=(
                VenueQuote("slow", 10, 4, 100),
                VenueQuote("fast", 10, 5, 10),
            ),
        )
        validation = MultiAgentValidator().validate(
            (
                CognitiveOpinion("risk", "HOLD", 0.9, 2),
                CognitiveOpinion("strategy", "BUY", 0.5),
            )
        )
        self.assertTrue(handshake.compatible)
        self.assertEqual(route.venue, "fast")
        self.assertEqual(validation.decision, "HOLD")
        self.assertTrue(validation.approved)

    def test_black_swan_latency_and_research_remain_bounded(self) -> None:
        impact = BlackSwanSimulator().run(BlackSwanScenario("liquidation_cascade", 1.0))
        latency = LatencyLab().assess((LatencyStage("decision", 160, 120),))
        research = ReplayNativeResearchEvaluator().evaluate(ResearchCandidate("candidate-a", 0.20, 0.10, 50))
        self.assertFalse(impact.execution_allowed)
        self.assertTrue(latency.degrade_required)
        self.assertTrue(research.promotable)
        self.assertFalse(research.auto_promoted)

    def test_year34_repository_baseline_exceeds_target(self) -> None:
        root = Path(__file__).resolve().parent.parent
        report = Year34ReadinessChecker(root).build_report()
        self.assertGreaterEqual(report.score, 90)
        self.assertTrue(report.baseline_ready)
        self.assertTrue(report.production_gaps)


if __name__ == "__main__":
    unittest.main()
