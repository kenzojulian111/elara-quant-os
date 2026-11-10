from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceAssessment:
    source: str
    credibility: float
    timestamp: float
    url: str = ""

    def temporal_relevance(self, *, now: float | None = None, half_life_sec: float = 86400.0) -> float:
        now = float(now if now is not None else time.time())
        age = max(0.0, now - float(self.timestamp or now))
        return 0.5 ** (age / max(1.0, float(half_life_sec)))


@dataclass(frozen=True)
class ResearchClaim:
    claim: str
    stance: str
    source: SourceAssessment
    confidence: float


@dataclass(frozen=True)
class ResearchSynthesis:
    thesis: str
    support_score: float
    contradiction_score: float
    net_confidence: float
    sources: tuple[str, ...]
    reasons: tuple[str, ...]


class ResearchSynthesizer:
    def synthesize(self, thesis: str, claims: Iterable[ResearchClaim], *, now: float | None = None) -> ResearchSynthesis:
        support = 0.0
        contradiction = 0.0
        sources: list[str] = []
        reasons: list[str] = []
        for claim in claims:
            relevance = claim.source.temporal_relevance(now=now)
            weight = max(0.0, min(1.0, claim.confidence)) * max(0.0, min(1.0, claim.source.credibility)) * relevance
            sources.append(claim.source.source)
            if claim.stance == "support":
                support += weight
            elif claim.stance == "contradict":
                contradiction += weight
            else:
                weight *= 0.25
            reasons.append(f"{claim.source.source}:{claim.stance}:{weight:.4f}")
        total = support + contradiction
        net = ((support - contradiction) / total) if total else 0.0
        return ResearchSynthesis(
            thesis=str(thesis or ""),
            support_score=support,
            contradiction_score=contradiction,
            net_confidence=net,
            sources=tuple(sorted(set(sources))),
            reasons=tuple(reasons),
        )
