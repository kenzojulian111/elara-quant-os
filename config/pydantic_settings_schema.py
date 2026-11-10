from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import ValidationError, model_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover - optional until dependencies are installed
    BaseSettings = None  # type: ignore[assignment]
    SettingsConfigDict = None  # type: ignore[assignment]
    ValidationError = None  # type: ignore[assignment]
    model_validator = None  # type: ignore[assignment]


def pydantic_settings_available() -> bool:
    return BaseSettings is not None and SettingsConfigDict is not None


if pydantic_settings_available():

    class RuntimeSettings(BaseSettings):
        model_config = SettingsConfigDict(extra="forbid")

        environment: str = "development"
        config_profile: str = "development"
        state_backend: Literal["auto", "file", "memory", "redis"] = "auto"
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

        @model_validator(mode="after")
        def validate_transport(self) -> "RuntimeSettings":
            if self.redis_event_bus_required and self.file_fallback_enabled:
                raise ValueError("redis_required_with_file_fallback")
            if self.redis_stream_event_bus_required and not self.redis_stream_event_bus_enabled:
                raise ValueError("stream_bus_required_but_disabled")
            if self.redis_stream_event_bus_required and not self.redis_event_bus_required:
                raise ValueError("stream_bus_requires_redis_event_bus")
            if self.redis_stream_event_bus_enabled and self.redis_stream_event_maxlen < 100:
                raise ValueError("stream_bus_maxlen_too_low")
            if self.state_backend == "redis" and self.state_backend_fail_fast and self.file_fallback_enabled:
                raise ValueError("redis_fail_fast_with_file_fallback")
            return self

    class TradingSettings(BaseSettings):
        model_config = SettingsConfigDict(extra="forbid")

        mode: Literal["paper", "live", "backtest", "simulation"] = "paper"
        symbol: str = ""
        strategy: str = ""
        strategy_version: str = ""
        daily_max_loss_pct: float = 0.0
        max_drawdown_pct: float = 0.0
        execution_dedup_ttl_sec: int = 600
        ipc_enabled: bool = False
        ipc_host: str = ""
        ipc_port: int = 8768

        @model_validator(mode="after")
        def validate_execution_window(self) -> "TradingSettings":
            if self.daily_max_loss_pct < 0:
                raise ValueError("daily_max_loss_pct_negative")
            if self.max_drawdown_pct < 0:
                raise ValueError("max_drawdown_pct_negative")
            if self.execution_dedup_ttl_sec < 30:
                raise ValueError("execution_dedup_ttl_too_low")
            return self

    class ExperimentalSettings(BaseSettings):
        model_config = SettingsConfigDict(extra="forbid")

        enabled_flags: tuple[str, ...] = ()
        max_enabled: int = 1
        isolated: bool = True

        @model_validator(mode="after")
        def validate_experimental_budget(self) -> "ExperimentalSettings":
            if len(self.enabled_flags) > max(0, int(self.max_enabled)):
                raise ValueError("too_many_experimental_flags")
            return self

else:
    RuntimeSettings = None
    TradingSettings = None
    ExperimentalSettings = None


def validate_registry_with_pydantic(registry: Any) -> tuple[str, ...]:
    if not pydantic_settings_available():
        return ()

    domain_models = {
        "runtime": RuntimeSettings,
        "trading": TradingSettings,
        "experimental": ExperimentalSettings,
    }
    errors: list[str] = []
    for domain_name, model in domain_models.items():
        domain = getattr(registry, domain_name)
        payload = domain.as_dict()
        payload.pop("domain", None)
        try:
            model(**payload)
        except ValidationError as exc:
            errors.append(f"{domain_name}.pydantic_settings=" + ";".join(error["msg"] for error in exc.errors()))
    return tuple(errors)
