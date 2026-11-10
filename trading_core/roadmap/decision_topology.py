from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from trading_core.roadmap.cognitive_coherence import CognitiveLayerContract, default_cognitive_layers


@dataclass(frozen=True)
class TopologyNode:
    node_id: str
    label: str
    owner_module: str
    responsibility: str
    required: bool = True

    def to_dict(self) -> Mapping[str, object]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "owner_module": self.owner_module,
            "responsibility": self.responsibility,
            "required": bool(self.required),
        }


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    label: str = ""

    def to_dict(self) -> Mapping[str, object]:
        return {"source": self.source, "target": self.target, "label": self.label}


@dataclass(frozen=True)
class DecisionTopology:
    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]

    def to_dict(self) -> Mapping[str, object]:
        return {
            "nodes": tuple(node.to_dict() for node in self.nodes),
            "edges": tuple(edge.to_dict() for edge in self.edges),
        }

    def to_mermaid(self) -> str:
        lines = [
            "flowchart LR",
            '  market["MarketState + Indicators"]',
            '  context["ActionContext + Config Snapshot"]',
        ]
        for node in self.nodes:
            lines.append(f'  {node.node_id}["{_escape(node.label)}"]')
        lines.append('  envelope["DecisionEnvelope + Event Ledger"]')
        for edge in self.edges:
            label = f'|{_escape(edge.label)}|' if edge.label else ""
            lines.append(f"  {edge.source} -->{label} {edge.target}")
        required = ",".join(node.node_id for node in self.nodes if node.required)
        optional = ",".join(node.node_id for node in self.nodes if not node.required)
        if required:
            lines.append("  classDef required fill:#e8f7ef,stroke:#1f7a4d,color:#103322;")
            lines.append(f"  class {required} required;")
        if optional:
            lines.append("  classDef optional fill:#f5f7fb,stroke:#64748b,color:#1f2937;")
            lines.append(f"  class {optional} optional;")
        return "\n".join(lines) + "\n"


def build_decision_topology(
    layers: Iterable[CognitiveLayerContract] | None = None,
) -> DecisionTopology:
    layer_tuple = tuple(layers or default_cognitive_layers())
    nodes = tuple(
        TopologyNode(
            node_id=_node_id(layer.name),
            label=f"{layer.name}: {layer.trace_step or layer.event_type or 'contract'}",
            owner_module=layer.owner_module,
            responsibility=layer.responsibility,
            required=layer.required,
        )
        for layer in layer_tuple
    )
    layer_names = {layer.name for layer in layer_tuple}
    edges: list[TopologyEdge] = [
        TopologyEdge("market", "strategy", "price + indicators"),
        TopologyEdge("market", "portfolio", "allocation context"),
        TopologyEdge("context", "runtime", "health + dependency state"),
        TopologyEdge("context", "objective", "operator intent"),
    ]
    for layer in layer_tuple:
        for dependency in layer.depends_on:
            if dependency in layer_names:
                edges.append(TopologyEdge(_node_id(dependency), _node_id(layer.name), "depends on"))
    edges.append(TopologyEdge("reasoning", "envelope", "final explainable decision"))
    return DecisionTopology(nodes=nodes, edges=_dedupe_edges(edges))


def _dedupe_edges(edges: Iterable[TopologyEdge]) -> tuple[TopologyEdge, ...]:
    seen: set[tuple[str, str, str]] = set()
    out: list[TopologyEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.label)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return tuple(out)


def _node_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip().lower()) or "node"


def _escape(value: str) -> str:
    return str(value or "").replace('"', "'")
