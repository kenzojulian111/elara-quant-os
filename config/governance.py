from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from config.pydantic_settings_schema import validate_registry_with_pydantic


SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY")
EXPERIMENTAL_KEYS = (
    "ENABLE_VOICE",
    "ENABLE_STREAM",
    "PRESENCE_ENABLED",
    "LLM_MARKDOWN_SKILLS_ENABLED",
    "LLM_MARKDOWN_ROUTER_ENABLED",
    "LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED",
    "LLM_MARKDOWN_SKILLS_WATCH_ENABLED",
    "LLM_MARKDOWN_ESCALATION_ENABLED",
    "EXPERIMENTAL_AUTO_RESEARCH_ENABLED",
    "EXPERIMENTAL_MULTIMODAL_ENABLED",
)


@dataclass(frozen=True)
class ConfigKeyMetadata:
    key: str
    domain: str
    secret: bool = False
    deprecated_since: str = ""
    replacement_key: str = ""
    removal_version: str = ""
    description: str = ""

    @property
    def deprecated(self) -> bool:
        return bool(self.deprecated_since or self.replacement_key or self.removal_version)


@dataclass(frozen=True)
class DomainConfig:
    domain: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True)
class CoreDomainConfig(DomainConfig):
    domain: str = "core"
    app_identity: str = ""
    product_focus: str = ""
    assistant_role: str = ""
    enable_trading_skill: bool = False
    enable_knowledge_skill: bool = False
    required_components: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeDomainConfig(DomainConfig):
    domain: str = "runtime"
    environment: str = "development"
    config_profile: str = "development"
    state_backend: str = "auto"
    redis_event_bus_required: bool = False
    redis_stream_event_bus_enabled: bool = False
    redis_stream_event_bus_required: bool = False
    redis_stream_event_key: str = ""
    redis_stream_event_maxlen: int = 100_000
    state_backend_fail_fast: bool = False
    file_fallback_enabled: bool = False
    runtime_event_store_enabled: bool = True
    runtime_event_log_path: str = ""
    fail_fast_startup: bool = False

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.state_backend not in {"auto", "file", "memory", "redis"}:
            errors.append(f"runtime.state_backend.invalid={self.state_backend}")
        if self.redis_event_bus_required and self.file_fallback_enabled:
            errors.append("runtime.transport_conflict=redis_required_with_file_fallback")
        if self.redis_stream_event_bus_required and not self.redis_stream_event_bus_enabled:
            errors.append("runtime.stream_bus_required_but_disabled")
        if self.redis_stream_event_bus_required and not self.redis_event_bus_required:
            errors.append("runtime.stream_bus_requires_redis_event_bus")
        if self.redis_stream_event_bus_enabled and self.redis_stream_event_maxlen < 100:
            errors.append("runtime.stream_bus_maxlen.too_low")
        if self.state_backend == "redis" and self.state_backend_fail_fast and self.file_fallback_enabled:
            errors.append("runtime.fail_fast_conflict=redis_fail_fast_with_file_fallback")
        return tuple(errors)


@dataclass(frozen=True)
class TradingDomainConfig(DomainConfig):
    domain: str = "trading"
    mode: str = "paper"
    symbol: str = ""
    strategy: str = ""
    strategy_version: str = ""
    daily_max_loss_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    execution_dedup_ttl_sec: int = 600
    ipc_enabled: bool = False
    ipc_host: str = ""
    ipc_port: int = 8768

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.mode not in {"paper", "live", "backtest", "simulation"}:
            errors.append(f"trading.mode.invalid={self.mode}")
        if self.daily_max_loss_pct < 0:
            errors.append("trading.daily_max_loss_pct.negative")
        if self.max_drawdown_pct < 0:
            errors.append("trading.max_drawdown_pct.negative")
        if self.execution_dedup_ttl_sec < 30:
            errors.append("trading.execution_dedup_ttl_sec.too_low")
        return tuple(errors)


