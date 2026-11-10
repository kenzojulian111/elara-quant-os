from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class FeatureVector:
    features: dict[str, float]
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelPrediction:
    label: str
    confidence: float
    scores: dict[str, float]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionStump:
    feature: str
    threshold: float
    above_label: str
    below_label: str
    weight: float = 1.0

    def predict(self, vector: FeatureVector) -> tuple[str, float]:
        value = float(vector.features.get(self.feature, 0.0))
        label = self.above_label if value >= float(self.threshold) else self.below_label
        return label, max(0.0, float(self.weight))


class LightweightEnsemble:
    def __init__(self, stumps: Iterable[DecisionStump] = ()):
        self.stumps = tuple(stumps or ())

    @classmethod
    def from_thresholds(cls, rules: Iterable[dict[str, Any]]) -> "LightweightEnsemble":
        return cls(
            DecisionStump(
                feature=str(rule.get("feature") or ""),
                threshold=float(rule.get("threshold") or 0.0),
                above_label=str(rule.get("above_label") or "positive"),
                below_label=str(rule.get("below_label") or "negative"),
                weight=float(rule.get("weight") or 1.0),
            )
            for rule in rules
        )

    def predict(self, vector: FeatureVector) -> ModelPrediction:
        if not self.stumps:
            return ModelPrediction("neutral", 0.0, {}, ("model_empty",))
        scores: dict[str, float] = {}
        reasons: list[str] = []
        total = 0.0
        for stump in self.stumps:
            label, weight = stump.predict(vector)
            scores[label] = scores.get(label, 0.0) + weight
            total += weight
            reasons.append(f"{stump.feature}>={stump.threshold}:{label}")
        if total <= 0:
            return ModelPrediction("neutral", 0.0, scores, tuple(reasons))
        label, score = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0]
        return ModelPrediction(
            label=label,
            confidence=max(0.0, min(1.0, score / total)),
            scores={key: value / total for key, value in scores.items()},
            reasons=tuple(reasons),
        )
