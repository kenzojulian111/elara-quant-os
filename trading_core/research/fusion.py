from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FusionInput:
    source_type: str
    signal: str
    confidence: float
    weight: float = 1.0
    metadata: dict | None = None


@dataclass(frozen=True)
class SituationAssessment:
    dominant_signal: str
    confidence: float
    scores: dict[str, float]
    reasons: tuple[str, ...]


class MultiSourceFusion:
    def assess(self, inputs: Iterable[FusionInput]) -> SituationAssessment:
        scores: dict[str, float] = {}
        reasons: list[str] = []
        for item in inputs or ():
            signal = str(item.signal or "neutral")
            score = max(0.0, float(item.confidence)) * max(0.0, float(item.weight))
            scores[signal] = scores.get(signal, 0.0) + score
            reasons.append(f"{item.source_type}:{signal}:{score:.4f}")
        if not scores:
            return SituationAssessment("neutral", 0.0, {}, ("fusion_no_inputs",))
        total = sum(scores.values()) or 1.0
        normalized = {key: value / total for key, value in scores.items()}
        dominant, confidence = sorted(normalized.items(), key=lambda item: (-item[1], item[0]))[0]
        return SituationAssessment(dominant, confidence, normalized, tuple(reasons))
