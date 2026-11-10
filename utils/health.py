import threading
import time
from collections import deque
from dataclasses import dataclass, field
try:
    import psutil
except Exception:
    psutil = None

from config.settings import Config
from core.runtime_events import append_runtime_event
from core.runtime_state import RuntimeStateMachine
from utils.runtime_sanity import evaluate_runtime_sanity
from utils.system_policy import (
    CRITICAL_DEPENDENCY_STATES,
    DEGRADED_DEPENDENCY_STATES,
    resolve_dependency_impact,
)

_lock = threading.Lock()
_state = {
    "db_lock_count": 0,
    "db_lock_last_ts": None,
    "db_write_lock_contention_count": 0,
    "db_write_lock_contention_last_ts": None,
    "loop_lag_ms": 0.0,
    "loop_lag_ts": None,
    "llm_tool_calls": 0,
    "llm_tool_executed": 0,
    "llm_tool_blocked": 0,
    "llm_tool_invalid_args": 0,
    "llm_tool_requires_approval": 0,
    "llm_tool_last_ts": None,
    "llm_markdown_route_attempts": 0,
    "llm_markdown_route_hits": 0,
    "llm_markdown_policy_filtered": 0,
    "llm_markdown_policy_blocked": 0,
    "llm_markdown_policy_escalated": 0,
    "llm_markdown_last_ts": None,
}
_db_lock_events = deque()
_db_write_lock_contention_events = deque()
_DB_LOCK_WINDOW_SEC = 300
_CRITICAL_DOWN_STATES = set(CRITICAL_DEPENDENCY_STATES)
_DEGRADED_STATES = set(DEGRADED_DEPENDENCY_STATES)
_SAFE_LOOP_LAG_MS = 1000.0
_DEGRADED_LOOP_LAG_MS = 250.0
_SAFE_MEM_PCT = 97.0
_DEGRADED_MEM_PCT = 90.0
_SAFE_CPU_PCT = 99.0
_DEGRADED_CPU_PCT = 92.0
_SAFE_DB_CONTENTION_RATE = 10.0
_DEGRADED_DB_CONTENTION_RATE = 3.0
_SAFE_HEALTH_SCORE = 50.0
_DEGRADED_HEALTH_SCORE = 75.0
_CIRCUIT_CLOSED = "closed"
_CIRCUIT_OPEN = "open"
_CIRCUIT_HALF_OPEN = "half_open"
_RUNTIME_STATE_MACHINE = RuntimeStateMachine()


