from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RustRuntimeHandshake:
    protocol_version: int
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    compatible: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


class RustRuntimeGateway:
    PROTOCOL_VERSION = 1
    REQUIRED_CAPABILITIES = ("event_stream_v1", "replay_executor_v1")

    def negotiate(self, *, protocol_version: int, capabilities: tuple[str, ...]) -> RustRuntimeHandshake:
        provided = tuple(sorted({str(capability) for capability in capabilities if str(capability)}))
        missing = tuple(capability for capability in self.REQUIRED_CAPABILITIES if capability not in provided)
        reasons: list[str] = []
        if int(protocol_version) != self.PROTOCOL_VERSION:
            reasons.append("protocol_version_mismatch")
        if missing:
            reasons.append(f"missing_capabilities={','.join(missing)}")
        return RustRuntimeHandshake(
            protocol_version=int(protocol_version),
            capabilities=provided,
            compatible=not reasons,
            reasons=tuple(reasons),
        )
