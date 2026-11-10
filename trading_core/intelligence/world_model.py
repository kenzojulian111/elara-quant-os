from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from trading_core.intelligence.causal_graph import CausalEdge, CausalGraph, CausalNode, CausalInferenceResult


@dataclass(frozen=True)
class CausalAssumption:
    source: str
    target: str
    relationship: str
    strength: float = 0.5
    confidence: float = 0.5


@dataclass(frozen=True)
class ScenarioProjection:
    scenario: str
    impacts: dict[str, float]
    confidence: float
    assumptions: tuple[CausalAssumption, ...]
    causal_result: CausalInferenceResult | None = None


class MarketWorldModel:
    def __init__(self, assumptions: Iterable[CausalAssumption] = ()):
        self.assumptions = tuple(assumptions or ())
        self.graph = self._build_graph(self.assumptions)

    def project(self, scenario: str, shocks: dict[str, float]) -> ScenarioProjection:
        impacts: dict[str, float] = {}
        used: list[CausalAssumption] = []
        for assumption in self.assumptions:
            if assumption.source not in shocks:
                continue
            delta = float(shocks[assumption.source])
            direction = -1.0 if assumption.relationship in {"inverse", "negative"} else 1.0
            impacts[assumption.target] = impacts.get(assumption.target, 0.0) + delta * float(assumption.strength) * direction
            used.append(assumption)
        evidence = {
            key: self._shock_to_probability(value)
            for key, value in shocks.items()
            if key in self.graph.nodes
        }
        causal_result = self.graph.infer(evidence) if evidence else None
        if causal_result is not None:
            for key, delta in causal_result.deltas.items():
                if key in shocks:
                    continue
                impacts.setdefault(key, float(delta))
        confidence = sum(float(item.confidence) for item in used) / len(used) if used else 0.0
        return ScenarioProjection(
            scenario=str(scenario or "scenario"),
            impacts=impacts,
            confidence=confidence,
            assumptions=tuple(used),
            causal_result=causal_result,
        )

    def add(self, assumption: CausalAssumption) -> "MarketWorldModel":
        return MarketWorldModel((*self.assumptions, assumption))

    @staticmethod
    def _shock_to_probability(value: float) -> float:
        return max(0.0, min(1.0, 0.5 + float(value) * 0.5))

    @staticmethod
    def _build_graph(assumptions: tuple[CausalAssumption, ...]) -> CausalGraph:
        node_names = sorted({name for item in assumptions for name in (item.source, item.target)})
        nodes = tuple(CausalNode(name) for name in node_names)
        edges = tuple(
            CausalEdge(
                source=item.source,
                target=item.target,
                weight=float(item.strength),
                relationship=item.relationship,
                confidence=float(item.confidence),
            )
            for item in assumptions
        )
        return CausalGraph(nodes, edges)
