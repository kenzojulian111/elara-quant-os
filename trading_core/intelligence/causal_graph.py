from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class CausalNode:
    name: str
    prior: float = 0.5
    description: str = ""


@dataclass(frozen=True)
class CausalEdge:
    source: str
    target: str
    weight: float
    relationship: str = "positive"
    confidence: float = 1.0

    @property
    def signed_weight(self) -> float:
        direction = -1.0 if self.relationship in {"inverse", "negative", "opposes"} else 1.0
        return direction * max(-1.0, min(1.0, float(self.weight))) * max(0.0, min(1.0, float(self.confidence)))


@dataclass(frozen=True)
class CausalPathActivation:
    source: str
    target: str
    delta: float
    relationship: str
    weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "delta": round(float(self.delta), 6),
            "relationship": self.relationship,
            "weight": round(float(self.weight), 6),
        }


@dataclass(frozen=True)
class CausalInferenceResult:
    beliefs: dict[str, float]
    deltas: dict[str, float]
    active_paths: tuple[CausalPathActivation, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "beliefs": {key: round(float(value), 6) for key, value in sorted(self.beliefs.items())},
            "deltas": {key: round(float(value), 6) for key, value in sorted(self.deltas.items())},
            "active_paths": tuple(path.to_dict() for path in self.active_paths),
            "reasons": tuple(self.reasons),
        }


class CausalGraph:
    def __init__(self, nodes: Iterable[CausalNode] = (), edges: Iterable[CausalEdge] = ()):
        self.nodes = {node.name: node for node in nodes}
        self.edges = tuple(edges or ())
        for edge in self.edges:
            self.nodes.setdefault(edge.source, CausalNode(edge.source))
            self.nodes.setdefault(edge.target, CausalNode(edge.target))

    def infer(
        self,
        evidence: dict[str, float],
        *,
        steps: int = 2,
        damping: float = 0.75,
        activation_threshold: float = 0.02,
    ) -> CausalInferenceResult:
        beliefs = {name: _clamp(node.prior) for name, node in self.nodes.items()}
        priors = dict(beliefs)
        for name, value in evidence.items():
            if name in beliefs:
                beliefs[name] = _clamp(float(value))

        active_paths: list[CausalPathActivation] = []
        for _ in range(max(1, int(steps))):
            logit_updates = {name: _logit(value) for name, value in beliefs.items()}
            for edge in self.edges:
                source_delta = beliefs.get(edge.source, 0.5) - priors.get(edge.source, 0.5)
                propagated = float(damping) * source_delta * edge.signed_weight
                if abs(propagated) < activation_threshold:
                    continue
                logit_updates[edge.target] = logit_updates.get(edge.target, _logit(priors.get(edge.target, 0.5))) + propagated
                active_paths.append(
                    CausalPathActivation(
                        source=edge.source,
                        target=edge.target,
                        delta=propagated,
                        relationship=edge.relationship,
                        weight=edge.signed_weight,
                    )
                )
            beliefs = {name: _sigmoid(value) for name, value in logit_updates.items()}
            for name, value in evidence.items():
                if name in beliefs:
                    beliefs[name] = _clamp(float(value))

        deltas = {name: beliefs[name] - priors.get(name, 0.5) for name in beliefs}
        reasons = (
            f"causal_nodes={len(self.nodes)}",
            f"causal_edges={len(self.edges)}",
            f"active_paths={len(active_paths)}",
        )
        return CausalInferenceResult(
            beliefs={key: _clamp(value) for key, value in beliefs.items()},
            deltas=deltas,
            active_paths=tuple(active_paths),
            reasons=reasons,
        )

    def add_node(self, node: CausalNode) -> "CausalGraph":
        return CausalGraph((*self.nodes.values(), node), self.edges)

    def add_edge(self, edge: CausalEdge) -> "CausalGraph":
        return CausalGraph(tuple(self.nodes.values()), (*self.edges, edge))


def default_macro_causal_graph() -> CausalGraph:
    nodes = (
        CausalNode("rates", 0.5, "Rate pressure"),
        CausalNode("liquidity", 0.5, "Market liquidity"),
        CausalNode("risk_sentiment", 0.5, "Risk appetite"),
        CausalNode("crypto_risk", 0.5, "Crypto risk-on state"),
        CausalNode("volatility", 0.5, "Volatility pressure"),
        CausalNode("btc_trend", 0.5, "BTC trend belief"),
    )
    edges = (
        CausalEdge("rates", "liquidity", 0.45, "negative", 0.8),
        CausalEdge("rates", "risk_sentiment", 0.55, "negative", 0.8),
        CausalEdge("liquidity", "crypto_risk", 0.65, "positive", 0.85),
        CausalEdge("risk_sentiment", "crypto_risk", 0.75, "positive", 0.9),
        CausalEdge("crypto_risk", "btc_trend", 0.70, "positive", 0.8),
        CausalEdge("liquidity", "volatility", 0.55, "negative", 0.7),
        CausalEdge("rates", "volatility", 0.50, "positive", 0.75),
    )
    return CausalGraph(nodes, edges)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _logit(value: float) -> float:
    clipped = min(0.999, max(0.001, float(value)))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)
