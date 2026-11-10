from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4


GOAL_STATUS_QUEUED = "queued"
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_BLOCKED = "blocked"
GOAL_STATUS_DONE = "done"
GOAL_STATUS_ARCHIVED = "archived"


@dataclass
class Goal:
    goal_id: str
    description: str
    domain: str = "system"
    tags: list[str] = field(default_factory=list)
    importance: float = 0.7
    urgency: float = 0.5
    impact: float = 0.7
    cost: float = 0.5
    risk: float = 0.3
    effort: float = 0.4
    confidence: float = 0.6
    priority_score: float = 0.0
    status: str = GOAL_STATUS_QUEUED
    source: str = "system"
    created_at: str = ""
    updated_at: str = ""
    status_updated_at: str = ""
    deadline: Optional[str] = None
    ttl_sec: Optional[int] = None
    preemptible: bool = True
    dependencies: list[str] = field(default_factory=list)
    completed_at: Optional[str] = None

    def compute_score(self) -> float:
        return GoalScoring.score(self)

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def mark_status(self, status: str) -> None:
        self.status = status
        stamp = _now_iso()
        self.status_updated_at = stamp
        self.updated_at = stamp
        if status == GOAL_STATUS_DONE:
            self.completed_at = stamp

    def is_expired(self, now_ts: Optional[float] = None) -> bool:
        if not self.ttl_sec or not self.created_at:
            return False
        try:
            created_ts = _parse_iso_ts(self.created_at)
        except Exception:
            return False
        now = float(now_ts or datetime.now(timezone.utc).timestamp())
        return (created_ts + float(self.ttl_sec)) <= now

    def normalize(self) -> None:
        self.importance = _clamp_unit(self.importance)
        self.urgency = _clamp_unit(self.urgency)
        self.impact = _clamp_unit(self.impact)
        self.cost = max(0.1, float(self.cost))
        self.risk = _clamp_unit(self.risk)
        self.effort = _clamp_unit(self.effort)
        self.confidence = _clamp_unit(self.confidence)
        self.priority_score = self.compute_score()


class GoalFactory:
    @staticmethod
    def create(
        description: str,
        *,
        domain: str = "system",
        tags: Optional[list[str]] = None,
        importance: float = 0.7,
        urgency: float = 0.5,
        impact: float = 0.7,
        cost: float = 0.5,
        risk: float = 0.3,
        effort: float = 0.4,
        confidence: float = 0.6,
        source: str = "system",
        deadline: Optional[str] = None,
        ttl_sec: Optional[int] = None,
        preemptible: bool = True,
        dependencies: Optional[list[str]] = None,
    ) -> Goal:
        stamp = _now_iso()
        goal = Goal(
            goal_id=uuid4().hex,
            description=str(description or "").strip(),
            domain=str(domain or "system"),
            tags=[str(tag) for tag in (tags or [])],
            importance=float(importance),
            urgency=float(urgency),
            impact=float(impact),
            cost=float(cost),
            risk=float(risk),
            effort=float(effort),
            confidence=float(confidence),
            source=str(source or "system"),
            created_at=stamp,
            updated_at=stamp,
            status_updated_at=stamp,
            deadline=deadline,
            ttl_sec=ttl_sec,
            preemptible=bool(preemptible),
            dependencies=[str(dep) for dep in (dependencies or [])],
        )
        goal.normalize()
        return goal


class GoalScoring:
    @staticmethod
    def score(goal: Goal, weights: Optional[dict] = None) -> float:
        weights = weights or {}
        importance = _clamp_unit(goal.importance) * float(weights.get("importance", 1.0))
        urgency = _clamp_unit(goal.urgency) * float(weights.get("urgency", 1.0))
        impact = _clamp_unit(goal.impact) * float(weights.get("impact", 1.0))
        confidence = _clamp_unit(goal.confidence)
        cost = max(0.1, float(goal.cost))
        risk_penalty = _clamp_unit(goal.risk) * float(weights.get("risk", 0.6))
        effort_penalty = _clamp_unit(goal.effort) * float(weights.get("effort", 0.5))
        dependency_penalty = min(0.4, 0.1 * float(len(goal.dependencies or [])))
        deadline_boost = _deadline_boost(goal.deadline)
        score = (importance * urgency * impact * confidence) / cost
        score = score + deadline_boost
        score = score - (risk_penalty + effort_penalty + dependency_penalty)
        return round(max(0.0, score), 4)


def _clamp_unit(value: float) -> float:
    try:
        val = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, val))


def _parse_iso_ts(value: str) -> float:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _deadline_boost(deadline: Optional[str]) -> float:
    if not deadline:
        return 0.0
    try:
        deadline_ts = _parse_iso_ts(deadline)
    except Exception:
        return 0.0
    hours = (deadline_ts - datetime.now(timezone.utc).timestamp()) / 3600.0
    if hours <= 0:
        return -0.2
    if hours <= 6:
        return 0.6
    if hours <= 24:
        return 0.35
    if hours <= 72:
        return 0.2
    return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

