from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LatencyStage:
    name: str
    observed_ms: float
    budget_ms: float


@dataclass(frozen=True)
class LatencyBudgetReport:
    total_ms: float
    breached_stages: tuple[str, ...] = field(default_factory=tuple)
    degrade_required: bool = False


class LatencyLab:
    def assess(self, stages: tuple[LatencyStage, ...]) -> LatencyBudgetReport:
        breached = tuple(stage.name for stage in stages if float(stage.observed_ms) > float(stage.budget_ms))
        return LatencyBudgetReport(
            total_ms=sum(max(0.0, float(stage.observed_ms)) for stage in stages),
            breached_stages=breached,
            degrade_required=bool(breached),
        )
