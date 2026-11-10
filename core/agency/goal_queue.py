from __future__ import annotations

import time
from dataclasses import asdict
from typing import Iterable, List, Optional

from core.agency.goals import (
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_ARCHIVED,
    GOAL_STATUS_BLOCKED,
    GOAL_STATUS_DONE,
    GOAL_STATUS_QUEUED,
    Goal,
    GoalScoring,
)
from core.agency.identity_anchor import IdentityAnchor


class GoalQueue:
    def __init__(self, state_manager, user_id: str = "default"):
        self.state_manager = state_manager
        self.user_id = str(user_id or "default")
        self._queue_key = "goal_queue"
        self._active_key = "goal_active_id"

    def add(self, goal: Goal, anchor: IdentityAnchor | None = None) -> Goal:
        goals = self._load()
        if anchor and bool(getattr(anchor, "goal_dedupe_enabled", True)):
            merged = _merge_duplicate_goal(goals, goal)
            if merged:
                self._save(goals)
                return merged
        goals.append(goal)
        goals = self._apply_anchor_limits(goals, anchor)
        self._save(goals)
        return goal

    def upsert(self, goal: Goal) -> Goal:
        goals = self._load()
        for idx, existing in enumerate(goals):
            if existing.goal_id == goal.goal_id:
                goal.touch()
                goals[idx] = goal
                self._save(goals)
                return goal
        goals.append(goal)
        self._save(goals)
        return goal

    def list(self) -> List[Goal]:
        return list(self._load())

    def set_active(self, goal_id: str) -> bool:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return False
        goals = self._load()
        updated = False
        for goal in goals:
            if goal.goal_id == goal_id:
                goal.mark_status(GOAL_STATUS_ACTIVE)
                updated = True
            elif goal.status == GOAL_STATUS_ACTIVE:
                goal.mark_status(GOAL_STATUS_QUEUED)
        self.state_manager.set_state(self._active_key, goal_id, user_id=self.user_id)
        self._save(goals)
        return updated

    def archive(self, goal_id: str) -> bool:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return False
        goals = self._load()
        updated = False
        for goal in goals:
            if goal.goal_id == goal_id:
                goal.mark_status(GOAL_STATUS_ARCHIVED)
                updated = True
        self._save(goals)
        return updated

    def block(self, goal_id: str) -> bool:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return False
        goals = self._load()
        updated = False
        for goal in goals:
            if goal.goal_id == goal_id:
                goal.mark_status(GOAL_STATUS_BLOCKED)
                updated = True
        self._save(goals)
        return updated

    def unblock(self, goal_id: str) -> bool:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return False
        goals = self._load()
        updated = False
        for goal in goals:
            if goal.goal_id == goal_id and goal.status == GOAL_STATUS_BLOCKED:
                goal.mark_status(GOAL_STATUS_QUEUED)
                updated = True
        self._save(goals)
        return updated

    def set_done(self, goal_id: str) -> bool:
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return False
        goals = self._load()
        updated = False
        for goal in goals:
            if goal.goal_id == goal_id:
                goal.mark_status(GOAL_STATUS_DONE)
                updated = True
        self._save(goals)
        return updated

    def recompute_scores(self, anchor: IdentityAnchor) -> None:
        goals = self._load()
        weights = anchor.scoring_weights or {}
        for goal in goals:
            goal.priority_score = GoalScoring.score(goal, weights)
        self._save(goals)

    def enforce_anchor(self, anchor: IdentityAnchor) -> None:
        goals = self._load()
        goals = self._apply_anchor_limits(goals, anchor)
        self._save(goals)

    def next_goal(self) -> Optional[Goal]:
        goals = self._load()
        active = [goal for goal in goals if goal.status in {GOAL_STATUS_ACTIVE, GOAL_STATUS_QUEUED}]
        if not active:
            return None
        active.sort(key=lambda item: float(item.priority_score), reverse=True)
        return active[0]

    def choose_next(self, anchor: IdentityAnchor) -> Optional[Goal]:
        goals = self._load()
        goals = self._apply_anchor_limits(goals, anchor)
        self._save(goals)
        self.prune_expired()
        self.recompute_scores(anchor)
        goals = self._load()
        active_id = str(self.state_manager.get_state(self._active_key, user_id=self.user_id) or "").strip()
        active_goal = next((goal for goal in goals if goal.goal_id == active_id), None)
        candidates = [
            goal
            for goal in goals
            if goal.status in {GOAL_STATUS_ACTIVE, GOAL_STATUS_QUEUED}
            and _dependencies_satisfied(goal, goals)
        ]
        if not candidates:
            return active_goal
        candidates.sort(key=lambda item: float(item.priority_score), reverse=True)
        top = candidates[0]

        if active_goal is None:
            self.set_active(top.goal_id)
            return top

        if active_goal.goal_id == top.goal_id:
            return active_goal

        if not active_goal.preemptible:
            return active_goal

        if _should_preempt(active_goal, top, anchor):
            self.set_active(top.goal_id)
            return top
        return active_goal

    def refresh_scores(self, weights: Optional[dict] = None) -> None:
        goals = self._load()
        for goal in goals:
            goal.priority_score = GoalScoring.score(goal, weights or {})
        self._save(goals)

    def prune_expired(self) -> None:
        goals = self._load()
        for goal in goals:
            if goal.is_expired():
                goal.mark_status(GOAL_STATUS_ARCHIVED)
        self._save(goals)

    def _load(self) -> List[Goal]:
        rows = self.state_manager.get_state(self._queue_key, user_id=self.user_id) or []
        fields = set(Goal.__dataclass_fields__.keys())
        goals: List[Goal] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            clean = {k: row.get(k) for k in row.keys() if k in fields}
            try:
                goal = Goal(**clean)
            except Exception:
                continue
            if not goal.goal_id:
                continue
            goal.normalize()
            goals.append(goal)
        return goals

    def _save(self, goals: Iterable[Goal]) -> None:
        payload = [asdict(goal) for goal in goals]
        self.state_manager.set_state(self._queue_key, payload, user_id=self.user_id)

    def _apply_anchor_limits(self, goals: List[Goal], anchor: IdentityAnchor | None) -> List[Goal]:
        if not anchor:
            return goals
        if anchor.goal_ttl_default_sec and anchor.goal_ttl_default_sec > 0:
            for goal in goals:
                if not goal.ttl_sec:
                    goal.ttl_sec = int(anchor.goal_ttl_default_sec)

        max_active = max(1, int(anchor.max_active_goals))
        active = [goal for goal in goals if goal.status == GOAL_STATUS_ACTIVE]
        if len(active) > max_active:
            active.sort(key=lambda item: float(item.priority_score), reverse=True)
            for goal in active[max_active:]:
                goal.mark_status(GOAL_STATUS_QUEUED)

        max_queue = int(getattr(anchor, "max_goal_queue", 0) or 0)
        if max_queue > 0 and len(goals) > max_queue:
            ranked = sorted(
                goals,
                key=lambda item: (
                    item.status not in {GOAL_STATUS_ACTIVE, GOAL_STATUS_QUEUED},
                    -float(item.priority_score),
                ),
            )
            keep = ranked[:max_queue]
            keep_ids = {goal.goal_id for goal in keep}
            for goal in goals:
                if goal.goal_id not in keep_ids and goal.status not in {GOAL_STATUS_DONE, GOAL_STATUS_ARCHIVED}:
                    goal.mark_status(GOAL_STATUS_ARCHIVED)

        max_sub = max(1, int(anchor.max_sub_goals))
        queued = [goal for goal in goals if goal.status == GOAL_STATUS_QUEUED]
        if len(queued) > max_sub:
            queued.sort(key=lambda item: float(item.priority_score), reverse=True)
            for goal in queued[max_sub:]:
                goal.mark_status(GOAL_STATUS_ARCHIVED)

        return goals


