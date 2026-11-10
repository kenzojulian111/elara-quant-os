from __future__ import annotations

from dataclasses import dataclass, field

from trading_core.data_contracts import ReasoningComponent
from trading_core.intelligence.uncertainty import UncertaintyAggregator, UncertaintyProfile


@dataclass(frozen=True)
class BayesianEvidence:
    source: str
    label: str
    probability: float
    reliability: float
    direction: str = "support"
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def normalized(self) -> "BayesianEvidence":
        return BayesianEvidence(
            source=str(self.source or "unknown"),
            label=str(self.label or ""),
            probability=_clamp(float(self.probability)),
            reliability=max(0.0, float(self.reliability)),
            direction=str(self.direction or "support").strip().lower(),
            reasons=tuple(str(reason) for reason in self.reasons if str(reason)),
        )


@dataclass(frozen=True)
class BayesianPosterior:
    prior: float
    probability: float
    variance: float
    uncertainty: float
    evidence_count: int
    effective_sample_size: float
    conflict: float
    alpha: float
    beta: float
    uncertainty_profile: UncertaintyProfile | None = None
    evidence: tuple[BayesianEvidence, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "prior": round(float(self.prior), 6),
            "probability": round(float(self.probability), 6),
            "variance": round(float(self.variance), 6),
            "uncertainty": round(float(self.uncertainty), 6),
            "evidence_count": int(self.evidence_count),
            "effective_sample_size": round(float(self.effective_sample_size), 6),
            "conflict": round(float(self.conflict), 6),
            "alpha": round(float(self.alpha), 6),
            "beta": round(float(self.beta), 6),
            "uncertainty_profile": self.uncertainty_profile.to_dict() if self.uncertainty_profile else {},
            "evidence": tuple(
                {
                    "source": item.source,
                    "label": item.label,
                    "probability": round(float(item.probability), 6),
                    "reliability": round(float(item.reliability), 6),
                    "direction": item.direction,
                    "reasons": tuple(item.reasons),
                }
                for item in self.evidence
            ),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class BayesianSignalConfig:
    prior_strength: float = 2.0
    min_reliability: float = 0.05
    max_reliability: float = 2.0
    conflict_weight: float = 0.35
    uncertainty_floor: float = 0.03


class BayesianSignalModel:
    def __init__(self, config: BayesianSignalConfig | None = None):
        self.config = config or BayesianSignalConfig()

    def infer(
        self,
        components: tuple[ReasoningComponent, ...],
        *,
        prior: float = 0.5,
    ) -> BayesianPosterior:
        normalized_prior = _clamp(float(prior))
        prior_strength = max(0.01, float(self.config.prior_strength))
        alpha = max(0.001, normalized_prior * prior_strength)
        beta = max(0.001, (1.0 - normalized_prior) * prior_strength)
        evidence = tuple(self.component_to_evidence(component) for component in components)
        evidence = tuple(item.normalized() for item in evidence if item.reliability > 0)

        for item in evidence:
            success, failure = self._soft_counts(item)
            alpha += success
            beta += failure

        total = alpha + beta
        probability = alpha / total if total > 0 else normalized_prior
        variance = (alpha * beta) / ((total * total) * (total + 1.0)) if total > 0 else 0.25
        conflict = self._conflict(evidence, probability)
        uncertainty_profile = UncertaintyAggregator().from_bayesian(
            variance=variance,
            effective_sample_size=total,
            conflict=conflict,
            evidence_count=len(evidence),
        )
        uncertainty = uncertainty_profile.total
        reasons = (
            f"bayesian_prior={normalized_prior:.4f}",
            f"bayesian_posterior={probability:.4f}",
            f"bayesian_uncertainty={uncertainty:.4f}",
            f"bayesian_conflict={conflict:.4f}",
            *uncertainty_profile.reasons,
        )
        return BayesianPosterior(
            prior=normalized_prior,
            probability=_clamp(probability),
            variance=max(0.0, float(variance)),
            uncertainty=_clamp(uncertainty),
            evidence_count=len(evidence),
            effective_sample_size=float(total),
            conflict=_clamp(conflict),
            alpha=float(alpha),
            beta=float(beta),
            uncertainty_profile=uncertainty_profile,
            evidence=evidence,
            reasons=reasons,
        )

    def component_to_evidence(self, component: ReasoningComponent) -> BayesianEvidence:
        confidence = _clamp(float(component.confidence))
        weight = max(0.0, float(component.weight))
        reliability = min(float(self.config.max_reliability), max(float(self.config.min_reliability), weight * 4.0))
        verdict = str(component.verdict or "neutral").strip().lower()
        direction = {
            "support": "support",
            "block": "oppose",
            "degrade": "oppose",
            "neutral": "neutral",
        }.get(verdict, "neutral")
        if direction == "neutral":
            probability = 0.5 + (confidence - 0.5) * 0.25
            reliability *= 0.5
        elif direction == "oppose" and verdict == "degrade":
            probability = 0.5 + confidence * 0.35
            reliability *= 0.65
        else:
            probability = confidence
        return BayesianEvidence(
            source=component.source,
            label=component.label,
            probability=probability,
            reliability=reliability,
            direction=direction,
            reasons=component.reasons,
        )

    @staticmethod
    def _soft_counts(evidence: BayesianEvidence) -> tuple[float, float]:
        probability = _clamp(evidence.probability)
        reliability = max(0.0, float(evidence.reliability))
        if evidence.direction == "oppose":
            return (1.0 - probability) * reliability, probability * reliability
        if evidence.direction == "neutral":
            return 0.5 * reliability, 0.5 * reliability
        return probability * reliability, (1.0 - probability) * reliability

    def _conflict(self, evidence: tuple[BayesianEvidence, ...], posterior: float) -> float:
        weighted_distance = 0.0
        total_weight = 0.0
        for item in evidence:
            success, failure = self._soft_counts(item)
            weight = success + failure
            if weight <= 0:
                continue
            implied = success / weight
            weighted_distance += abs(implied - posterior) * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return _clamp(weighted_distance / total_weight * 2.0)

def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
