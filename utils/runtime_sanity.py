from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.settings import Config
from utils.system_policy import CRITICAL_DEPENDENCY_STATES, enabled_experimental_subsystems


@dataclass(frozen=True)
class RuntimeSanityFinding:
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RuntimeSanityReport:
    status: str
    score: int
    config_hash: str
    enabled_experimental_subsystems: tuple[str, ...] = ()
    experimental_budget: int = 0
    findings: tuple[RuntimeSanityFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": int(self.score),
            "config_hash": self.config_hash,
            "enabled_experimental_subsystems": tuple(self.enabled_experimental_subsystems),
            "experimental_budget": int(self.experimental_budget),
            "findings": tuple(finding.to_dict() for finding in self.findings),
        }


def _bool_attr(config: Any, key: str, default: bool = False) -> bool:
    return bool(getattr(config, key, default))


def _int_attr(config: Any, key: str, default: int) -> int:
    try:
        return int(getattr(config, key, default))
    except Exception:
        return int(default)


def _config_hash(config: Any) -> str:
    fn = getattr(config, "config_hash", None)
    if callable(fn):
        try:
            return str(fn() or "")
        except Exception:
            return ""
    return ""


def _unknown_config_keys(config: Any) -> tuple[str, ...]:
    fn = getattr(config, "unknown_config_keys", None)
    if callable(fn):
        try:
            return tuple(str(item) for item in fn() if str(item))
        except Exception:
            return ()
    return ()


def _deprecated_config_keys(config: Any) -> tuple[str, ...]:
    fn = getattr(config, "deprecated_config_keys", None)
    if callable(fn):
        try:
            return tuple(str(item) for item in fn() if str(item))
        except Exception:
            return ()
    return ()


def evaluate_runtime_sanity(
    *,
    config: Any = None,
    dependency_states: dict[str, str] | None = None,
    runtime_state: str = "",
) -> RuntimeSanityReport:
    cfg = config or Config
    findings: list[RuntimeSanityFinding] = []
    dependency_states = {
        str(name or "").strip().lower(): str(state or "").strip().lower()
        for name, state in (dependency_states or {}).items()
        if str(name or "").strip()
    }
    runtime_state = str(runtime_state or "").strip().lower()

    unknown = _unknown_config_keys(cfg)
    if unknown:
        findings.append(
            RuntimeSanityFinding(
                code="unknown_config_keys",
                severity="warning",
                message="Project .env contains keys that are not part of the config contract.",
                details={"keys": unknown},
            )
        )

    deprecated = _deprecated_config_keys(cfg)
    if deprecated:
        findings.append(
            RuntimeSanityFinding(
                code="deprecated_config_keys",
                severity="warning",
                message="Project .env contains deprecated keys.",
                details={"keys": deprecated},
            )
        )

    enabled_experimental = enabled_experimental_subsystems(cfg)
    experimental_budget = max(0, _int_attr(cfg, "RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL", 1))
    if len(enabled_experimental) > experimental_budget:
        findings.append(
            RuntimeSanityFinding(
                code="experimental_complexity_budget_exceeded",
                severity="warning",
                message="Too many experimental subsystems are active for the configured runtime budget.",
                details={"enabled": enabled_experimental, "budget": experimental_budget},
            )
        )

    mode = str(getattr(cfg, "TRADING_MODE", "") or "").strip().lower()
    if mode in {"paper", "live"} and not _bool_attr(cfg, "RUNTIME_EVENT_STORE_ENABLED", True):
        findings.append(
            RuntimeSanityFinding(
                code="runtime_event_store_disabled",
                severity="warning" if mode == "paper" else "critical",
                message="Trading runtime is active without append-only runtime events.",
                details={"trading_mode": mode},
            )
        )

    if mode == "live" and _bool_attr(cfg, "DB_AUTO_CREATE_TABLES", False):
        findings.append(
            RuntimeSanityFinding(
                code="live_auto_create_tables_enabled",
                severity="critical",
                message="Live trading should not rely on automatic schema creation.",
                details={"DB_AUTO_CREATE_TABLES": True},
            )
        )

    if _bool_attr(cfg, "REDIS_EVENT_BUS_REQUIRED", False):
        fallback_keys = (
            "TRADING_FILE_IPC_FALLBACK_ENABLED",
            "TRADING_STATUS_FILE_FALLBACK_ENABLED",
            "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED",
        )
        enabled_fallbacks = tuple(key for key in fallback_keys if _bool_attr(cfg, key, False))
        if enabled_fallbacks:
            findings.append(
                RuntimeSanityFinding(
                    code="redis_required_with_file_fallbacks",
                    severity="critical",
                    message="Redis-required runtime still has file fallback paths enabled.",
                    details={"fallbacks": enabled_fallbacks},
                )
            )

    if runtime_state == "full":
        blocking = {str(item).strip().lower() for item in CRITICAL_DEPENDENCY_STATES}
        impossible = {
            name: state
            for name, state in dependency_states.items()
            if state in blocking and name in {"exchange", "db", "trader_setup"}
        }
        if impossible:
            findings.append(
                RuntimeSanityFinding(
                    code="impossible_full_runtime_state",
                    severity="critical",
                    message="Runtime reports full while a core dependency is unavailable.",
                    details={"runtime_state": runtime_state, "dependencies": impossible},
                )
            )

    critical_count = sum(1 for finding in findings if finding.severity == "critical")
    warning_count = sum(1 for finding in findings if finding.severity == "warning")
    status = "critical" if critical_count else "warning" if warning_count else "ok"
    score = max(0, 100 - (critical_count * 35) - (warning_count * 10))
    return RuntimeSanityReport(
        status=status,
        score=score,
        config_hash=_config_hash(cfg),
        enabled_experimental_subsystems=enabled_experimental,
        experimental_budget=experimental_budget,
        findings=tuple(findings),
    )
