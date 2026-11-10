from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class RuntimeCapabilities:
    service: str
    version: str = "1"
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def supports(self, capability: str) -> bool:
        return str(capability or "").strip() in set(self.capabilities)

    def to_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "version": self.version,
            "capabilities": tuple(self.capabilities),
        }


@dataclass(frozen=True)
class CapabilityNegotiation:
    local: RuntimeCapabilities
    remote: RuntimeCapabilities
    required: tuple[str, ...] = field(default_factory=tuple)
    optional: tuple[str, ...] = field(default_factory=tuple)

    @property
    def common(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.local.capabilities).intersection(self.remote.capabilities)))

    @property
    def missing_required(self) -> tuple[str, ...]:
        remote = set(self.remote.capabilities)
        return tuple(item for item in self.required if item not in remote)

    @property
    def ready(self) -> bool:
        return not self.missing_required

    def to_dict(self) -> dict[str, object]:
        return {
            "local": self.local.to_dict(),
            "remote": self.remote.to_dict(),
            "required": tuple(self.required),
            "optional": tuple(self.optional),
            "common": self.common,
            "missing_required": self.missing_required,
            "ready": self.ready,
        }


def default_core_capabilities() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        service="elara-core",
        capabilities=(
            "command_enqueue_v1",
            "health_ready_v1",
            "metrics_prometheus_v1",
            "runtime_state_v1",
            "decision_envelope_v1",
        ),
    )


def default_trader_capabilities() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        service="elara-trader",
        capabilities=(
            "execution_ack_v1",
            "execution_idempotency_v1",
            "health_status_v1",
            "metrics_prometheus_v1",
            "partial_fill_tracking_v1",
        ),
    )


def negotiate(
    local: RuntimeCapabilities,
    remote: RuntimeCapabilities,
    *,
    required: Iterable[str] = (),
    optional: Iterable[str] = (),
) -> CapabilityNegotiation:
    return CapabilityNegotiation(
        local=local,
        remote=remote,
        required=tuple(str(item) for item in required if str(item or "").strip()),
        optional=tuple(str(item) for item in optional if str(item or "").strip()),
    )
