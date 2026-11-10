from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "running", "active"}:
            return True
        if text in {"", "0", "false", "no", "off", "inactive", "stopped"}:
            return False
    return default


def _as_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


@dataclass(frozen=True)
class HealthCheckContract:
    name: str
    status: str = "unknown"
    details: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, name: str, payload: Any) -> "HealthCheckContract":
        data = _as_dict(payload)
        return cls(
            name=str(name or "").strip() or "unknown",
            status=_as_str(data.get("status"), "unknown"),
            details=data,
        )


@dataclass(frozen=True)
class CoreHealthContract:
    overall_status: str = "unknown"
    checks: dict[str, HealthCheckContract] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "CoreHealthContract":
        data = _as_dict(payload)
        raw_checks = _as_dict(data.get("checks"))
        checks = {
            str(name): HealthCheckContract.from_payload(str(name), value)
            for name, value in raw_checks.items()
        }
        return cls(
            overall_status=_as_str(data.get("status"), "unknown"),
            checks=checks,
        )

    def check_status(self, name: str) -> str:
        check = self.checks.get(str(name or "").strip())
        if not check:
            return "unknown"
        return check.status


@dataclass(frozen=True)
class TraderStatusContract:
    is_running: bool = False
    simulation_fallback: bool = False
    control_state: str = "unknown"
    setup_error: str = ""
    stale: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "TraderStatusContract":
        data = _as_dict(payload)
        is_running = _as_bool(data.get("is_running"))
        simulation_fallback = _as_bool(data.get("simulation_fallback"))
        control_state = _as_str(data.get("control_state"), "unknown")
        setup_error = _as_str(data.get("setup_error"), "")
        stale = _as_bool(data.get("stale"))
        normalized = dict(data)
        normalized["is_running"] = is_running
        normalized["simulation_fallback"] = simulation_fallback
        normalized["control_state"] = control_state
        normalized["setup_error"] = setup_error
        normalized["stale"] = stale
        return cls(
            is_running=is_running,
            simulation_fallback=simulation_fallback,
            control_state=control_state,
            setup_error=setup_error,
            stale=stale,
            raw=normalized,
        )

