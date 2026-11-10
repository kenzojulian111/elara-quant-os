from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class UncertaintyComponent:
    name: str
    value: float
    weight: float = 1.0
    reason: str = ""

    def normalized(self) -> "UncertaintyComponent":
        return UncertaintyComponent(
            name=str(self.name or "unknown"),
            value=_clamp(float(self.value)),
            weight=max(0.0, float(self.weight)),
            reason=str(self.reason or ""),
        )

    def to_dict(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "value": round(float(self.value), 6),
            "weight": round(float(self.weight), 6),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UncertaintyProfile:
    total: float
    epistemic: float = 0.0
    aleatoric: float = 0.0
    conflict: float = 0.0
    data_quality: float = 0.0
    components: tuple[UncertaintyComponent, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "total": round(float(self.total), 6),
            "epistemic": round(float(self.epistemic), 6),
            "aleatoric": round(float(self.aleatoric), 6),
            "conflict": round(float(self.conflict), 6),
            "data_quality": round(float(self.data_quality), 6),
            "components": tuple(component.to_dict() for component in self.components),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class UncertaintyConfig:
    floor: float = 0.03
    conflict_weight: float = 0.35
    epistemic_weight: float = 0.35
    aleatoric_weight: float = 0.20
    data_quality_weight: float = 0.10


class UncertaintyAggregator:
    def __init__(self, config: UncertaintyConfig | None = None):
        self.config = config or UncertaintyConfig()

    def combine(
        self,
        components: tuple[UncertaintyComponent, ...],
        *,
        epistemic: float = 0.0,
        aleatoric: float = 0.0,
        conflict: float = 0.0,
        data_quality: float = 0.0,
    ) -> UncertaintyProfile:
        normalized = tuple(component.normalized() for component in components)
        weighted_total = 0.0
        total_weight = 0.0
        for component in normalized:
            weighted_total += float(component.value) * float(component.weight)
            total_weight += float(component.weight)
        component_total = weighted_total / total_weight if total_weight > 0 else 0.0
        blended = (
            component_total
            + _clamp(epistemic) * float(self.config.epistemic_weight)
            + _clamp(aleatoric) * float(self.config.aleatoric_weight)
            + _clamp(conflict) * float(self.config.conflict_weight)
            + _clamp(data_quality) * float(self.config.data_quality_weight)
        )
        total = max(float(self.config.floor), min(1.0, blended))
        reasons = tuple(
            reason
            for reason in (
                f"epistemic={_clamp(epistemic):.4f}",
                f"aleatoric={_clamp(aleatoric):.4f}",
                f"conflict={_clamp(conflict):.4f}",
                f"data_quality={_clamp(data_quality):.4f}",
                f"uncertainty_total={total:.4f}",
            )
            if reason
        )
        return UncertaintyProfile(
            total=total,
            epistemic=_clamp(epistemic),
            aleatoric=_clamp(aleatoric),
            conflict=_clamp(conflict),
            data_quality=_clamp(data_quality),
            components=normalized,
            reasons=reasons,
        )

    def from_bayesian(
        self,
        *,
        variance: float,
        effective_sample_size: float,
        conflict: float,
        evidence_count: int,
    ) -> UncertaintyProfile:
        epistemic = min(1.0, math.sqrt(max(0.0, float(variance))) * 2.0)
        data_quality = min(1.0, 1.0 / max(1.0, float(effective_sample_size)))
        sparse_evidence = 1.0 if int(evidence_count) <= 0 else min(1.0, 1.0 / float(evidence_count))
        components = (
            UncertaintyComponent("posterior_variance", epistemic, 1.0, "beta_posterior_variance"),
            UncertaintyComponent("sample_sparsity", sparse_evidence, 0.6, "limited_effective_evidence"),
            UncertaintyComponent("evidence_conflict", conflict, 0.9, "component_disagreement"),
        )
        return self.combine(
            components,
            epistemic=epistemic,
            aleatoric=sparse_evidence,
            conflict=conflict,
            data_quality=data_quality,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
