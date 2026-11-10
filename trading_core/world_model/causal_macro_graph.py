from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from trading_core.intelligence.causal_graph import CausalEdge, CausalGraph, CausalInferenceResult, default_macro_causal_graph
from trading_core.intelligence.uncertainty import UncertaintyAggregator, UncertaintyComponent, UncertaintyProfile


@dataclass(frozen=True)
class MacroCausalEvidence:
    rates_pressure: float = 0.5
    liquidity: float = 0.5
    risk_sentiment: float = 0.5
    inflation_pressure: float = 0.5
    dollar_strength: float = 0.5
    source: str = "macro"

    def to_graph_evidence(self) -> dict[str, float]:
        return {
            "rates": _clamp(self.rates_pressure),
            "liquidity": _clamp(self.liquidity),
            "risk_sentiment": _clamp(self.risk_sentiment),
            "inflation": _clamp(self.inflation_pressure),
            "dollar_strength": _clamp(self.dollar_strength),
        }


@dataclass(frozen=True)
class MacroCausalState:
    risk_state: str
    trend_state: str
    volatility_state: str
    beliefs: dict[str, float]
    deltas: dict[str, float]
    uncertainty: UncertaintyProfile
    active_paths: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "risk_state": self.risk_state,
            "trend_state": self.trend_state,
            "volatility_state": self.volatility_state,
            "beliefs": {key: round(float(value), 6) for key, value in sorted(self.beliefs.items())},
            "deltas": {key: round(float(value), 6) for key, value in sorted(self.deltas.items())},
            "uncertainty": self.uncertainty.to_dict(),
            "active_paths": tuple(self.active_paths),
            "reasons": tuple(self.reasons),
        }


class MacroCausalGraph:
    def __init__(self, graph: CausalGraph | None = None):
        self.graph = graph or self._default_graph()

    def infer(self, evidence: MacroCausalEvidence, *, steps: int = 3) -> MacroCausalState:
        graph_evidence = evidence.to_graph_evidence()
        result = self.graph.infer(graph_evidence, steps=steps)
        uncertainty = self._uncertainty(result)
        beliefs = result.beliefs
        risk = beliefs.get("crypto_risk", 0.5)
        trend = beliefs.get("btc_trend", 0.5)
        volatility = beliefs.get("volatility", 0.5)
        reasons = (
            f"macro_source={evidence.source}",
            f"risk_belief={risk:.4f}",
            f"trend_belief={trend:.4f}",
            f"volatility_belief={volatility:.4f}",
            *result.reasons,
            *uncertainty.reasons,
        )
        return MacroCausalState(
            risk_state=_state_label(risk, "risk_on", "risk_off"),
            trend_state=_state_label(trend, "trend_up", "trend_down"),
            volatility_state=_state_label(volatility, "high", "low"),
            beliefs=beliefs,
            deltas=result.deltas,
            uncertainty=uncertainty,
            active_paths=tuple(path.to_dict() for path in result.active_paths),
            reasons=reasons,
        )

    @staticmethod
    def _default_graph() -> CausalGraph:
        graph = default_macro_causal_graph()
        graph = graph.add_edge(CausalEdge("inflation", "rates", 0.60, "positive", 0.75))
        graph = graph.add_edge(CausalEdge("dollar_strength", "liquidity", 0.45, "negative", 0.70))
        graph = graph.add_edge(CausalEdge("dollar_strength", "crypto_risk", 0.45, "negative", 0.65))
        return graph

    @staticmethod
    def _uncertainty(result: CausalInferenceResult) -> UncertaintyProfile:
        active = len(result.active_paths)
        max_delta = max((abs(delta) for delta in result.deltas.values()), default=0.0)
        sparse_paths = 1.0 if active <= 0 else min(1.0, 1.0 / active)
        weak_signal = 1.0 - min(1.0, max_delta * 4.0)
        components = (
            UncertaintyComponent("causal_path_sparsity", sparse_paths, 0.7, "few_active_macro_paths"),
            UncertaintyComponent("causal_signal_strength", weak_signal, 0.6, "small_belief_delta"),
        )
        return UncertaintyAggregator().combine(
            components,
            epistemic=sparse_paths,
            aleatoric=weak_signal,
            conflict=0.0,
            data_quality=sparse_paths,
        )


def default_macro_world_model() -> MacroCausalGraph:
    return MacroCausalGraph()


def _state_label(value: float, high_label: str, low_label: str) -> str:
    if value >= 0.52:
        return high_label
    if value <= 0.48:
        return low_label
    return "neutral"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
