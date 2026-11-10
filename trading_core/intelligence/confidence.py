from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from trading_core.data_contracts import ReasoningComponent


@dataclass(frozen=True)
class ConfidenceContribution:
    source: str
    input_confidence: float
    propagated_confidence: float
    weight: float
    verdict: str
    uncertainty_contribution: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "source": self.source,
            "input_confidence": round(float(self.input_confidence), 6),
            "propagated_confidence": round(float(self.propagated_confidence), 6),
            "weight": round(float(self.weight), 6),
            "verdict": self.verdict,
            "uncertainty_contribution": round(float(self.uncertainty_contribution), 6),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class ConfidencePropagation:
    base_confidence: float
    propagated_confidence: float
    confidence_spread: float
    conflict: float
    uncertainty: float
    contributions: tuple[ConfidenceContribution, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "base_confidence": round(float(self.base_confidence), 6),
            "propagated_confidence": round(float(self.propagated_confidence), 6),
            "confidence_spread": round(float(self.confidence_spread), 6),
            "conflict": round(float(self.conflict), 6),
            "uncertainty": round(float(self.uncertainty), 6),
            "contributions": tuple(item.to_dict() for item in self.contributions),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class ConfidencePropagationConfig:
    block_multiplier: float = 0.12
    degrade_multiplier: float = 0.55
    neutral_multiplier: float = 0.85
    conflict_weight: float = 0.45
    spread_weight: float = 0.35
    missing_component_uncertainty: float = 0.35


class ConfidencePropagationEngine:
    def __init__(self, config: ConfidencePropagationConfig | None = None):
        self.config = config or ConfidencePropagationConfig()

    def propagate(
        self,
        components: Iterable[ReasoningComponent],
        *,
        base_confidence: float,
    ) -> ConfidencePropagation:
        component_tuple = tuple(components)
        normalized_base = _clamp(base_confidence)
        if not component_tuple:
            uncertainty = max(0.0, min(1.0, float(self.config.missing_component_uncertainty)))
            return ConfidencePropagation(
                base_confidence=normalized_base,
                propagated_confidence=normalized_base,
                confidence_spread=0.0,
                conflict=0.0,
                uncertainty=uncertainty,
                reasons=("confidence_no_components", f"confidence_uncertainty={uncertainty:.4f}"),
            )

        contributions = tuple(self._contribution(component) for component in component_tuple)
        total_weight = sum(float(item.weight) for item in contributions)
        if total_weight <= 0:
            propagated = normalized_base
        else:
            propagated = sum(float(item.propagated_confidence) * float(item.weight) for item in contributions) / total_weight
        spread = _spread(tuple(item.propagated_confidence for item in contributions))
        conflict = _conflict(contributions, propagated)
        uncertainty = _clamp(
            spread * float(self.config.spread_weight)
            + conflict * float(self.config.conflict_weight)
            + max(0.0, 1.0 - total_weight) * 0.05
        )
        reasons = (
            f"propagated_confidence={propagated:.4f}",
            f"confidence_spread={spread:.4f}",
            f"confidence_conflict={conflict:.4f}",
            f"confidence_uncertainty={uncertainty:.4f}",
        )
        return ConfidencePropagation(
            base_confidence=normalized_base,
            propagated_confidence=_clamp(propagated),
            confidence_spread=spread,
            conflict=conflict,
            uncertainty=uncertainty,
            contributions=contributions,
            reasons=reasons,
        )

    def _contribution(self, component: ReasoningComponent) -> ConfidenceContribution:
        confidence = _clamp(component.confidence)
        verdict = str(component.verdict or "neutral").strip().lower()
        multiplier = 1.0
        if verdict == "block":
            multiplier = float(self.config.block_multiplier)
        elif verdict == "degrade":
            multiplier = float(self.config.degrade_multiplier)
        elif verdict == "neutral":
            multiplier = float(self.config.neutral_multiplier)
        propagated = _clamp(confidence * multiplier)
        uncertainty = _clamp(abs(confidence - propagated) + (0.5 - abs(propagated - 0.5)) * 0.25)
        return ConfidenceContribution(
            source=component.source,
            input_confidence=confidence,
            propagated_confidence=propagated,
            weight=max(0.0, float(component.weight)),
            verdict=verdict,
            uncertainty_contribution=uncertainty,
            reasons=tuple(component.reasons),
        )


def _spread(values: tuple[float, ...]) -> float:
    if len(values) <= 1:
        return 0.0
    avg = sum(values) / float(len(values))
    variance = sum((value - avg) ** 2 for value in values) / float(len(values))
    return _clamp(variance ** 0.5 * 2.0)


def _conflict(contributions: tuple[ConfidenceContribution, ...], propagated: float) -> float:
    weighted = 0.0
    total = 0.0
    for item in contributions:
        weight = max(0.0, float(item.weight))
        if weight <= 0:
            continue
        weighted += abs(float(item.propagated_confidence) - float(propagated)) * weight
        total += weight
    if total <= 0:
        return 0.0
    return _clamp(weighted / total * 2.0)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
