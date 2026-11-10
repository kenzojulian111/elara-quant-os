from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


RUNTIME_FULL = "full"
RUNTIME_DEGRADED = "degraded"
RUNTIME_SAFE = "safe"
RUNTIME_RECOVERY = "recovery"
RUNTIME_STATES = (RUNTIME_FULL, RUNTIME_DEGRADED, RUNTIME_SAFE, RUNTIME_RECOVERY)

ALLOWED_RUNTIME_TRANSITIONS: dict[str, tuple[str, ...]] = {
    RUNTIME_FULL: (RUNTIME_DEGRADED, RUNTIME_SAFE),
    RUNTIME_DEGRADED: (RUNTIME_SAFE, RUNTIME_RECOVERY),
    RUNTIME_SAFE: (RUNTIME_RECOVERY,),
    RUNTIME_RECOVERY: (RUNTIME_FULL, RUNTIME_DEGRADED, RUNTIME_SAFE),
}


def normalize_runtime_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    if state not in RUNTIME_STATES:
        raise ValueError(f"Unsupported runtime state: {value!r}")
    return state


@dataclass(frozen=True)
class RuntimeTransition:
    from_state: str
    to_state: str
    reason: str
    transition_at: float
    entered_at: float
    previous_entered_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.from_state != self.to_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "transition_at": float(self.transition_at),
            "entered_at": float(self.entered_at),
            "previous_entered_at": float(self.previous_entered_at),
            "changed": self.changed,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RuntimeStateSnapshot:
    state: str
    entered_at: float
    previous_state: str
    transition_reason: str
    transition_count: int
    allowed_transitions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "entered_at": float(self.entered_at),
            "previous_state": self.previous_state,
            "transition_reason": self.transition_reason,
            "transition_count": int(self.transition_count),
            "allowed_transitions": tuple(self.allowed_transitions),
        }


class RuntimeStateMachine:
    def __init__(self, initial_state: str = RUNTIME_FULL, *, time_fn=None):
        now = float((time_fn or time.time)())
        self._time_fn = time_fn or time.time
        self._state = normalize_runtime_state(initial_state)
        self._entered_at = now
        self._previous_state = ""
        self._transition_reason = "runtime_initialized"
        self._transition_count = 0

    @property
    def state(self) -> str:
        return self._state

    def snapshot(self) -> RuntimeStateSnapshot:
        return RuntimeStateSnapshot(
            state=self._state,
            entered_at=self._entered_at,
            previous_state=self._previous_state,
            transition_reason=self._transition_reason,
            transition_count=self._transition_count,
            allowed_transitions=ALLOWED_RUNTIME_TRANSITIONS[self._state],
        )

    def transition_to(
        self,
        target_state: str,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeTransition:
        target = normalize_runtime_state(target_state)
        now = float(self._time_fn())
        previous_entered_at = self._entered_at
        if target == self._state:
            return RuntimeTransition(
                from_state=self._state,
                to_state=target,
                reason=str(reason or self._transition_reason),
                transition_at=now,
                entered_at=self._entered_at,
                previous_entered_at=previous_entered_at,
                metadata=dict(metadata or {}),
            )

        allowed = ALLOWED_RUNTIME_TRANSITIONS[self._state]
        if target not in allowed:
            raise ValueError(f"Invalid runtime transition: {self._state} -> {target}")

        previous_state = self._state
        self._state = target
        self._previous_state = previous_state
        self._entered_at = now
        self._transition_reason = str(reason or "runtime_transition")
        self._transition_count += 1
        return RuntimeTransition(
            from_state=previous_state,
            to_state=target,
            reason=self._transition_reason,
            transition_at=now,
            entered_at=self._entered_at,
            previous_entered_at=previous_entered_at,
            metadata=dict(metadata or {}),
        )

    def target_for_assessment(self, assessment: Any) -> str:
        desired = normalize_runtime_state(getattr(assessment, "mode", assessment))
        current = self._state
        if desired == current:
            return current
        if desired == RUNTIME_FULL and current != RUNTIME_RECOVERY:
            return RUNTIME_RECOVERY
        if current == RUNTIME_SAFE and desired == RUNTIME_DEGRADED:
            return RUNTIME_RECOVERY
        return desired

    def apply_assessment(
        self,
        assessment: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeTransition:
        reasons = tuple(getattr(assessment, "reasons", ()) or ())
        resolved_reason = reason or (reasons[0] if reasons else "runtime_assessment")
        resolved_metadata = dict(metadata or {})
        if reasons:
            resolved_metadata.setdefault("assessment_reasons", reasons)
        resolved_metadata.setdefault("assessment_mode", getattr(assessment, "mode", assessment))
        return self.transition_to(
            self.target_for_assessment(assessment),
            reason=resolved_reason,
            metadata=resolved_metadata,
        )
