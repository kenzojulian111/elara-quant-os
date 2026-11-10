from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CognitiveOpinion:
    node: str
    decision: str
    confidence: float
    weight: float = 1.0


@dataclass(frozen=True)
class CognitiveValidationReport:
    decision: str
    confidence: float
    approved: bool
    contradictions: tuple[str, ...] = field(default_factory=tuple)


class MultiAgentValidator:
    def validate(self, opinions: tuple[CognitiveOpinion, ...], *, threshold: float = 0.60) -> CognitiveValidationReport:
        scores: dict[str, float] = {}
        total = 0.0
        for opinion in opinions:
            weighted = _clamp(opinion.confidence) * max(0.0, float(opinion.weight))
            scores[opinion.decision] = scores.get(opinion.decision, 0.0) + weighted
            total += weighted
        # pick the decision with the highest score
        decision = max(scores, key=lambda k: scores[k]) if scores else "HOLD"
        confidence = scores.get(decision, 0.0) / total if total else 0.0
        contradictions = tuple(sorted(item for item, score in scores.items() if item != decision and score > 0))
        return CognitiveValidationReport(
            decision=decision,
            confidence=confidence,
            approved=confidence >= float(threshold) and len(contradictions) <= 1,
            contradictions=contradictions,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
