from __future__ import annotations

import os
import re
import logging
import hashlib
import json
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from config.governance import ConfigRegistry, build_config_registry, registry_contract


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
ENV_PATH = ROOT_DIR / ".env"
SQLITE_DEFAULT_PATH = ROOT_DIR / "runtime" / "elara.db"
log = logging.getLogger("elara.config")
_DOMAIN_ENV_FILES: tuple[str, ...] = (
    "config/core.env",
    "config/runtime.env",
    "config/trading.env",
    "config/governance.env",
    "config/observability.env",
    "config/llm.env",
    "config/blockchain.env",
    "config/experimental.env",
)
_CORE_CONFIG_KEYS: tuple[str, ...] = (
    "CONFIG_GOVERNANCE_ENABLED",
    "CONFIG_DOMAIN_FILES_ENABLED",
    "CONFIG_DOMAIN_FILES",
    "CONFIG_STRICT_VALIDATION",
    "CONFIG_STRICT_UNKNOWN_KEYS",
    "CONFIG_STRICT_DEPRECATED_KEYS",
    "CONFIG_SNAPSHOT_AUDIT_ENABLED",
    "CONFIG_SNAPSHOT_AUDIT_PATH",
    "CONFIG_PROFILE",
    "APP_IDENTITY",
    "APP_PRODUCT_FOCUS",
    "APP_ASSISTANT_ROLE",
    "ENV",
    "TRADING_MODE",
    "TRADING_SYMBOL",
    "TRADING_STRATEGY",
    "SECRET_KEY",
    "JWT_SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "STATE_BACKEND",
    "REDIS_EVENT_BUS_REQUIRED",
    "REDIS_STREAM_EVENT_BUS_ENABLED",
    "REDIS_STREAM_EVENT_BUS_REQUIRED",
    "STATE_BACKEND_FAIL_FAST",
    "FAIL_FAST_STARTUP",
    "DB_AUTO_CREATE_TABLES",
    "WEB_FORCE_HTTPS",
    "WEB_REVERSE_PROXY_ENABLED",
    "WEB_ALLOWED_HOSTS",
    "CORS_ORIGINS",
    "ENABLE_TRADING_SKILL",
    "ENABLE_KNOWLEDGE_SKILL",
    "LLM_PRIMARY_PROVIDER",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "DAILY_MAX_LOSS_PCT",
    "MAX_DRAWDOWN_PCT",
    "LIFE_MIN_SLEEP_HOURS",
    "LIFE_MAX_STRESS",
)
_EXPERIMENTAL_PROFILE_DEFAULTS: dict[str, Any] = {
    "ENABLE_KNOWLEDGE_SKILL": False,
    "ENABLE_STREAM": False,
    "ENABLE_VOICE": False,
    "EXPERIMENTAL_RUNTIME_ALLOWLIST": "",
    "PRESENCE_ENABLED": False,
    "LLM_MARKDOWN_SKILLS_ENABLED": False,
    "LLM_MARKDOWN_POLICY_ENABLED": False,
    "LLM_MARKDOWN_ROUTER_ENABLED": False,
    "LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED": False,
    "LLM_MARKDOWN_SKILLS_WATCH_ENABLED": False,
    "LLM_MARKDOWN_ESCALATION_ENABLED": False,
}
_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "development": {
        "APP_IDENTITY": "Elara is an AI-native trading decision platform with an assistant interface.",
        "APP_PRODUCT_FOCUS": "trading_decision_platform",
        "APP_ASSISTANT_ROLE": "development_assistant",
        "TRADING_MODE": "paper",
        "STATE_BACKEND": "file",
        "STATE_ALLOW_MEMORY_FALLBACK": False,
        "REDIS_EVENT_BUS_REQUIRED": False,
        "REDIS_STREAM_EVENT_BUS_ENABLED": False,
        "REDIS_STREAM_EVENT_BUS_REQUIRED": False,
        "STATE_BACKEND_FAIL_FAST": False,
        "TRADING_FILE_IPC_FALLBACK_ENABLED": True,
        "TRADING_STATUS_FILE_FALLBACK_ENABLED": True,
        "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": True,
        "FAIL_FAST_STARTUP": False,
        "DB_AUTO_CREATE_TABLES": True,
        "WEB_FORCE_HTTPS": False,
        **_EXPERIMENTAL_PROFILE_DEFAULTS,
    },
    "paper": {
        "APP_IDENTITY": "Elara is an AI-native trading decision platform with an assistant interface.",
        "APP_PRODUCT_FOCUS": "trading_decision_platform",
        "APP_ASSISTANT_ROLE": "paper_trading_operator",
        "TRADING_MODE": "paper",
        "STATE_BACKEND": "redis",
        "STATE_ALLOW_MEMORY_FALLBACK": False,
        "REDIS_EVENT_BUS_REQUIRED": True,
        "REDIS_STREAM_EVENT_BUS_ENABLED": True,
        "REDIS_STREAM_EVENT_BUS_REQUIRED": True,
        "STATE_BACKEND_FAIL_FAST": True,
        "TRADING_FILE_IPC_FALLBACK_ENABLED": False,
        "TRADING_STATUS_FILE_FALLBACK_ENABLED": False,
        "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": False,
        "FAIL_FAST_STARTUP": True,
        "DB_AUTO_CREATE_TABLES": False,
        "WEB_FORCE_HTTPS": False,
        **_EXPERIMENTAL_PROFILE_DEFAULTS,
    },
    "backtest": {
        "APP_IDENTITY": "Elara is an AI-native trading decision platform with an assistant interface.",
        "APP_PRODUCT_FOCUS": "trading_decision_platform",
        "APP_ASSISTANT_ROLE": "research_assistant",
        "TRADING_MODE": "backtest",
        "STATE_BACKEND": "file",
        "STATE_ALLOW_MEMORY_FALLBACK": False,
        "REDIS_EVENT_BUS_REQUIRED": False,
        "REDIS_STREAM_EVENT_BUS_ENABLED": False,
        "REDIS_STREAM_EVENT_BUS_REQUIRED": False,
        "STATE_BACKEND_FAIL_FAST": False,
        "TRADING_FILE_IPC_FALLBACK_ENABLED": True,
        "TRADING_STATUS_FILE_FALLBACK_ENABLED": True,
        "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": True,
        "FAIL_FAST_STARTUP": False,
        "DB_AUTO_CREATE_TABLES": True,
        "WEB_FORCE_HTTPS": False,
        **_EXPERIMENTAL_PROFILE_DEFAULTS,
    },
    "live": {
        "APP_IDENTITY": "Elara is an AI-native trading decision platform with an assistant interface.",
        "APP_PRODUCT_FOCUS": "trading_decision_platform",
        "APP_ASSISTANT_ROLE": "live_trading_guard",
        "TRADING_MODE": "live",
        "STATE_BACKEND": "redis",
        "STATE_ALLOW_MEMORY_FALLBACK": False,
        "REDIS_EVENT_BUS_REQUIRED": True,
        "REDIS_STREAM_EVENT_BUS_ENABLED": True,
        "REDIS_STREAM_EVENT_BUS_REQUIRED": True,
        "STATE_BACKEND_FAIL_FAST": True,
        "TRADING_FILE_IPC_FALLBACK_ENABLED": False,
        "TRADING_STATUS_FILE_FALLBACK_ENABLED": False,
        "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": False,
        "FAIL_FAST_STARTUP": True,
        "DB_AUTO_CREATE_TABLES": False,
        "WEB_FORCE_HTTPS": True,
        **_EXPERIMENTAL_PROFILE_DEFAULTS,
    },
}

