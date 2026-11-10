import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.cognition import (
    AdaptiveComputeRouter,
    ComputeRouteRequest,
    MemoryNode,
    ReasoningNode,
    ReasoningNodeCapability,
)
from trading_core.intelligence import RuntimeCausalAnalyzer
from utils.health import RuntimeModeAssessment


class RuntimeCausalCognitionMeshTests(unittest.TestCase):
    def test_runtime_causal_analysis_surfaces_dominant_dependency(self):
        assessment = RuntimeModeAssessment(
            mode="safe",
            reasons=("core_dependency_down=exchange:down:market_access",),
            allow_new_entries=False,
            dependency_states={"exchange": "down"},
            dependency_impacts={"exchange": {"state": "down", "impact": "core_market_access"}},
        )

        report = RuntimeCausalAnalyzer().analyze(assessment)

        self.assertEqual("safe", report.runtime_mode)
        self.assertEqual("exchange:down", report.dominant_cause)
        self.assertFalse(report.allow_new_entries)
        self.assertEqual("high", report.findings[0].severity)

    def test_compute_router_selects_remote_node_when_healthy(self):
        node = ReasoningNode(
            "reasoner-a",
            "reasoning",
            capabilities=(ReasoningNodeCapability("probabilistic_reasoning"),),
        )
        request = ComputeRouteRequest(
            task="decision_validation",
            uncertainty=0.6,
            runtime_mode="full",
            required_capabilities=("probabilistic_reasoning",),
        )

        route = AdaptiveComputeRouter().route(request, (node.heartbeat(latency_ms=20.0),))

        self.assertTrue(route.accepted)
        self.assertEqual("reasoner-a", route.target)
        self.assertEqual("deep", route.mode)

    def test_compute_router_uses_local_minimal_in_safe_mode(self):
        request = ComputeRouteRequest(task="decision_validation", uncertainty=0.9, runtime_mode="safe")

        route = AdaptiveComputeRouter().route(request, ())

        self.assertTrue(route.accepted)
        self.assertEqual("local", route.target)
        self.assertEqual("minimal", route.mode)

    def test_memory_node_keeps_bounded_recent_context(self):
        memory = MemoryNode("memory-a", max_items=2)
        memory.append({"trace_id": "t1", "label": "first"})
        memory.append({"trace_id": "t2", "label": "second"})
        memory.append({"trace_id": "t3", "label": "third"})

        snapshot = memory.snapshot()

        self.assertEqual(2, snapshot.item_count)
        self.assertEqual("t3", snapshot.latest_trace_id)
        self.assertEqual(("second", "third"), snapshot.labels)


if __name__ == "__main__":
    unittest.main()