@dataclass(frozen=True)
class RuntimeModeAssessment:
    mode: str
    reasons: tuple[str, ...] = ()
    allow_new_entries: bool = True
    allow_background_tasks: bool = True
    dependency_states: dict[str, str] = field(default_factory=dict)
    dependency_impacts: dict[str, dict[str, str]] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DependencyCircuitObservation:
    states: dict[str, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    circuit_states: dict[str, str] = field(default_factory=dict)


class DependencyCircuitBreaker:
    def __init__(
        self,
        *,
        fail_threshold: int = 2,
        cooldown_sec: float = 120.0,
        blocking_states: set[str] | None = None,
        time_fn=None,
    ):
        self.fail_threshold = max(1, int(fail_threshold))
        self.cooldown_sec = max(1.0, float(cooldown_sec))
        self.blocking_states = {str(state or "").strip().lower() for state in (blocking_states or _CRITICAL_DOWN_STATES)}
        self._time_fn = time_fn or time.time
        self._state: dict[str, dict[str, float | int | str]] = {}

    def observe(self, dependency_states: dict[str, str] | None) -> DependencyCircuitObservation:
        now = float(self._time_fn())
        normalized = {
            str(name or "").strip().lower(): str(state or "").strip().lower()
            for name, state in (dependency_states or {}).items()
            if str(name or "").strip()
        }
        reasons: list[str] = []
        effective: dict[str, str] = {}
        circuit_states: dict[str, str] = {}

        for name, raw_state in normalized.items():
            tracker = self._state.setdefault(
                name,
                {"failures": 0, "open_until": 0.0, "state": _CIRCUIT_CLOSED},
            )
            open_until = float(tracker.get("open_until") or 0.0)
            if now < open_until:
                if raw_state in self.blocking_states:
                    tracker["failures"] = max(self.fail_threshold, int(tracker.get("failures") or 0))
                    tracker["open_until"] = now + self.cooldown_sec
                tracker["state"] = _CIRCUIT_OPEN
                effective[name] = "failed"
                circuit_states[name] = _CIRCUIT_OPEN
                reasons.append(f"dependency_circuit_open={name}:{raw_state or 'unknown'}")
                continue

            if open_until > 0:
                if raw_state in self.blocking_states:
                    tracker["failures"] = self.fail_threshold
                    tracker["open_until"] = now + self.cooldown_sec
                    tracker["state"] = _CIRCUIT_OPEN
                    effective[name] = "failed"
                    circuit_states[name] = _CIRCUIT_OPEN
                    reasons.append(f"dependency_circuit_reopened={name}:{raw_state}")
                    continue

                tracker["failures"] = 0
                tracker["open_until"] = 0.0
                tracker["state"] = _CIRCUIT_CLOSED
                effective[name] = raw_state or "unknown"
                circuit_states[name] = _CIRCUIT_HALF_OPEN
                reasons.append(f"dependency_circuit_half_open={name}")
                continue

            if raw_state in self.blocking_states:
                tracker["failures"] = int(tracker.get("failures") or 0) + 1
                if int(tracker["failures"]) >= self.fail_threshold:
                    tracker["open_until"] = now + self.cooldown_sec
                    tracker["state"] = _CIRCUIT_OPEN
                    effective[name] = "failed"
                    circuit_states[name] = _CIRCUIT_OPEN
                    reasons.append(f"dependency_circuit_open={name}:{raw_state}")
                else:
                    tracker["open_until"] = 0.0
                    tracker["state"] = _CIRCUIT_CLOSED
                    effective[name] = raw_state
                    circuit_states[name] = _CIRCUIT_CLOSED
                    reasons.append(f"dependency_failure_streak={name}:{tracker['failures']}")
                continue

            tracker["failures"] = 0
            tracker["open_until"] = 0.0
            tracker["state"] = _CIRCUIT_CLOSED
            effective[name] = raw_state or "unknown"
            circuit_states[name] = _CIRCUIT_CLOSED

        return DependencyCircuitObservation(
            states=effective,
            reasons=tuple(reasons),
            circuit_states=circuit_states,
        )


def runtime_state_snapshot() -> dict:
    return _RUNTIME_STATE_MACHINE.snapshot().to_dict()


def observe_runtime_state(
    assessment: RuntimeModeAssessment,
    *,
    source: str = "health",
    metadata: dict | None = None,
) -> dict:
    transition = _RUNTIME_STATE_MACHINE.apply_assessment(
        assessment,
        metadata={
            "dependency_states": dict(getattr(assessment, "dependency_states", {}) or {}),
            **dict(metadata or {}),
        },
    )
    if transition.changed:
        config_hash = ""
        try:
            config_hash = Config.config_hash()
        except Exception:
            config_hash = ""
        append_runtime_event(
            "RUNTIME_STATE_CHANGED",
            source=source,
            payload=transition.to_dict(),
            config_hash=config_hash,
        )
    return _RUNTIME_STATE_MACHINE.snapshot().to_dict()


def record_db_lock():
    now = time.time()
    with _lock:
        _state["db_lock_count"] += 1
        _state["db_lock_last_ts"] = now
        _db_lock_events.append(now)
        cutoff = now - _DB_LOCK_WINDOW_SEC
        while _db_lock_events and _db_lock_events[0] < cutoff:
            _db_lock_events.popleft()


def record_db_write_lock_contention():
    now = time.time()
    with _lock:
        _state["db_write_lock_contention_count"] += 1
        _state["db_write_lock_contention_last_ts"] = now
        _db_write_lock_contention_events.append(now)
        cutoff = now - _DB_LOCK_WINDOW_SEC
        while _db_write_lock_contention_events and _db_write_lock_contention_events[0] < cutoff:
            _db_write_lock_contention_events.popleft()


def update_loop_lag(lag_ms: float):
    with _lock:
        _state["loop_lag_ms"] = float(lag_ms)
        _state["loop_lag_ts"] = time.time()


def record_llm_tool_call():
    now = time.time()
    with _lock:
        _state["llm_tool_calls"] += 1
        _state["llm_tool_last_ts"] = now


def record_llm_tool_executed():
    now = time.time()
    with _lock:
        _state["llm_tool_executed"] += 1
        _state["llm_tool_last_ts"] = now


def record_llm_tool_blocked():
    now = time.time()
    with _lock:
        _state["llm_tool_blocked"] += 1
        _state["llm_tool_last_ts"] = now


def record_llm_tool_invalid_args():
    now = time.time()
    with _lock:
        _state["llm_tool_invalid_args"] += 1
        _state["llm_tool_last_ts"] = now


def record_llm_tool_requires_approval():
    now = time.time()
    with _lock:
        _state["llm_tool_requires_approval"] += 1
        _state["llm_tool_last_ts"] = now


def record_llm_markdown_route_attempt():
    now = time.time()
    with _lock:
        _state["llm_markdown_route_attempts"] += 1
        _state["llm_markdown_last_ts"] = now


def record_llm_markdown_route_hit():
    now = time.time()
    with _lock:
        _state["llm_markdown_route_hits"] += 1
        _state["llm_markdown_last_ts"] = now


def record_llm_markdown_policy_filtered():
    now = time.time()
    with _lock:
        _state["llm_markdown_policy_filtered"] += 1
        _state["llm_markdown_last_ts"] = now


def record_llm_markdown_policy_blocked():
    now = time.time()
    with _lock:
        _state["llm_markdown_policy_blocked"] += 1
        _state["llm_markdown_last_ts"] = now


def record_llm_markdown_policy_escalated():
    now = time.time()
    with _lock:
        _state["llm_markdown_policy_escalated"] += 1
        _state["llm_markdown_last_ts"] = now


def _get_firewall_queue_depth():
    path = Config.FIREWALL_QUEUE_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0
    except Exception:
        return None


def snapshot():
    with _lock:
        data = dict(_state)
        now = time.time()
        cutoff = now - _DB_LOCK_WINDOW_SEC
        while _db_lock_events and _db_lock_events[0] < cutoff:
            _db_lock_events.popleft()
        while _db_write_lock_contention_events and _db_write_lock_contention_events[0] < cutoff:
            _db_write_lock_contention_events.popleft()
        rate_per_min = (len(_db_lock_events) / (_DB_LOCK_WINDOW_SEC / 60.0)) if _DB_LOCK_WINDOW_SEC else 0.0
        contention_rate_per_min = (
            len(_db_write_lock_contention_events) / (_DB_LOCK_WINDOW_SEC / 60.0)
        ) if _DB_LOCK_WINDOW_SEC else 0.0
        data["db_lock_rate_per_min"] = rate_per_min
        data["db_write_lock_contention_rate_per_min"] = contention_rate_per_min
    data["firewall_queue_depth"] = _get_firewall_queue_depth()
    if psutil:
        try:
            data["cpu_pct"] = float(psutil.cpu_percent(interval=None))
            vm = psutil.virtual_memory()
            data["mem_pct"] = float(vm.percent)
        except Exception:
            data["cpu_pct"] = None
            data["mem_pct"] = None
    else:
        data["cpu_pct"] = None
        data["mem_pct"] = None
    data["runtime_state"] = runtime_state_snapshot()
    data["runtime_sanity"] = evaluate_runtime_sanity(
        runtime_state=str((data.get("runtime_state") or {}).get("state") or "")
    ).to_dict()
    return data


def assess_runtime_mode(
    *,
    dependency_states: dict[str, str] | None = None,
    metrics: dict | None = None,
    system_health_score: float | None = None,
) -> RuntimeModeAssessment:
    normalized_dependencies = {
        str(name or "").strip().lower(): str(state or "").strip().lower()
        for name, state in (dependency_states or {}).items()
        if str(name or "").strip()
    }
    metrics = dict(metrics or {})
    reasons: list[str] = []
    mode = "full"
    dependency_impacts: dict[str, dict[str, str]] = {}

    for name, state in normalized_dependencies.items():
        impact = resolve_dependency_impact(name, state)
        dependency_impacts[name] = {
            "dependency": impact.dependency,
            "state": impact.state,
            "tier": impact.tier,
            "scope": impact.scope,
            "effect": impact.effect,
        }
        if impact.effect == "safe":
            mode = "safe"
        elif impact.effect == "degraded" and mode != "safe":
            mode = "degraded"
        if impact.reason:
            reasons.append(impact.reason)

    loop_lag_ms = float(metrics.get("loop_lag_ms") or 0.0)
    if loop_lag_ms >= _SAFE_LOOP_LAG_MS:
        mode = "safe"
        reasons.append(f"loop_lag_safe={loop_lag_ms:.1f}")
    elif loop_lag_ms >= _DEGRADED_LOOP_LAG_MS and mode != "safe":
        mode = "degraded"
        reasons.append(f"loop_lag_degraded={loop_lag_ms:.1f}")

    db_contention = float(metrics.get("db_write_lock_contention_rate_per_min") or 0.0)
    if db_contention >= _SAFE_DB_CONTENTION_RATE:
        mode = "safe"
        reasons.append(f"db_contention_safe={db_contention:.2f}")
    elif db_contention >= _DEGRADED_DB_CONTENTION_RATE and mode != "safe":
        mode = "degraded"
        reasons.append(f"db_contention_degraded={db_contention:.2f}")

    mem_pct = float(metrics.get("mem_pct") or 0.0)
    if mem_pct >= _SAFE_MEM_PCT:
        mode = "safe"
        reasons.append(f"memory_safe={mem_pct:.1f}")
    elif mem_pct >= _DEGRADED_MEM_PCT and mode != "safe":
        mode = "degraded"
        reasons.append(f"memory_degraded={mem_pct:.1f}")

    cpu_pct = float(metrics.get("cpu_pct") or 0.0)
    if cpu_pct >= _SAFE_CPU_PCT:
        mode = "safe"
        reasons.append(f"cpu_safe={cpu_pct:.1f}")
    elif cpu_pct >= _DEGRADED_CPU_PCT and mode != "safe":
        mode = "degraded"
        reasons.append(f"cpu_degraded={cpu_pct:.1f}")

    if system_health_score is not None:
        score = float(system_health_score)
        if score <= _SAFE_HEALTH_SCORE:
            mode = "safe"
            reasons.append(f"system_health_safe={score:.1f}")
        elif score <= _DEGRADED_HEALTH_SCORE and mode != "safe":
            mode = "degraded"
            reasons.append(f"system_health_degraded={score:.1f}")

    if not reasons:
        reasons.append("runtime_full")

    return RuntimeModeAssessment(
        mode=mode,
        reasons=tuple(reasons),
        allow_new_entries=(mode == "full"),
        allow_background_tasks=(mode != "safe"),
        dependency_states=normalized_dependencies,
        dependency_impacts=dependency_impacts,
        metrics={
            "loop_lag_ms": loop_lag_ms,
            "db_write_lock_contention_rate_per_min": db_contention,
            "mem_pct": metrics.get("mem_pct"),
            "cpu_pct": metrics.get("cpu_pct"),
            "system_health_score": system_health_score,
        },
    )

