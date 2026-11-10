from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CognitiveNode:
    node_id: str
    role: str
    capabilities: tuple[str, ...]
    load: float = 0.0
    healthy: bool = True


@dataclass(frozen=True)
class RoutingPlan:
    task: str
    node_id: str
    reason: str
    fallback_node_id: str = ""


class DistributedCognitionRouter:
    def route(self, task: str, required_capability: str, nodes: Iterable[CognitiveNode]) -> RoutingPlan:
        healthy = [node for node in nodes if node.healthy and required_capability in set(node.capabilities)]
        if not healthy:
            return RoutingPlan(task, "", "no_healthy_capable_node")
        healthy.sort(key=lambda node: (float(node.load), node.node_id))
        primary = healthy[0]
        fallback = healthy[1].node_id if len(healthy) > 1 else ""
        return RoutingPlan(task, primary.node_id, f"capability_match={required_capability}", fallback)
