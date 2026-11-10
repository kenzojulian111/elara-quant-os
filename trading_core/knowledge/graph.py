from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    kind: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    weight: float = 1.0
    confidence: float = 1.0


class InMemoryKnowledgeGraph:
    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source not in self.nodes:
            self.add_node(GraphNode(edge.source, "unknown"))
        if edge.target not in self.nodes:
            self.add_node(GraphNode(edge.target, "unknown"))
        self.edges.append(edge)

    def neighbors(self, node_id: str, *, relation: str = "") -> tuple[GraphEdge, ...]:
        return tuple(
            edge
            for edge in self.edges
            if edge.source == node_id and (not relation or edge.relation == relation)
        )

    def influence_score(self, source: str, target: str) -> float:
        return sum(
            float(edge.weight) * float(edge.confidence)
            for edge in self.edges
            if edge.source == source and edge.target == target
        )