_LIST_KEYS = {
    "DELIST_STATUS_RISK_FLAGS",
    "EXCHANGE_FALLBACKS",
    "JWT_TOKEN_LOCATION",
    "REQUIRED_COMPONENTS",
}
_PLACEHOLDER_MARKERS = (
    "replace-with-",
    "changeme",
    "your_",
    "dummy",
    "example",
    "<",
    ">",
)
_STRING_KEY_MARKERS = (
    "API",
    "CHANNEL",
    "COMMAND",
    "CREDENTIAL",
    "DEVICE",
    "DIR",
    "DOMAIN",
    "EMAIL",
    "HOST",
    "KEY",
    "LANGUAGE",
    "MODEL",
    "MODE",
    "NAME",
    "PASSWORD",
    "PATH",
    "PREFIX",
    "PROFILE",
    "PROVIDER",
    "ROLE",
    "SECRET",
    "STATUS",
    "SYMBOL",
    "TOKEN",
    "TOPIK",
    "TYPE",
    "URL",
    "USER",
)
_SECRET_KEY_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY")
_EXACT_BOOL_KEYS = {
    "ALLOW_GUEST_DASHBOARD",
    "CONFIG_STRICT_DEPRECATED_KEYS",
    "CONFIG_STRICT_UNKNOWN_KEYS",
    "CONFIG_STRICT_VALIDATION",
    "DEBUG",
    "DB_AUTO_CREATE_TABLES",
    "DB_USE_ALEMBIC",
    "DB_ALEMBIC_AUTO_UPGRADE",
    "FAIL_FAST_STARTUP",
    "JWT_COOKIE_CSRF_PROTECT",
    "JWT_REVOKE_ENABLED",
    "LOG_JSON",
    "LLM_MARKDOWN_SKILLS_WATCH_ENABLED",
    "OPENBB_ONLY",
    "OPENBB_STRICT",
    "PRESENCE_ENABLED",
    "SELF_DEPLOY_SLO_ENFORCE",
    "SELF_HEAL_CONFIG_DRIFT_ENABLED",
    "SQLALCHEMY_TRACK_MODIFICATIONS",
    "TESTING",
    "WEB_CSRF_PROTECT",
    "WEB_FORCE_HTTPS",
    "WEB_SECURITY_HEADERS",
}
_NUMERIC_SUFFIXES = (
    "_AGE",
    "_BACKOFF",
    "_BURST",
    "_BYTES",
    "_CHARS",
    "_COUNT",
    "_DAYS",
    "_HEIGHT",
    "_HOURS",
    "_INTERVAL",
    "_LIMIT",
    "_LOOPS",
    "_MAX",
    "_MAXLEN",
    "_MB",
    "_MIN",
    "_MINUTES",
    "_MS",
    "_PCT",
    "_PERIOD",
    "_POINTS",
    "_PORT",
    "_RETRIES",
    "_SEC",
    "_SECONDS",
    "_SIZE",
    "_STEPS",
    "_TIMEOUT",
    "_TOKENS",
    "_TTL",
    "_WIDTH",
    "_WINDOW",
)
_NUMERIC_INFIX_MARKERS = (
    "_AGE_",
    "_BURST",
    "_BYTES",
    "_CHARS",
    "_COUNT",
    "_HEIGHT",
    "_INTERVAL",
    "_LIMIT",
    "_LOOPS",
    "_MAX_",
    "_MB",
    "_PCT",
    "_POINTS",
    "_PORT",
    "_RETRIES",
    "_SEC",
    "_SIZE",
    "_STEPS",
    "_TIMEOUT",
    "_TOKENS",
    "_TTL",
    "_WIDTH",
    "_WINDOW",
)
_GROUPED_CONFIG_VERSION = "v1"
_VALID_STATE_BACKENDS = {"auto", "file", "memory", "redis"}
_TRANSPORT_FILE_FALLBACK_KEYS = (
    "TRADING_FILE_IPC_FALLBACK_ENABLED",
    "TRADING_STATUS_FILE_FALLBACK_ENABLED",
    "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED",
)
_DEPRECATED_CONFIG_KEYS: dict[str, str] = {
    "TG_ALLOWED_USER_ID": "Use TG_ALLOWED_USER instead.",
}
_CONFIG_WARNINGS: dict[str, tuple[str, ...]] = {
    "unknown": (),
    "deprecated": (),
}


@dataclass(frozen=True)
class TradingConfigSchema:
    profile: str
    mode: str
    symbol: str
    strategy: str
    daily_max_loss_pct: float
    max_drawdown_pct: float
    life_min_sleep_hours: float
    life_max_stress: float
    trading_skill_enabled: bool


@dataclass(frozen=True)
class InfraConfigSchema:
    environment: str
    state_backend: str
    redis_event_bus_required: bool
    state_backend_fail_fast: bool
    database_configured: bool
    redis_configured: bool
    db_auto_create_tables: bool
    trading_status_path: str
    trading_command_path: str
    core_heartbeat_path: str
    file_fallback_enabled: bool


@dataclass(frozen=True)
class SecurityConfigSchema:
    secret_key_configured: bool
    jwt_secret_configured: bool
    fail_fast_startup: bool
    web_force_https: bool
    web_reverse_proxy_enabled: bool
    web_allowed_hosts: tuple[str, ...]
    cors_origins: tuple[str, ...]
    csrf_protect: bool
    security_headers: bool
    jwt_cookie_csrf_protect: bool


@dataclass(frozen=True)
class LLMConfigSchema:
    primary_provider: str
    nlu_providers: tuple[str, ...]
    openai_configured: bool
    groq_configured: bool
    gemini_configured: bool
    allow_no_api_key_in_dev: bool


