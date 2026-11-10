from __future__ import annotations

from dataclasses import dataclass
from typing import List

from core.agency.goals import GOAL_STATUS_ACTIVE, GOAL_STATUS_QUEUED, Goal
from core.agency.identity_anchor import IdentityAnchor


@dataclass(frozen=True)
class ContextPack:
    system: str
    task: str
    evidence: str


class ContextGovernor:
    def select_active(self, goals: List[Goal], anchor: IdentityAnchor) -> List[Goal]:
        active = [goal for goal in goals if goal.status in {GOAL_STATUS_ACTIVE, GOAL_STATUS_QUEUED}]
        if not active:
            return []
        active.sort(key=lambda item: float(item.priority_score), reverse=True)
        max_active = max(1, int(anchor.max_active_goals))
        return active[:max_active]

    def build_context(
        self,
        *,
        anchor: IdentityAnchor,
        active_goal: Goal,
        queue: List[Goal],
        recent_notes: List[str] | None = None,
    ) -> ContextPack:
        system_text = "\n".join(
            [
                "Core goals:",
                *[f"- {goal}" for goal in anchor.core_goals],
                "Non-negotiables:",
                *[f"- {rule}" for rule in anchor.non_negotiables],
            ]
        )
        queue_lines = [
            f"- {goal.description} (score {goal.priority_score:.2f})"
            for goal in sorted(queue, key=lambda item: float(item.priority_score), reverse=True)[:5]
        ]
        task_text = "\n".join(
            [
                f"Active goal: {active_goal.description}",
                f"Domain: {active_goal.domain}",
                "Top queue:",
                *queue_lines,
            ]
        )
        evidence_lines = list(recent_notes or [])[:5]
        evidence_text = "\n".join(evidence_lines) if evidence_lines else ""

        budgets = anchor.context_budget or {}
        system_text = _clamp(system_text, int(budgets.get("system", 600)))
        task_text = _clamp(task_text, int(budgets.get("task", 1200)))
        evidence_text = _clamp(evidence_text, int(budgets.get("evidence", 1200)))
        return ContextPack(system=system_text, task=task_text, evidence=evidence_text)


def _clamp(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    trimmed = str(text or "")
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[-max_chars:]

