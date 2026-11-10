import os
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.governance.charter import default_governance_charter
from trading_core.intelligence.hierarchy import HierarchicalDecisionSystem, LayerDecision
from trading_core.intelligence.resource_allocation import AdaptiveResourceAllocator
from trading_core.intelligence.world_model import CausalAssumption, MarketWorldModel
from trading_core.knowledge.graph import GraphEdge, GraphNode, InMemoryKnowledgeGraph
from trading_core.lab.distributed import CognitiveNode, DistributedCognitionRouter
from trading_core.lab.strategy_lab import StrategyLaboratory
from trading_core.research.autonomous import ResearchClaim, ResearchSynthesizer, SourceAssessment
from trading_core.research.fusion import FusionInput, MultiSourceFusion


class Year2FoundationsTests(unittest.TestCase):
    def test_hierarchy_prioritizes_reflex_exit(self):
        decision = HierarchicalDecisionSystem().decide(
            [
                LayerDecision("strategic", "enter_long", True, 0.8, ("strategic_buy",)),
                LayerDecision("reflex", "exit", True, 1.0, ("stop_loss_hit",)),
            ]
        )

        self.assertEqual("exit", decision.final_action)
        self.assertEqual("reflex", decision.controlling_layer)

    def test_world_model_projects_causal_impacts(self):
        model = MarketWorldModel(
            [
                CausalAssumption("rates", "crypto_risk", "inverse", strength=0.8, confidence=0.9),
                CausalAssumption("liquidity", "crypto_risk", "positive", strength=0.5, confidence=0.8),
            ]
        )

        projection = model.project("tightening", {"rates": 1.0, "liquidity": -1.0})

        self.assertLess(projection.impacts["crypto_risk"], 0)
        self.assertGreater(projection.confidence, 0.0)

    def test_research_synthesizer_scores_contradictions(self):
        now = time.time()
        synthesis = ResearchSynthesizer().synthesize(
            "BTC liquidity improving",
            [
                ResearchClaim("exchange depth rising", "support", SourceAssessment("exchange", 0.9, now), 0.8),
                ResearchClaim("stablecoin outflow", "contradict", SourceAssessment("macro", 0.8, now), 0.6),
            ],
            now=now,
        )

        self.assertGreater(synthesis.support_score, 0)
        self.assertGreater(synthesis.contradiction_score, 0)
        self.assertIn("exchange", synthesis.sources)

    def test_multi_source_fusion_finds_dominant_signal(self):
        assessment = MultiSourceFusion().assess(
            [
                FusionInput("market", "risk_on", 0.8, 1.0),
                FusionInput("macro", "risk_off", 0.6, 0.5),
                FusionInput("onchain", "risk_on", 0.7, 0.8),
            ]
        )

        self.assertEqual("risk_on", assessment.dominant_signal)
        self.assertGreater(assessment.confidence, 0.5)

    def test_strategy_lab_penalizes_overfit_and_selects_tournament(self):
        lab = StrategyLaboratory(seed=1)
        variant = lab.mutate("trend", {"rsi": 50, "window": 20})
        weak = lab.evaluate(variant, train_score=1.0, validation_score=0.6)
        strong = lab.evaluate(lab.mutate("trend", {"rsi": 55}), train_score=0.7, validation_score=0.65)

        winners = lab.tournament([weak, strong])

        self.assertFalse(weak.accepted)
        self.assertTrue(strong.accepted)
        self.assertEqual(strong.variant.variant_id, winners[0].variant.variant_id)

    def test_distributed_router_chooses_lowest_load_capable_node(self):
        plan = DistributedCognitionRouter().route(
            "simulate",
            "simulation",
            [
                CognitiveNode("a", "simulation", ("simulation",), load=0.9),
                CognitiveNode("b", "simulation", ("simulation",), load=0.1),
            ],
        )

        self.assertEqual("b", plan.node_id)
        self.assertEqual("a", plan.fallback_node_id)

    def test_adaptive_resource_allocator_reduces_compute_when_safe(self):
        budget = AdaptiveResourceAllocator().allocate(runtime_mode="safe", market_volatility="high", queue_depth=0)

        self.assertEqual("minimal", budget.reasoning_depth)
        self.assertFalse(budget.enable_expensive_models)

    def test_knowledge_graph_and_charter_contracts(self):
        graph = InMemoryKnowledgeGraph()
        graph.add_node(GraphNode("rates", "macro"))
        graph.add_node(GraphNode("BTC", "asset"))
        graph.add_edge(GraphEdge("rates", "BTC", "pressures", weight=-0.8, confidence=0.9))
        charter = default_governance_charter()

        self.assertLess(graph.influence_score("rates", "BTC"), 0)
        self.assertTrue(charter.requires_block("runtime_safe"))
        self.assertGreaterEqual(len(charter.principles), 4)


if __name__ == "__main__":
    unittest.main()