def _parse_iso_ts(value: str) -> float:
    from datetime import datetime

    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.timestamp()
    return dt.timestamp()


def _should_preempt(active: Goal, challenger: Goal, anchor: IdentityAnchor) -> bool:
    preemption = anchor.preemption or {}
    min_delta = float(preemption.get("min_score_delta", 0.15))
    if float(challenger.priority_score) >= float(active.priority_score) + min_delta:
        return True
    deadline_hours = float(preemption.get("deadline_hours", 0))
    if deadline_hours > 0 and _deadline_within(challenger, deadline_hours):
        return True
    if bool(preemption.get("risk_override", True)):
        if float(challenger.risk) < float(active.risk) - 0.2:
            return True
    return False


def _deadline_within(goal: Goal, hours: float) -> bool:
    if not goal.deadline:
        return False
    try:
        deadline_ts = _parse_iso_ts(goal.deadline)
    except Exception:
        return False
    return (deadline_ts - time.time()) <= (hours * 3600)


def _normalize_goal_key(goal: Goal) -> str:
    return f"{goal.domain}:{' '.join(str(goal.description or '').lower().split())}"


def _merge_duplicate_goal(goals: List[Goal], incoming: Goal) -> Goal | None:
    incoming_key = _normalize_goal_key(incoming)
    for goal in goals:
        if goal.status in {GOAL_STATUS_ARCHIVED, GOAL_STATUS_DONE}:
            continue
        if _normalize_goal_key(goal) == incoming_key:
            goal.importance = max(goal.importance, incoming.importance)
            goal.urgency = max(goal.urgency, incoming.urgency)
            goal.impact = max(goal.impact, incoming.impact)
            goal.cost = min(goal.cost, incoming.cost)
            goal.risk = max(goal.risk, incoming.risk)
            goal.effort = max(goal.effort, incoming.effort)
            goal.confidence = max(goal.confidence, incoming.confidence)
            if incoming.tags:
                goal.tags = sorted(set(goal.tags) | set(incoming.tags))
            if incoming.deadline:
                goal.deadline = incoming.deadline
            if incoming.ttl_sec:
                goal.ttl_sec = incoming.ttl_sec
            goal.touch()
            goal.priority_score = goal.compute_score()
            return goal
    return None


def _dependencies_satisfied(goal: Goal, goals: List[Goal]) -> bool:
    deps = [str(dep) for dep in (goal.dependencies or []) if str(dep).strip()]
    if not deps:
        return True
    resolved = {g.goal_id for g in goals if g.status in {GOAL_STATUS_DONE, GOAL_STATUS_ARCHIVED}}
    return all(dep in resolved for dep in deps)