@dataclass(frozen=True)
class AppConfigSchema:
    identity: str
    product_focus: str
    assistant_role: str
    trading: TradingConfigSchema
    infra: InfraConfigSchema
    security: SecurityConfigSchema
    llm: LLMConfigSchema

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _parse_domain_env_files(domain_files: Any = "") -> tuple[dict[str, str], tuple[str, ...]]:
    values: dict[str, str] = {}
    layers: list[str] = []
    env_files = _csv_items(domain_files) or _DOMAIN_ENV_FILES
    for rel_path in env_files:
        path = Path(rel_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        parsed = _parse_env_file(path)
        if not parsed:
            continue
        values.update(parsed)
        try:
            layers.append(path.resolve().relative_to(ROOT_DIR).as_posix())
        except ValueError:
            layers.append(str(path.resolve()))
    return values, tuple(layers)


def _seed_process_env(dotenv_values: dict[str, str]) -> None:
    for key, value in dotenv_values.items():
        if key and value is not None:
            os.environ.setdefault(key, value)


def _looks_like_placeholder(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_bool_key(key: str) -> bool:
    upper = key.upper()
    if upper in _EXACT_BOOL_KEYS:
        return True
    if upper.startswith(("ALLOW_", "ENABLE_", "DISABLE_", "USE_", "FORCE_", "REQUIRE_")):
        return True
    if upper.endswith(("_ENABLED", "_DISABLED", "_REQUIRED", "_PROTECT", "_SECURE")):
        return True
    return any(
        token in upper
        for token in (
            "_ALLOW_",
            "_ENABLE_",
            "_DISABLE_",
            "_FORCE_",
            "_PROTECT_",
            "_REQUIRE_",
            "_USE_",
        )
    )


def _bool_from_string(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_stringish_key(key: str) -> bool:
    upper = key.upper()
    if upper.endswith(_NUMERIC_SUFFIXES):
        return False
    if any(marker in upper for marker in _NUMERIC_INFIX_MARKERS):
        return False
    return any(marker in upper for marker in _STRING_KEY_MARKERS)


def _coerce_scalar(key: str, value: str) -> Any:
    raw = str(value or "").strip()
    if key in _LIST_KEYS:
        return [part.strip() for part in raw.split(",") if part.strip()]

    if _is_bool_key(key) and raw.lower() in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
        return _bool_from_string(raw)

    if raw == "":
        if _is_stringish_key(key):
            return ""
        return None

    if _is_stringish_key(key):
        return raw

    if re.fullmatch(r"[+-]?\d+", raw):
        try:
            return int(raw)
        except Exception:
            return raw

    if re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.|\.\d+)", raw):
        try:
            return float(raw)
        except Exception:
            return raw

    return raw


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return str(path.resolve())


def _csv_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = str(value or "").split(",")
    out: list[str] = []
    seen = set()
    for item in parts:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _config_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _config_json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_config_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _stable_config_json(value: Any) -> str:
    return json.dumps(_config_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_secret_key_name(key: str) -> bool:
    upper = str(key or "").upper()
    return any(marker in upper for marker in _SECRET_KEY_MARKERS)


def _redact_config_tree(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_config_tree(item, str(key)) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if _is_secret_key_name(key_hint):
        return "__configured__" if _is_configured_secret(value) else ""
    if isinstance(value, (list, tuple, set)):
        return [_redact_config_tree(item, key_hint) for item in value]
    return _config_json_safe(value)


def _enabled_feature_flags(values: dict[str, Any]) -> tuple[str, ...]:
    prefixes = ("ENABLE_", "USE_", "ALLOW_", "FORCE_", "REQUIRE_")
    suffixes = ("_ENABLED", "_REQUIRED")
    flags = []
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, bool) or not value:
            continue
        upper = key.upper()
        if upper.startswith(prefixes) or upper.endswith(suffixes):
            flags.append(upper)
    return tuple(sorted(set(flags)))


def _is_configured_secret(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not _looks_like_placeholder(text)


def _normalize_profile_name(value: Any) -> str:
    normalized = str(value or "").strip().lower() or "development"
    if normalized in _PROFILE_DEFAULTS:
        return normalized
    return "development"


def _known_config_keys(example_values: dict[str, str]) -> set[str]:
    keys = set(example_values.keys())
    for example_path in (ROOT_DIR / "config").glob("*.env.example"):
        keys.update(_parse_env_file(example_path).keys())
    keys.update(_CORE_CONFIG_KEYS)
    keys.update(_EXPERIMENTAL_PROFILE_DEFAULTS.keys())
    keys.update(_LIST_KEYS)
    keys.update(_EXACT_BOOL_KEYS)
    keys.update(_DEPRECATED_CONFIG_KEYS.keys())
    keys.update(
        {
            "APP_ENV",
            "FLASK_ENV",
            "SQLALCHEMY_DATABASE_URI",
            "SQLALCHEMY_ENGINE_OPTIONS",
            "SQLALCHEMY_TRACK_MODIFICATIONS",
            "SELF_DEPLOY_REQUIREMENTS_FILE",
            "SELF_DEPLOY_REQUIRE_HASHES",
        }
    )
    for defaults in _PROFILE_DEFAULTS.values():
        keys.update(defaults.keys())
    return {str(key).strip() for key in keys if str(key).strip()}


def _collect_config_warnings(
    *,
    dotenv_values: dict[str, str],
    example_values: dict[str, str],
) -> dict[str, tuple[str, ...]]:
    known_keys = _known_config_keys(example_values)
    project_keys = set(dotenv_values.keys())
    unknown = tuple(sorted(key for key in project_keys if key not in known_keys))
    deprecated = tuple(sorted(key for key in project_keys if key in _DEPRECATED_CONFIG_KEYS))
    return {"unknown": unknown, "deprecated": deprecated}


def _publish_config_warnings(warnings: dict[str, tuple[str, ...]]) -> None:
    _CONFIG_WARNINGS["unknown"] = tuple(warnings.get("unknown") or ())
    _CONFIG_WARNINGS["deprecated"] = tuple(warnings.get("deprecated") or ())
    if _CONFIG_WARNINGS["unknown"]:
        log.warning("Unknown project config keys: %s", ", ".join(_CONFIG_WARNINGS["unknown"]))
    if _CONFIG_WARNINGS["deprecated"]:
        details = [
            f"{key} ({_DEPRECATED_CONFIG_KEYS.get(key, 'deprecated')})"
            for key in _CONFIG_WARNINGS["deprecated"]
        ]
        log.warning("Deprecated project config keys in .env: %s", "; ".join(details))


def _config_registry_for_values(values: dict[str, Any]) -> ConfigRegistry:
    return build_config_registry(
        values,
        unknown_keys=tuple(_CONFIG_WARNINGS.get("unknown") or ()),
        deprecated_keys=tuple(_CONFIG_WARNINGS.get("deprecated") or ()),
        deprecated_metadata=_DEPRECATED_CONFIG_KEYS,
        source_layers=tuple(values.get("CONFIG_SOURCE_LAYERS") or ()),
    )


def _apply_config_governance(values: dict[str, Any]) -> None:
    values["CONFIG_GOVERNANCE_ENABLED"] = bool(values.get("CONFIG_GOVERNANCE_ENABLED", True))
    values["CONFIG_STRICT_VALIDATION"] = bool(values.get("CONFIG_STRICT_VALIDATION", False))
    values["CONFIG_STRICT_UNKNOWN_KEYS"] = bool(values.get("CONFIG_STRICT_UNKNOWN_KEYS", False))
    values["CONFIG_STRICT_DEPRECATED_KEYS"] = bool(values.get("CONFIG_STRICT_DEPRECATED_KEYS", False))
    values["CONFIG_SNAPSHOT_AUDIT_ENABLED"] = bool(values.get("CONFIG_SNAPSHOT_AUDIT_ENABLED", False))
    values["CONFIG_SNAPSHOT_AUDIT_PATH"] = str(
        values.get("CONFIG_SNAPSHOT_AUDIT_PATH") or "runtime/config_snapshots.jsonl"
    )

    if not bool(values.get("CONFIG_GOVERNANCE_ENABLED", True)):
        values["CONFIG_REGISTRY_HASH"] = ""
        values["CONFIG_GOVERNANCE_REPORT"] = {
            "errors": (),
            "warnings": (),
            "unknown_keys": tuple(_CONFIG_WARNINGS.get("unknown") or ()),
            "deprecated_keys": tuple(_CONFIG_WARNINGS.get("deprecated") or ()),
        }
        return

    registry = _config_registry_for_values(values)
    report = registry.validate()
    values["CONFIG_REGISTRY_HASH"] = registry.config_hash()
    values["CONFIG_GOVERNANCE_REPORT"] = asdict(report)

    if report.errors:
        log.error("Config governance validation errors: %s", "; ".join(report.errors))
    if report.warnings:
        log.warning("Config governance warnings: %s", "; ".join(report.warnings))

    strict_errors = list(report.errors) if values["CONFIG_STRICT_VALIDATION"] else []
    if values["CONFIG_STRICT_UNKNOWN_KEYS"] and report.unknown_keys:
        strict_errors.append("unknown_config_keys=" + ",".join(report.unknown_keys))
    if values["CONFIG_STRICT_DEPRECATED_KEYS"] and report.deprecated_keys:
        strict_errors.append("deprecated_config_keys=" + ",".join(report.deprecated_keys))
    if strict_errors:
        raise ValueError("Invalid config governance contract: " + " | ".join(strict_errors))


def _enabled_keys(values: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(key for key in keys if bool(values.get(key, False)))


def _validate_config_dependencies(values: dict[str, Any]) -> None:
    errors: list[str] = []
    state_backend = str(values.get("STATE_BACKEND", "auto") or "auto").strip().lower()
    values["STATE_BACKEND"] = state_backend
    if state_backend not in _VALID_STATE_BACKENDS:
        errors.append(
            "STATE_BACKEND must be one of "
            f"{', '.join(sorted(_VALID_STATE_BACKENDS))}; got {state_backend!r}"
        )

    file_fallbacks = _enabled_keys(values, _TRANSPORT_FILE_FALLBACK_KEYS)
    if bool(values.get("REDIS_EVENT_BUS_REQUIRED", False)) and file_fallbacks:
        errors.append(
            "REDIS_EVENT_BUS_REQUIRED=1 cannot be combined with file fallback transports: "
            + ", ".join(file_fallbacks)
        )

    if bool(values.get("REDIS_STREAM_EVENT_BUS_REQUIRED", False)):
        if not bool(values.get("REDIS_STREAM_EVENT_BUS_ENABLED", False)):
            errors.append("REDIS_STREAM_EVENT_BUS_REQUIRED=1 requires REDIS_STREAM_EVENT_BUS_ENABLED=1")
        if not bool(values.get("REDIS_EVENT_BUS_REQUIRED", False)):
            errors.append("REDIS_STREAM_EVENT_BUS_REQUIRED=1 requires REDIS_EVENT_BUS_REQUIRED=1")

    stream_maxlen = int(values.get("REDIS_STREAM_EVENT_MAXLEN", 100_000) or 100_000)
    if bool(values.get("REDIS_STREAM_EVENT_BUS_ENABLED", False)) and stream_maxlen < 100:
        errors.append("REDIS_STREAM_EVENT_MAXLEN must be >= 100 when Redis Streams event bus is enabled")

    if (
        state_backend == "redis"
        and bool(values.get("STATE_BACKEND_FAIL_FAST", False))
        and bool(values.get("STATE_ALLOW_MEMORY_FALLBACK", False))
    ):
        errors.append(
            "STATE_BACKEND=redis with STATE_BACKEND_FAIL_FAST=1 cannot allow memory fallback"
        )

    if errors:
        raise ValueError("Invalid config dependencies: " + " | ".join(errors))


def _build_config_values() -> dict[str, Any]:
    example_values = _parse_env_file(ENV_EXAMPLE_PATH)
    dotenv_values = _parse_env_file(ENV_PATH)
    domain_controls = dict(example_values)
    domain_controls.update(dotenv_values)
    domain_controls.update({key: value for key, value in os.environ.items() if key})
    domain_files_enabled = _bool_from_string(str(domain_controls.get("CONFIG_DOMAIN_FILES_ENABLED", "1")))
    domain_values, domain_layers = (
        _parse_domain_env_files(domain_controls.get("CONFIG_DOMAIN_FILES", ""))
        if domain_files_enabled
        else ({}, ())
    )
    project_values = dict(domain_values)
    project_values.update(dotenv_values)
    _publish_config_warnings(
        _collect_config_warnings(dotenv_values=project_values, example_values=example_values)
    )
    _seed_process_env(project_values)
    selected_profile = _normalize_profile_name(
        os.environ.get("CONFIG_PROFILE")
        or dotenv_values.get("CONFIG_PROFILE")
        or domain_values.get("CONFIG_PROFILE")
        or example_values.get("CONFIG_PROFILE")
        or "development"
    )

    merged_raw = dict(example_values)
    merged_raw.update(domain_values)
    merged_raw.update(dotenv_values)
    merged_raw.update({key: value for key, value in os.environ.items() if key})
    source_layers = []
    if ENV_EXAMPLE_PATH.exists():
        source_layers.append(".env.example")
    source_layers.extend(domain_layers)
    if ENV_PATH.exists():
        source_layers.append(".env")
    source_layers.append("process")

    values: dict[str, Any] = {
        "root_path": str(ROOT_DIR),
        "CONFIG_PROFILE": selected_profile,
        "CONFIG_DOMAIN_FILES_ENABLED": domain_files_enabled,
        "CONFIG_DOMAIN_FILES": ",".join(_csv_items(domain_controls.get("CONFIG_DOMAIN_FILES", "")) or _DOMAIN_ENV_FILES),
        "CONFIG_SOURCE_LAYERS": tuple(source_layers),
        "ENV": str(
            merged_raw.get("APP_ENV")
            or merged_raw.get("FLASK_ENV")
            or os.environ.get("APP_ENV")
            or os.environ.get("FLASK_ENV")
            or "development"
        ).strip()
        or "development",
    }
    values.update(_PROFILE_DEFAULTS.get(selected_profile, {}))

    for key, raw in merged_raw.items():
        if key:
            coerced = _coerce_scalar(key, raw)
            if coerced is not None:
                values[key] = coerced

    database_url = str(merged_raw.get("DATABASE_URL", "") or "").strip()
    if not database_url or _looks_like_placeholder(database_url):
        database_url = f"sqlite:///{SQLITE_DEFAULT_PATH.as_posix()}"
    values["DATABASE_URL"] = database_url
    values["SQLALCHEMY_DATABASE_URI"] = database_url
    values["SQLALCHEMY_TRACK_MODIFICATIONS"] = bool(
        values.get("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    )

    sqlite_busy_timeout = int(values.get("SQLITE_BUSY_TIMEOUT", 5) or 5)
    if str(database_url).lower().startswith("sqlite"):
        values["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "connect_args": {
                "check_same_thread": False,
                "timeout": sqlite_busy_timeout,
            },
        }
    else:
        values["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }

    values["JWT_SECRET_KEY"] = str(
        values.get("JWT_SECRET_KEY")
        or values.get("SECRET_KEY")
        or "replace-with-strong-jwt-secret"
    )
    values["JWT_ACCESS_MINUTES"] = int(values.get("JWT_ACCESS_MINUTES", 720) or 720)
    values["JWT_TOKEN_LOCATION"] = list(
        values.get("JWT_TOKEN_LOCATION") or ["headers", "cookies"]
    )
    values["JWT_COOKIE_CSRF_PROTECT"] = bool(values.get("JWT_COOKIE_CSRF_PROTECT", False))
    values["JWT_COOKIE_SAMESITE"] = str(values.get("JWT_COOKIE_SAMESITE", "Lax") or "Lax")
    values["JWT_COOKIE_MAX_BYTES"] = int(values.get("JWT_COOKIE_MAX_BYTES", 4096) or 4096)

    values["DEBUG"] = bool(values.get("DEBUG", False))
    values["TESTING"] = bool(values.get("TESTING", False))
    values["TRADING_STRATEGY"] = str(values.get("TRADING_STRATEGY", "swing") or "swing")
    values["TRADING_STRATEGY_VERSION"] = str(values.get("TRADING_STRATEGY_VERSION", "") or "")
    values["WEB_CSRF_PROTECT"] = bool(values.get("WEB_CSRF_PROTECT", True))
    values["WEB_SECURITY_HEADERS"] = bool(values.get("WEB_SECURITY_HEADERS", True))
    values["WEB_FORCE_HTTPS"] = bool(values.get("WEB_FORCE_HTTPS", False))
    values["WEB_REVERSE_PROXY_HOPS"] = int(values.get("WEB_REVERSE_PROXY_HOPS", 1) or 1)
    values["WEB_MAX_CONTENT_LENGTH_BYTES"] = int(
        values.get("WEB_MAX_CONTENT_LENGTH_BYTES", 1024 * 1024) or 1024 * 1024
    )
    values["CORS_ORIGINS"] = str(values.get("CORS_ORIGINS", "") or "")

    values["REQUIRED_COMPONENTS"] = list(values.get("REQUIRED_COMPONENTS") or [])

    values["DEVICE_CONFIG_PATH"] = _normalize_path(
        values.get("DEVICE_CONFIG_PATH") or "config/devices.json"
    )
    values["BRAIN_STORAGE_PATH"] = _normalize_path(
        values.get("BRAIN_STORAGE_PATH") or "brain_storage"
    )
    values["FIREWALL_QUEUE_PATH"] = _normalize_path(
        values.get("FIREWALL_QUEUE_PATH") or "runtime/firewall_queue.jsonl"
    )
    values["TRADING_COMMAND_PATH"] = _normalize_path(
        values.get("TRADING_COMMAND_PATH") or "runtime/trading_command.json"
    )
    values["TRADING_STATUS_PATH"] = _normalize_path(
        values.get("TRADING_STATUS_PATH") or "runtime/trading_status.json"
    )
    values["CORE_HEARTBEAT_PATH"] = _normalize_path(
        values.get("CORE_HEARTBEAT_PATH") or "runtime/core_heartbeat.json"
    )
    values["STATE_DB_PATH"] = _normalize_path(
        values.get("STATE_DB_PATH") or "runtime/state_memory.json"
    )
    values["BACKUP_LOCAL_DIR"] = _normalize_path(
        values.get("BACKUP_LOCAL_DIR") or "runtime/backups"
    )

    google_creds = str(values.get("GOOGLE_APPLICATION_CREDENTIALS", "") or "").strip()
    values["GOOGLE_APPLICATION_CREDENTIALS"] = _normalize_path(google_creds) if google_creds else ""
    for path_key in ("PIPER_BIN", "PIPER_MODEL_PATH", "PIPER_CONFIG_PATH"):
        path_value = str(values.get(path_key, "") or "").strip()
        values[path_key] = _normalize_path(path_value) if path_value else ""

    values["REDIS_URL"] = "" if _looks_like_placeholder(str(merged_raw.get("REDIS_URL", "") or "")) else str(
        merged_raw.get("REDIS_URL", "") or ""
    ).strip()

    values["MQTT_HOST"] = str(values.get("MQTT_HOST") or "127.0.0.1")
    values["MQTT_PORT"] = int(values.get("MQTT_PORT", 1883) or 1883)
    values["TOPIK_ARUS"] = str(values.get("TOPIK_ARUS") or "elara/iot/arus")
    values["TOPIK_SUHU"] = str(values.get("TOPIK_SUHU") or "elara/iot/suhu")
    values["STREAM_BIND_HOST"] = str(values.get("STREAM_BIND_HOST") or "127.0.0.1")
    values["TG_ALLOWED_USER"] = int(values.get("TG_ALLOWED_USER", 0) or 0)

    values["ALLOW_DEV_BACKDOOR"] = bool(values.get("ALLOW_DEV_BACKDOOR", False))
    values["DB_AUTO_CREATE_TABLES"] = bool(values.get("DB_AUTO_CREATE_TABLES", True))
    values["JWT_REVOKE_ENABLED"] = bool(values.get("JWT_REVOKE_ENABLED", True))
    values["ALLOW_GUEST_DASHBOARD"] = bool(values.get("ALLOW_GUEST_DASHBOARD", False))
    values["WEB_MARKET_ALLOW_BLOCKING_FALLBACK"] = bool(
        values.get("WEB_MARKET_ALLOW_BLOCKING_FALLBACK", False)
    )
    values["WEB_ADMIN_MFA_REQUIRED"] = bool(values.get("WEB_ADMIN_MFA_REQUIRED", True))
    values["WEB_REQUIRE_EMAIL_VERIFICATION"] = bool(
        values.get("WEB_REQUIRE_EMAIL_VERIFICATION", True)
    )
    values["PRESENCE_ENABLED"] = bool(values.get("PRESENCE_ENABLED", False))
    values["PRESENCE_IPS"] = str(values.get("PRESENCE_IPS", "") or "")
    values["PRESENCE_INTERVAL_SEC"] = int(values.get("PRESENCE_INTERVAL_SEC", 15) or 15)
    values["PRESENCE_AWAY_AFTER_SEC"] = int(values.get("PRESENCE_AWAY_AFTER_SEC", 300) or 300)
    values["PRESENCE_COOLDOWN_SEC"] = int(values.get("PRESENCE_COOLDOWN_SEC", 60) or 60)
    values["PRESENCE_ONLINE_COMMANDS"] = str(values.get("PRESENCE_ONLINE_COMMANDS", "") or "")
    values["PRESENCE_OFFLINE_COMMANDS"] = str(values.get("PRESENCE_OFFLINE_COMMANDS", "") or "")
    values["WEB_ACTIVITY_ALERT_TG_ENABLED"] = bool(
        values.get("WEB_ACTIVITY_ALERT_TG_ENABLED", False)
    )
    values["WEB_EXPOSE_DEV_AUTH_TOKENS"] = bool(
        values.get("WEB_EXPOSE_DEV_AUTH_TOKENS", False)
    )
    values["WEB_MFA_TOTP_PERIOD_SEC"] = int(values.get("WEB_MFA_TOTP_PERIOD_SEC", 30) or 30)
    values["WEB_MFA_TOTP_WINDOW_STEPS"] = int(values.get("WEB_MFA_TOTP_WINDOW_STEPS", 1) or 1)

    values["ADMIN_BOOTSTRAP_EMAIL"] = str(values.get("ADMIN_BOOTSTRAP_EMAIL", "") or "")
    values["ADMIN_BOOTSTRAP_PASSWORD"] = str(values.get("ADMIN_BOOTSTRAP_PASSWORD", "") or "")
    values["ADMIN_BOOTSTRAP_NAME"] = str(values.get("ADMIN_BOOTSTRAP_NAME", "Elara Admin") or "Elara Admin")
    values["ADMIN_BOOTSTRAP_ROLE"] = str(values.get("ADMIN_BOOTSTRAP_ROLE", "admin") or "admin")
    values["VOICE_STT_ENGINE"] = str(values.get("VOICE_STT_ENGINE", "google") or "google")
    values["VOICE_TTS_ENGINE"] = str(values.get("VOICE_TTS_ENGINE", "gtts") or "gtts")
    values["WEATHER_API_KEY"] = str(values.get("WEATHER_API_KEY", "") or "")
    values["BACKUP_ENABLED"] = bool(values.get("BACKUP_ENABLED", False))
    values["BACKUP_HOUR"] = int(values.get("BACKUP_HOUR", 2) or 2)
    values["BACKUP_MINUTE"] = int(values.get("BACKUP_MINUTE", 15) or 15)
    values["BACKUP_RETENTION_DAYS"] = int(values.get("BACKUP_RETENTION_DAYS", 7) or 7)
    values["BACKUP_PG_DUMP_TIMEOUT_SEC"] = int(values.get("BACKUP_PG_DUMP_TIMEOUT_SEC", 120) or 120)
    values["BACKUP_GDRIVE_FOLDER_ID"] = str(values.get("BACKUP_GDRIVE_FOLDER_ID", "") or "")
    values["CRITICAL_CONFIRM_ENABLED"] = bool(values.get("CRITICAL_CONFIRM_ENABLED", True))
    values["CRITICAL_CONFIRM_TTL_SEC"] = int(values.get("CRITICAL_CONFIRM_TTL_SEC", 120) or 120)
    values["CRITICAL_CONFIRM_TOKEN_BYTES"] = int(values.get("CRITICAL_CONFIRM_TOKEN_BYTES", 3) or 3)
    values["CRITICAL_CONFIRM_INTENTS"] = str(values.get("CRITICAL_CONFIRM_INTENTS", "") or "")
    values["CRITICAL_CONFIRM_TOOLS"] = str(values.get("CRITICAL_CONFIRM_TOOLS", "flatten_all_positions") or "")
    values["WEB_REVERSE_PROXY_REQUIRED_IN_PROD"] = bool(
        values.get("WEB_REVERSE_PROXY_REQUIRED_IN_PROD", True)
    )
    values["DB_ALLOW_SQLITE"] = bool(values.get("DB_ALLOW_SQLITE", True))
    values["DB_REQUIRE_SERVER_FOR_PROD"] = bool(values.get("DB_REQUIRE_SERVER_FOR_PROD", False))
    values["DB_REQUIRE_SERVER_FOR_LIVE_TRADING"] = bool(
        values.get("DB_REQUIRE_SERVER_FOR_LIVE_TRADING", False)
    )
    values["PROMPT_INJECTION_GUARD_ENABLED"] = bool(
        values.get("PROMPT_INJECTION_GUARD_ENABLED", True)
    )
    values["ML_ARTIFACT_SIGNING_KEY"] = str(values.get("ML_ARTIFACT_SIGNING_KEY", "") or "")
    values["ML_REQUIRE_SIGNED_ARTIFACTS"] = bool(values.get("ML_REQUIRE_SIGNED_ARTIFACTS", False))
    values["ML_ARTIFACT_MAX_BYTES"] = int(values.get("ML_ARTIFACT_MAX_BYTES", 100 * 1024 * 1024) or 0)
    values["ML_ARTIFACT_ALLOWED_CLASS_PREFIXES"] = str(
        values.get("ML_ARTIFACT_ALLOWED_CLASS_PREFIXES", "sklearn.") or "sklearn."
    )
    values["ML_ARTIFACT_ALLOW_LEGACY_PICKLE"] = bool(
        values.get("ML_ARTIFACT_ALLOW_LEGACY_PICKLE", False)
    )
    values["ML_ARTIFACT_ALLOW_EXTERNAL_PATHS"] = bool(
        values.get("ML_ARTIFACT_ALLOW_EXTERNAL_PATHS", True)
    )
    values["ML_ARTIFACT_IO_CHUNK_SIZE"] = int(
        values.get("ML_ARTIFACT_IO_CHUNK_SIZE", 1024 * 1024) or 1024 * 1024
    )
    values["OPENBB_ENABLED"] = bool(values.get("OPENBB_ENABLED", False))
    values["OPENBB_ONLY"] = bool(values.get("OPENBB_ONLY", False))
    values["OPENBB_STRICT"] = bool(values.get("OPENBB_STRICT", False))
    values["CRYPTO_SCREEN_USE_OPENBB"] = bool(values.get("CRYPTO_SCREEN_USE_OPENBB", False))
    values["RUNTIME_EVENT_STORE_ENABLED"] = bool(values.get("RUNTIME_EVENT_STORE_ENABLED", True))
    values["SQLITE_BUSY_TIMEOUT"] = int(values.get("SQLITE_BUSY_TIMEOUT", 5) or 5)
    values["TRADER_API_TIMEOUT_SEC"] = float(values.get("TRADER_API_TIMEOUT_SEC", 2.0) or 2.0)
    values["TRADER_REQUIRE_CORE_HEARTBEAT"] = bool(values.get("TRADER_REQUIRE_CORE_HEARTBEAT", False))
    values["TRADER_CORE_HEARTBEAT_STALE_SEC"] = int(
        values.get("TRADER_CORE_HEARTBEAT_STALE_SEC", 30) or 30
    )
    values["TRADER_CORE_HEARTBEAT_CHECK_SEC"] = int(
        values.get("TRADER_CORE_HEARTBEAT_CHECK_SEC", 5) or 5
    )
    values["TRADER_CORE_HEARTBEAT_GRACE_SEC"] = int(
        values.get("TRADER_CORE_HEARTBEAT_GRACE_SEC", 10) or 10
    )
    values["LLM_AGENT_ALLOW_CRITICAL_TOOLS"] = bool(values.get("LLM_AGENT_ALLOW_CRITICAL_TOOLS", False))
    values["LLM_AGENT_MAX_LOOPS"] = int(values.get("LLM_AGENT_MAX_LOOPS", 3) or 3)
    values["LLM_AGENT_MAX_TOTAL_TOOL_CALLS"] = int(values.get("LLM_AGENT_MAX_TOTAL_TOOL_CALLS", 8) or 8)
    values["LLM_AGENT_TOOL_ALLOWLIST"] = str(values.get("LLM_AGENT_TOOL_ALLOWLIST", "") or "")
    values["LLM_AGENT_TOOL_DENYLIST"] = str(values.get("LLM_AGENT_TOOL_DENYLIST", "") or "")
    values["LLM_HISTORY_TURNS"] = int(values.get("LLM_HISTORY_TURNS", 14) or 14)
    values["LLM_HISTORY_MAX_CHARS"] = int(values.get("LLM_HISTORY_MAX_CHARS", 7000) or 7000)
    values["LLM_HISTORY_MAX_TOKENS"] = int(values.get("LLM_HISTORY_MAX_TOKENS", 4000) or 4000)
    values["LLM_HISTORY_SUMMARY_ENABLED"] = bool(values.get("LLM_HISTORY_SUMMARY_ENABLED", True))
    values["LLM_HISTORY_SUMMARY_MAX_CHARS"] = int(
        values.get("LLM_HISTORY_SUMMARY_MAX_CHARS", 1600) or 1600
    )
    values["LLM_MARKDOWN_SKILLS_INCLUDE"] = str(values.get("LLM_MARKDOWN_SKILLS_INCLUDE", "") or "")
    values["LLM_MARKDOWN_SKILLS_EXCLUDE"] = str(values.get("LLM_MARKDOWN_SKILLS_EXCLUDE", "") or "")
    values["LLM_MARKDOWN_SKILLS_MAX_COUNT"] = int(values.get("LLM_MARKDOWN_SKILLS_MAX_COUNT", 1200) or 1200)
    values["LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS"] = int(
        values.get("LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS", 6000) or 6000
    )
    values["LLM_MARKDOWN_SKILLS_REFRESH_SEC"] = int(
        values.get("LLM_MARKDOWN_SKILLS_REFRESH_SEC", 30) or 30
    )
    values["LLM_MARKDOWN_SKILLS_PROFILE"] = str(
        values.get("LLM_MARKDOWN_SKILLS_PROFILE", "default") or "default"
    )
    values["LLM_MARKDOWN_MIN_TRUST"] = str(values.get("LLM_MARKDOWN_MIN_TRUST", "standard") or "standard")
    values["LLM_MARKDOWN_DEFAULT_TRUST"] = str(
        values.get("LLM_MARKDOWN_DEFAULT_TRUST", "standard") or "standard"
    )
    for key in (
        "LLM_MARKDOWN_BLOCKED_SKILLS",
        "LLM_MARKDOWN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_PROFILE_SKILL_RULES",
        "LLM_MARKDOWN_USER_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_ROLE_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_USER_ROLE_OVERRIDES",
        "LLM_MARKDOWN_USER_TENANT_OVERRIDES",
        "LLM_MARKDOWN_TENANT_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_TENANT_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_TENANT_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_ESCALATION_ROLES",
        "LLM_MARKDOWN_ESCALATION_PHRASES",
    ):
        values[key] = str(values.get(key, "") or "")
    values["LLM_MARKDOWN_ESCALATION_ENABLED"] = bool(
        values.get("LLM_MARKDOWN_ESCALATION_ENABLED", False)
    )
    values["LLM_MARKDOWN_PROMPT_GUARD_ENABLED"] = bool(
        values.get("LLM_MARKDOWN_PROMPT_GUARD_ENABLED", False)
    )
    values["LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY"] = bool(
        values.get("LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY", False)
    )
    values["LLM_MARKDOWN_ROUTER_ENABLED"] = bool(values.get("LLM_MARKDOWN_ROUTER_ENABLED", False))
    values["LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED"] = bool(
        values.get("LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED", True)
    )
    values["LLM_MARKDOWN_FEEDBACK_MATCH_WEIGHT"] = float(
        values.get("LLM_MARKDOWN_FEEDBACK_MATCH_WEIGHT", 0.35) or 0.35
    )
    values["LLM_MARKDOWN_FEEDBACK_ROUTER_MIN_SAMPLES"] = int(
        values.get("LLM_MARKDOWN_FEEDBACK_ROUTER_MIN_SAMPLES", 3) or 3
    )
    values["LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_BOOST"] = int(
        values.get("LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_BOOST", 45) or 45
    )
    values["LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_PENALTY"] = int(
        values.get("LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_PENALTY", 60) or 60
    )
    values["LLM_MARKDOWN_SKILLS_WATCH_ENABLED"] = bool(
        values.get("LLM_MARKDOWN_SKILLS_WATCH_ENABLED", False)
    )
    values["LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC"] = int(
        values.get("LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC", 30) or 30
    )
    values["LLM_MARKDOWN_SKILLS_DIRS"] = str(values.get("LLM_MARKDOWN_SKILLS_DIRS", "") or "")
    values["DISABLED_SKILLS"] = str(values.get("DISABLED_SKILLS", "") or "")
    values["DISABLED_INTENTS"] = str(values.get("DISABLED_INTENTS", "") or "")
    values["STATE_REQUIRE_REDIS_FOR_PROD"] = bool(values.get("STATE_REQUIRE_REDIS_FOR_PROD", False))
    values["STATE_REQUIRE_REDIS_FOR_LIVE_TRADING"] = bool(
        values.get("STATE_REQUIRE_REDIS_FOR_LIVE_TRADING", False)
    )
    values["MARKET_ORDER_MAX_SPREAD_PCT"] = float(
        values.get("MARKET_ORDER_MAX_SPREAD_PCT", values.get("SPREAD_MAX_PCT", 0.5)) or 0.5
    )
    values["TRADING_COMMAND_POP_MAX_BATCH"] = int(
        values.get("TRADING_COMMAND_POP_MAX_BATCH", 1000) or 1000
    )
    values["STOCK_SCREEN_WORKERS"] = int(values.get("STOCK_SCREEN_WORKERS", 4) or 4)
    values["STOCK_SCREEN_LIMIT"] = int(values.get("STOCK_SCREEN_LIMIT", 12) or 12)
    values["STOCK_MIN_PRICE"] = float(values.get("STOCK_MIN_PRICE", 50) or 50)
    values["STOCK_MAX_PRICE"] = float(values.get("STOCK_MAX_PRICE", 100000) or 100000)
    values["STOCK_ATR_PCT_MIN"] = float(values.get("STOCK_ATR_PCT_MIN", 1.0) or 1.0)
    values["STOCK_MIN_LIQUIDITY_IDR"] = float(
        values.get("STOCK_MIN_LIQUIDITY_IDR", 1_000_000_000) or 1_000_000_000
    )
    values["STOCK_VOLUME_LOT_MULT"] = float(values.get("STOCK_VOLUME_LOT_MULT", 1.0) or 1.0)
    values["STOCK_VOL_SPIKE_MULT"] = float(values.get("STOCK_VOL_SPIKE_MULT", 1.5) or 1.5)
    values["STOCK_VCP_STD_PCT"] = float(values.get("STOCK_VCP_STD_PCT", 0.035) or 0.035)
    values["STOCK_RS_DAYS"] = int(values.get("STOCK_RS_DAYS", 60) or 60)
    values["STOCK_MAX_DER"] = float(values.get("STOCK_MAX_DER", 1.0) or 1.0)
    values["STOCK_MIN_ROE"] = float(values.get("STOCK_MIN_ROE", 10.0) or 10.0)
    values["STOCK_MAX_POSITION_NOTIONAL_IDR"] = float(
        values.get("STOCK_MAX_POSITION_NOTIONAL_IDR", 0) or 0
    )
    values["STOCK_RISK_BUDGET_IDR"] = float(values.get("STOCK_RISK_BUDGET_IDR", 0) or 0)
    values["CRYPTO_MAX_POSITION_NOTIONAL_USDT"] = float(
        values.get("CRYPTO_MAX_POSITION_NOTIONAL_USDT", 0) or 0
    )
    values["CRYPTO_RISK_BUDGET_USDT"] = float(values.get("CRYPTO_RISK_BUDGET_USDT", 0) or 0)
    values["CRYPTO_MAX_LIQUIDITY_SHARE"] = float(values.get("CRYPTO_MAX_LIQUIDITY_SHARE", 0.1) or 0.1)
    values["STATE_ENCRYPTION_ENABLED"] = bool(values.get("STATE_ENCRYPTION_ENABLED", False))
    values["STATE_ENCRYPTION_REQUIRED"] = bool(values.get("STATE_ENCRYPTION_REQUIRED", False))
    values["STATE_ENCRYPTION_KEY"] = str(values.get("STATE_ENCRYPTION_KEY", "") or "")
    values["SELF_HEAL_CONFIG_DRIFT_ENABLED"] = bool(
        values.get("SELF_HEAL_CONFIG_DRIFT_ENABLED", False)
    )
    values["SELF_HEAL_CONFIG_DRIFT_TARGETS"] = str(
        values.get("SELF_HEAL_CONFIG_DRIFT_TARGETS", "") or ""
    )
    values["SELF_DEPLOY_SLO_ENFORCE"] = bool(values.get("SELF_DEPLOY_SLO_ENFORCE", False))
    values["SELF_DEPLOY_SLO_MAX_HEAL_DURATION_MS"] = int(
        values.get("SELF_DEPLOY_SLO_MAX_HEAL_DURATION_MS", 600000) or 600000
    )
    values["SELF_DEPLOY_SLO_MAX_WARNING_COUNT"] = int(
        values.get("SELF_DEPLOY_SLO_MAX_WARNING_COUNT", 5) or 5
    )
    values["SELF_DEPLOY_SLO_MIN_HEALTH_SCORE"] = float(
        values.get("SELF_DEPLOY_SLO_MIN_HEALTH_SCORE", 0.8) or 0.8
    )

    _validate_config_dependencies(values)
    _apply_config_governance(values)
    return values


class _ConfigMeta(type):
    def __getattr__(cls, name: str) -> Any:
        if name in cls._values:
            return cls._values[name]
        if name.isupper():
            if name in _LIST_KEYS:
                return []
            if _is_stringish_key(name):
                return ""
        raise AttributeError(name)

    def __setattr__(cls, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "_values":
            return
        if name.isupper() or name == "root_path":
            cls._values[name] = value

    def __dir__(cls) -> list[str]:
        return sorted(set(super().__dir__()) | set(cls._values.keys()))


class Config(metaclass=_ConfigMeta):
    _values: dict[str, Any] = {}
    root_path = str(ROOT_DIR)

    @classmethod
    def as_dict(cls) -> dict[str, Any]:
        return dict(cls._values)

    @classmethod
    def reload(cls) -> None:
        fresh = _build_config_values()
        cls._values.clear()
        for key, value in fresh.items():
            setattr(cls, key, value)
        cls._write_config_snapshot_audit()

    @classmethod
    def _write_config_snapshot_audit(cls) -> None:
        if not bool(cls._values.get("CONFIG_SNAPSHOT_AUDIT_ENABLED", False)):
            return
        path = Path(_normalize_path(cls._values.get("CONFIG_SNAPSHOT_AUDIT_PATH") or "runtime/config_snapshots.jsonl"))
        event = {
            "schema": "elara.config.audit.v1",
            "created_at_unix": time.time(),
            "profile": cls.profile_name(),
            "config_hash": cls.config_hash(),
            "registry_hash": cls.config_registry_hash(),
            "source_layers": tuple(cls._values.get("CONFIG_SOURCE_LAYERS") or ()),
            "validation": cls.config_governance_report(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(_stable_config_json(event) + "\n")
        except Exception:
            log.warning("Failed to append config snapshot audit: %s", path, exc_info=True)

    @classmethod
    def profile_name(cls) -> str:
        return _normalize_profile_name(cls._values.get("CONFIG_PROFILE", "development"))

    @classmethod
    def profile_defaults(cls) -> dict[str, Any]:
        return dict(_PROFILE_DEFAULTS.get(cls.profile_name(), {}))

    @classmethod
    def identity_summary(cls) -> str:
        identity = str(cls._values.get("APP_IDENTITY", "") or "").strip()
        if identity:
            return identity
        return str(_PROFILE_DEFAULTS["development"].get("APP_IDENTITY", "") or "").strip()

    @classmethod
    def core_keys(cls) -> tuple[str, ...]:
        return tuple(_CORE_CONFIG_KEYS)

    @classmethod
    def core_config(cls) -> dict[str, Any]:
        return {key: cls._values.get(key) for key in _CORE_CONFIG_KEYS}

    @classmethod
    def advanced_config(cls) -> dict[str, Any]:
        hidden = set(_CORE_CONFIG_KEYS) | {"root_path"}
        return {key: value for key, value in cls._values.items() if key not in hidden}

    @classmethod
    def config_layers(cls) -> dict[str, dict[str, Any]]:
        return {
            "core": cls.core_config(),
            "advanced": cls.advanced_config(),
        }

    @classmethod
    def trading_config(cls) -> TradingConfigSchema:
        return TradingConfigSchema(
            profile=cls.profile_name(),
            mode=str(cls._values.get("TRADING_MODE", "") or ""),
            symbol=str(cls._values.get("TRADING_SYMBOL", "") or ""),
            strategy=str(cls._values.get("TRADING_STRATEGY", "") or ""),
            daily_max_loss_pct=float(cls._values.get("DAILY_MAX_LOSS_PCT", 0.0) or 0.0),
            max_drawdown_pct=float(cls._values.get("MAX_DRAWDOWN_PCT", 0.0) or 0.0),
            life_min_sleep_hours=float(cls._values.get("LIFE_MIN_SLEEP_HOURS", 0.0) or 0.0),
            life_max_stress=float(cls._values.get("LIFE_MAX_STRESS", 0.0) or 0.0),
            trading_skill_enabled=bool(cls._values.get("ENABLE_TRADING_SKILL", False)),
        )

    @classmethod
    def infra_config(cls) -> InfraConfigSchema:
        return InfraConfigSchema(
            environment=str(cls._values.get("ENV", "") or ""),
            state_backend=str(cls._values.get("STATE_BACKEND", "") or ""),
            redis_event_bus_required=bool(cls._values.get("REDIS_EVENT_BUS_REQUIRED", False)),
            state_backend_fail_fast=bool(cls._values.get("STATE_BACKEND_FAIL_FAST", False)),
            database_configured=bool(str(cls._values.get("DATABASE_URL", "") or "").strip()),
            redis_configured=bool(str(cls._values.get("REDIS_URL", "") or "").strip()),
            db_auto_create_tables=bool(cls._values.get("DB_AUTO_CREATE_TABLES", False)),
            trading_status_path=str(cls._values.get("TRADING_STATUS_PATH", "") or ""),
            trading_command_path=str(cls._values.get("TRADING_COMMAND_PATH", "") or ""),
            core_heartbeat_path=str(cls._values.get("CORE_HEARTBEAT_PATH", "") or ""),
            file_fallback_enabled=bool(
                cls._values.get("TRADING_FILE_IPC_FALLBACK_ENABLED", False)
                or cls._values.get("TRADING_STATUS_FILE_FALLBACK_ENABLED", False)
                or cls._values.get("CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", False)
            ),
        )

    @classmethod
    def security_config(cls) -> SecurityConfigSchema:
        return SecurityConfigSchema(
            secret_key_configured=_is_configured_secret(cls._values.get("SECRET_KEY")),
            jwt_secret_configured=_is_configured_secret(cls._values.get("JWT_SECRET_KEY")),
            fail_fast_startup=bool(cls._values.get("FAIL_FAST_STARTUP", False)),
            web_force_https=bool(cls._values.get("WEB_FORCE_HTTPS", False)),
            web_reverse_proxy_enabled=bool(cls._values.get("WEB_REVERSE_PROXY_ENABLED", False)),
            web_allowed_hosts=_csv_items(cls._values.get("WEB_ALLOWED_HOSTS")),
            cors_origins=_csv_items(cls._values.get("CORS_ORIGINS")),
            csrf_protect=bool(cls._values.get("WEB_CSRF_PROTECT", False)),
            security_headers=bool(cls._values.get("WEB_SECURITY_HEADERS", False)),
            jwt_cookie_csrf_protect=bool(cls._values.get("JWT_COOKIE_CSRF_PROTECT", False)),
        )

    @classmethod
    def llm_config(cls) -> LLMConfigSchema:
        return LLMConfigSchema(
            primary_provider=str(cls._values.get("LLM_PRIMARY_PROVIDER", "") or ""),
            nlu_providers=_csv_items(cls._values.get("LLM_NLU_PROVIDERS")),
            openai_configured=_is_configured_secret(cls._values.get("OPENAI_API_KEY")),
            groq_configured=_is_configured_secret(cls._values.get("GROQ_API_KEY")),
            gemini_configured=_is_configured_secret(cls._values.get("GEMINI_API_KEY")),
            allow_no_api_key_in_dev=bool(cls._values.get("LLM_ALLOW_NO_API_KEY_IN_DEV", False)),
        )

    @classmethod
    def config_schema(cls) -> AppConfigSchema:
        return AppConfigSchema(
            identity=cls.identity_summary(),
            product_focus=str(cls._values.get("APP_PRODUCT_FOCUS", "") or ""),
            assistant_role=str(cls._values.get("APP_ASSISTANT_ROLE", "") or ""),
            trading=cls.trading_config(),
            infra=cls.infra_config(),
            security=cls.security_config(),
            llm=cls.llm_config(),
        )

    @classmethod
    def grouped_config(cls) -> dict[str, Any]:
        return cls.config_schema().as_dict()

    @classmethod
    def grouped_config_version(cls) -> str:
        return _GROUPED_CONFIG_VERSION

    @classmethod
    def grouped_config_contract(cls) -> dict[str, tuple[str, ...]]:
        return {
            "trading": tuple(field.name for field in fields(TradingConfigSchema)),
            "infra": tuple(field.name for field in fields(InfraConfigSchema)),
            "security": tuple(field.name for field in fields(SecurityConfigSchema)),
            "llm": tuple(field.name for field in fields(LLMConfigSchema)),
        }

    @classmethod
    def grouped_config_v1(cls) -> dict[str, Any]:
        return {
            "version": cls.grouped_config_version(),
            "identity": cls.identity_summary(),
            "product_focus": str(cls._values.get("APP_PRODUCT_FOCUS", "") or ""),
            "assistant_role": str(cls._values.get("APP_ASSISTANT_ROLE", "") or ""),
            "contract": cls.grouped_config_contract(),
            "config": cls.grouped_config(),
        }

    @classmethod
    def config_registry(cls) -> ConfigRegistry:
        return _config_registry_for_values(cls._values)

    @classmethod
    def config_domain_contract(cls) -> dict[str, tuple[str, ...]]:
        return registry_contract()

    @classmethod
    def config_registry_snapshot(cls, *, redacted: bool = True) -> dict[str, Any]:
        return cls.config_registry().snapshot(redacted=redacted)

    @classmethod
    def config_registry_hash(cls) -> str:
        if str(cls._values.get("CONFIG_REGISTRY_HASH", "") or "").strip():
            return str(cls._values.get("CONFIG_REGISTRY_HASH"))
        return cls.config_registry().config_hash()

    @classmethod
    def config_governance_report(cls) -> dict[str, Any]:
        report = cls._values.get("CONFIG_GOVERNANCE_REPORT")
        if isinstance(report, dict):
            return _config_json_safe(report)
        return _config_json_safe(asdict(cls.config_registry().validate()))

    @classmethod
    def config_feature_map(cls) -> dict[str, tuple[str, ...]]:
        return cls.config_registry().feature_map()

    @classmethod
    def deterministic_config_snapshot(cls) -> dict[str, Any]:
        snapshot = {
            "schema": "elara.config.snapshot.v1",
            "profile": cls.profile_name(),
            "runtime_mode": str(cls._values.get("TRADING_MODE", "") or ""),
            "strategy": str(cls._values.get("TRADING_STRATEGY", "") or ""),
            "strategy_version": str(cls._values.get("TRADING_STRATEGY_VERSION", "") or ""),
            "grouped_config_version": cls.grouped_config_version(),
            "config_registry_hash": cls.config_registry_hash(),
            "source_layers": tuple(cls._values.get("CONFIG_SOURCE_LAYERS") or ()),
            "feature_flags": _enabled_feature_flags(cls._values),
            "feature_map": cls.config_feature_map(),
            "validation": cls.config_governance_report(),
            "config": _redact_config_tree(cls.grouped_config()),
        }
        return _config_json_safe(snapshot)

    @classmethod
    def config_hash(cls) -> str:
        return hashlib.sha256(
            _stable_config_json(cls.deterministic_config_snapshot()).encode("utf-8")
        ).hexdigest()

    @classmethod
    def config_warnings(cls) -> dict[str, tuple[str, ...]]:
        return {key: tuple(value) for key, value in _CONFIG_WARNINGS.items()}

    @classmethod
    def unknown_config_keys(cls) -> tuple[str, ...]:
        return tuple(_CONFIG_WARNINGS.get("unknown") or ())

    @classmethod
    def deprecated_config_keys(cls) -> tuple[str, ...]:
        return tuple(_CONFIG_WARNINGS.get("deprecated") or ())


Config.reload()