@dataclass(frozen=True)
class GovernanceDomainConfig(DomainConfig):
    domain: str = "governance"
    life_min_sleep_hours: float = 0.0
    life_max_stress: float = 0.0
    critical_confirm_enabled: bool = True
    critical_confirm_ttl_sec: int = 120
    prompt_injection_guard_enabled: bool = True
    runtime_complexity_max_experimental: int = 1

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.life_min_sleep_hours < 0:
            errors.append("governance.life_min_sleep_hours.negative")
        if self.life_max_stress < 0:
            errors.append("governance.life_max_stress.negative")
        if self.runtime_complexity_max_experimental < 0:
            errors.append("governance.runtime_complexity_max_experimental.negative")
        return tuple(errors)


@dataclass(frozen=True)
class ObservabilityDomainConfig(DomainConfig):
    domain: str = "observability"
    log_json: bool = False
    observability_enabled: bool = True
    prometheus_metrics_enabled: bool = True
    prometheus_metrics_public: bool = False
    otel_enabled: bool = False
    otel_service_name: str = ""
    slow_request_ms: float = 1000.0

    def validate(self) -> tuple[str, ...]:
        if self.slow_request_ms <= 0:
            return ("observability.slow_request_ms.non_positive",)
        return ()


@dataclass(frozen=True)
class LLMDomainConfig(DomainConfig):
    domain: str = "llm"
    primary_provider: str = ""
    tool_calling_enabled: bool = False
    agent_orchestrator_enabled: bool = False
    markdown_skills_enabled: bool = False
    openai_configured: bool = False
    groq_configured: bool = False
    gemini_configured: bool = False


@dataclass(frozen=True)
class BlockchainDomainConfig(DomainConfig):
    domain: str = "blockchain"
    enabled: bool = False
    provider_url_configured: bool = False
    wallet_configured: bool = False


@dataclass(frozen=True)
class ExperimentalDomainConfig(DomainConfig):
    domain: str = "experimental"
    enabled_flags: tuple[str, ...] = ()
    max_enabled: int = 1
    isolated: bool = True

    def validate(self) -> tuple[str, ...]:
        if len(self.enabled_flags) > max(0, int(self.max_enabled)):
            return (f"experimental.too_many_enabled={len(self.enabled_flags)}>{self.max_enabled}",)
        return ()


@dataclass(frozen=True)
class SecretsDomainConfig(DomainConfig):
    domain: str = "secrets"
    configured_keys: tuple[str, ...] = ()
    placeholder_keys: tuple[str, ...] = ()

    def validate(self) -> tuple[str, ...]:
        if self.placeholder_keys:
            return tuple(f"secrets.placeholder={key}" for key in self.placeholder_keys)
        return ()


