from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResearchCandidate:
    candidate_id: str
    replay_return: float
    max_drawdown: float
    replay_cases: int


@dataclass(frozen=True)
class ResearchEvaluation:
    candidate_id: str
    promotable: bool
    auto_promoted: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


class ReplayNativeResearchEvaluator:
    def evaluate(self, candidate: ResearchCandidate) -> ResearchEvaluation:
        reasons: list[str] = []
        if int(candidate.replay_cases) < 30:
            reasons.append("insufficient_replay_cases")
        if float(candidate.replay_return) <= 0:
            reasons.append("non_positive_replay_return")
        if float(candidate.max_drawdown) > 0.20:
            reasons.append("drawdown_above_limit")
        return ResearchEvaluation(
            candidate_id=candidate.candidate_id,
            promotable=not reasons,
            auto_promoted=False,
            reasons=tuple(reasons),
        )