@dataclass(frozen=True)
class ConfigValidationReport:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    unknown_keys: tuple[str, ...] = ()
    deprecated_keys: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ConfigRegistry:
    core: CoreDomainConfig
    runtime: RuntimeDomainConfig
    trading: TradingDomainConfig
    governance: GovernanceDomainConfig
    observability: ObservabilityDomainConfig
    llm: LLMDomainConfig
    blockchain: BlockchainDomainConfig
    experimental: ExperimentalDomainConfig
    secrets: SecretsDomainConfig
    metadata: dict[str, ConfigKeyMetadata] = field(default_factory=dict)
    unknown_keys: tuple[str, ...] = ()
    deprecated_keys: tuple[str, ...] = ()
    source_layers: tuple[str, ...] = ()

    def domains(self) -> dict[str, DomainConfig]:
        return {
            "core": self.core,
            "runtime": self.runtime,
            "trading": self.trading,
            "governance": self.governance,
            "observability": self.observability,
            "llm": self.llm,
            "blockchain": self.blockchain,
            "experimental": self.experimental,
            "secrets": self.secrets,
        }

    def validate(self) -> ConfigValidationReport:
        errors: list[str] = []
        warnings: list[str] = []
        for domain in self.domains().values():
            for error in domain.validate():
                if error.startswith("secrets.placeholder"):
                    warnings.append(error)
                else:
                    errors.append(error)
        errors.extend(validate_registry_with_pydantic(self))
        if self.unknown_keys:
            warnings.append("unknown_config_keys=" + ",".join(self.unknown_keys))
        if self.deprecated_keys:
            warnings.append("deprecated_config_keys=" + ",".join(self.deprecated_keys))
        return ConfigValidationReport(
            errors=tuple(errors),
            warnings=tuple(warnings),
            unknown_keys=self.unknown_keys,
            deprecated_keys=self.deprecated_keys,
        )

    def feature_map(self) -> dict[str, tuple[str, ...]]:
        return {
            "core": tuple(key for key, value in self.core.as_dict().items() if key.startswith("enable_") and bool(value)),
            "llm": tuple(key for key, value in self.llm.as_dict().items() if key.endswith("_enabled") and bool(value)),
            "experimental": tuple(self.experimental.enabled_flags),
            "observability": tuple(
                key
                for key, value in self.observability.as_dict().items()
                if key.endswith("_enabled") and bool(value)
            ),
        }

    def snapshot(self, *, redacted: bool = True) -> dict[str, Any]:
        data = {
            "schema": "elara.config.registry.v1",
            "source_layers": tuple(self.source_layers),
            "domains": {name: domain.as_dict() for name, domain in self.domains().items()},
            "feature_map": self.feature_map(),
            "validation": asdict(self.validate()),
            "deprecated": {
                key: asdict(meta)
                for key, meta in sorted(self.metadata.items())
                if meta.deprecated and key in set(self.deprecated_keys)
            },
        }
        return redact_config(data) if redacted else data

    def config_hash(self) -> str:
        raw = json.dumps(self.snapshot(redacted=True), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_config_registry(
    values: dict[str, Any],
    *,
    unknown_keys: tuple[str, ...] = (),
    deprecated_keys: tuple[str, ...] = (),
    deprecated_metadata: dict[str, str] | None = None,
    source_layers: tuple[str, ...] = (),
) -> ConfigRegistry:
    metadata = build_metadata(deprecated_metadata or {})
    file_fallback_enabled = bool(
        values.get("TRADING_FILE_IPC_FALLBACK_ENABLED")
        or values.get("TRADING_STATUS_FILE_FALLBACK_ENABLED")
        or values.get("CORE_HEARTBEAT_FILE_FALLBACK_ENABLED")
    )
    experimental_enabled = tuple(
        sorted(key for key in EXPERIMENTAL_KEYS if bool(values.get(key, False)))
    )
    secrets = _secrets_domain(values)
    return ConfigRegistry(
        core=CoreDomainConfig(
            app_identity=str(values.get("APP_IDENTITY", "") or ""),
            product_focus=str(values.get("APP_PRODUCT_FOCUS", "") or ""),
            assistant_role=str(values.get("APP_ASSISTANT_ROLE", "") or ""),
            enable_trading_skill=bool(values.get("ENABLE_TRADING_SKILL", False)),
            enable_knowledge_skill=bool(values.get("ENABLE_KNOWLEDGE_SKILL", False)),
            required_components=_csv_items(values.get("REQUIRED_COMPONENTS")),
        ),
        runtime=RuntimeDomainConfig(
            environment=str(values.get("ENV") or values.get("APP_ENV") or "development"),
            config_profile=str(values.get("CONFIG_PROFILE") or "development"),
            state_backend=str(values.get("STATE_BACKEND") or "auto"),
            redis_event_bus_required=bool(values.get("REDIS_EVENT_BUS_REQUIRED", False)),
            redis_stream_event_bus_enabled=bool(values.get("REDIS_STREAM_EVENT_BUS_ENABLED", False)),
            redis_stream_event_bus_required=bool(values.get("REDIS_STREAM_EVENT_BUS_REQUIRED", False)),
            redis_stream_event_key=str(values.get("REDIS_STREAM_EVENT_KEY", "") or ""),
            redis_stream_event_maxlen=int(values.get("REDIS_STREAM_EVENT_MAXLEN", 100_000) or 100_000),
            state_backend_fail_fast=bool(values.get("STATE_BACKEND_FAIL_FAST", False)),
            file_fallback_enabled=file_fallback_enabled,
            runtime_event_store_enabled=bool(values.get("RUNTIME_EVENT_STORE_ENABLED", True)),
            runtime_event_log_path=str(values.get("RUNTIME_EVENT_LOG_PATH", "") or ""),
            fail_fast_startup=bool(values.get("FAIL_FAST_STARTUP", False)),
        ),
        trading=TradingDomainConfig(
            mode=str(values.get("TRADING_MODE") or "paper"),
            symbol=str(values.get("TRADING_SYMBOL") or ""),
            strategy=str(values.get("TRADING_STRATEGY") or ""),
            strategy_version=str(values.get("TRADING_STRATEGY_VERSION") or ""),
            daily_max_loss_pct=float(values.get("DAILY_MAX_LOSS_PCT", 0.0) or 0.0),
            max_drawdown_pct=float(values.get("MAX_DRAWDOWN_PCT", 0.0) or 0.0),
            execution_dedup_ttl_sec=int(values.get("TRADING_EXECUTION_DEDUP_TTL_SEC", 600) or 600),
            ipc_enabled=bool(values.get("TRADING_IPC_ENABLED", False)),
            ipc_host=str(values.get("TRADING_IPC_HOST", "") or ""),
            ipc_port=int(values.get("TRADING_IPC_PORT", 8768) or 8768),
        ),
        governance=GovernanceDomainConfig(
            life_min_sleep_hours=float(values.get("LIFE_MIN_SLEEP_HOURS", 0.0) or 0.0),
            life_max_stress=float(values.get("LIFE_MAX_STRESS", 0.0) or 0.0),
            critical_confirm_enabled=bool(values.get("CRITICAL_CONFIRM_ENABLED", True)),
            critical_confirm_ttl_sec=int(values.get("CRITICAL_CONFIRM_TTL_SEC", 120) or 120),
            prompt_injection_guard_enabled=bool(values.get("PROMPT_INJECTION_GUARD_ENABLED", True)),
            runtime_complexity_max_experimental=int(values.get("RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL", 1) or 1),
        ),
        observability=ObservabilityDomainConfig(
            log_json=bool(values.get("LOG_JSON", False)),
            observability_enabled=bool(values.get("OBSERVABILITY_ENABLED", True)),
            prometheus_metrics_enabled=bool(values.get("PROMETHEUS_METRICS_ENABLED", True)),
            prometheus_metrics_public=bool(values.get("PROMETHEUS_METRICS_PUBLIC", False)),
            otel_enabled=bool(values.get("OTEL_ENABLED", False)),
            otel_service_name=str(values.get("OTEL_SERVICE_NAME", "") or ""),
            slow_request_ms=float(values.get("OBSERVABILITY_SLOW_REQUEST_MS", 1000.0) or 1000.0),
        ),
        llm=LLMDomainConfig(
            primary_provider=str(values.get("LLM_PRIMARY_PROVIDER", "") or ""),
            tool_calling_enabled=bool(values.get("LLM_TOOL_CALLING_ENABLED", False)),
            agent_orchestrator_enabled=bool(values.get("LLM_AGENT_ORCHESTRATOR_ENABLED", False)),
            markdown_skills_enabled=bool(values.get("LLM_MARKDOWN_SKILLS_ENABLED", False)),
            openai_configured=_configured_secret(values.get("OPENAI_API_KEY")),
            groq_configured=_configured_secret(values.get("GROQ_API_KEY")),
            gemini_configured=_configured_secret(values.get("GEMINI_API_KEY")),
        ),
        blockchain=BlockchainDomainConfig(
            enabled=bool(values.get("BLOCKCHAIN_ENABLED", False)),
            provider_url_configured=bool(str(values.get("BLOCKCHAIN_PROVIDER_URL", "") or "").strip()),
            wallet_configured=bool(str(values.get("BLOCKCHAIN_WALLET_ADDRESS", "") or "").strip()),
        ),
        experimental=ExperimentalDomainConfig(
            enabled_flags=experimental_enabled,
            max_enabled=int(values.get("RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL", 1) or 1),
            isolated=True,
        ),
        secrets=secrets,
        metadata=metadata,
        unknown_keys=tuple(unknown_keys),
        deprecated_keys=tuple(deprecated_keys),
        source_layers=tuple(source_layers),
    )


def build_metadata(deprecated_metadata: dict[str, str]) -> dict[str, ConfigKeyMetadata]:
    metadata: dict[str, ConfigKeyMetadata] = {
        "TG_ALLOWED_USER_ID": ConfigKeyMetadata(
            key="TG_ALLOWED_USER_ID",
            domain="core",
            deprecated_since="2026.05",
            replacement_key="TG_ALLOWED_USER",
            removal_version="v2",
            description=deprecated_metadata.get("TG_ALLOWED_USER_ID", "Use TG_ALLOWED_USER instead."),
        )
    }
    for key, description in deprecated_metadata.items():
        metadata.setdefault(
            key,
            ConfigKeyMetadata(
                key=key,
                domain=guess_domain(key),
                deprecated_since="unknown",
                description=description,
            ),
        )
    return metadata


def guess_domain(key: str) -> str:
    upper = str(key or "").upper()
    if upper.startswith(("TRADING_", "EXCHANGE_", "ORDER_", "WS_")):
        return "trading"
    if upper.startswith(("REDIS_", "DB_", "STATE_", "DATABASE_", "POSTGRES_", "CORE_HEARTBEAT_", "RUNTIME_")):
        return "runtime"
    if upper.startswith(("OBSERVABILITY_", "PROMETHEUS_", "OTEL_", "GRAFANA_", "LOKI_", "TEMPO_", "LOG_")):
        return "observability"
    if upper.startswith(("LLM_", "OPENAI_", "GROQ_", "GEMINI_")):
        return "llm"
    if upper.startswith(("LIFE_", "CRITICAL_", "PROMPT_", "GOVERNANCE_")):
        return "governance"
    if upper.startswith(("BLOCKCHAIN_", "WEB3_")):
        return "blockchain"
    if "SECRET" in upper or "TOKEN" in upper or "PASSWORD" in upper:
        return "secrets"
    return "core"


def redact_config(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        return {str(key): redact_config(item, str(key)) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if _secret_name(key_hint):
        return "__configured__" if _configured_secret(value) else ""
    if isinstance(value, (list, tuple, set)):
        return [redact_config(item, key_hint) for item in value]
    return value


def _secrets_domain(values: dict[str, Any]) -> SecretsDomainConfig:
    configured: list[str] = []
    placeholders: list[str] = []
    for key, value in sorted(values.items()):
        if not _secret_name(str(key)):
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if _looks_like_placeholder(text):
            placeholders.append(str(key))
        else:
            configured.append(str(key))
    return SecretsDomainConfig(
        configured_keys=tuple(configured),
        placeholder_keys=tuple(placeholders),
    )


def _csv_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = str(value or "").split(",")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _secret_name(key: str) -> bool:
    upper = str(key or "").upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def _configured_secret(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not _looks_like_placeholder(text)


def _looks_like_placeholder(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return any(marker in lowered for marker in ("replace-with-", "changeme", "your_", "dummy", "example", "<", ">"))


def registry_contract() -> dict[str, tuple[str, ...]]:
    domain_classes = (
        CoreDomainConfig,
        RuntimeDomainConfig,
        TradingDomainConfig,
        GovernanceDomainConfig,
        ObservabilityDomainConfig,
        LLMDomainConfig,
        BlockchainDomainConfig,
        ExperimentalDomainConfig,
        SecretsDomainConfig,
    )
    return {
        cls().domain: tuple(field.name for field in fields(cls) if field.name != "domain")
        for cls in domain_classes
    }
