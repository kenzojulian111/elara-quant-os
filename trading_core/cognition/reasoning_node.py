from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ReasoningNodeCapability:
    name: str
    version: str = "v1"
    required: bool = False

    def token(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass(frozen=True)
class ReasoningNodeHeartbeat:
    node_id: str
    healthy: bool
    latency_ms: float = 0.0
    queue_depth: int = 0
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "node_id": self.node_id,
            "healthy": bool(self.healthy),
            "latency_ms": float(self.latency_ms),
            "queue_depth": int(self.queue_depth),
            "capabilities": tuple(self.capabilities),
        }


@dataclass(frozen=True)
class ReasoningNode:
    node_id: str
    role: str
    capabilities: tuple[ReasoningNodeCapability, ...] = field(default_factory=tuple)

    def supports(self, required: tuple[str, ...]) -> bool:
        supported = {capability.name for capability in self.capabilities}
        return all(item in supported for item in required)

    def heartbeat(self, *, latency_ms: float = 0.0, queue_depth: int = 0, healthy: bool = True) -> ReasoningNodeHeartbeat:
        return ReasoningNodeHeartbeat(
            node_id=self.node_id,
            healthy=bool(healthy),
            latency_ms=max(0.0, float(latency_ms)),
            queue_depth=max(0, int(queue_depth)),
            capabilities=tuple(capability.token() for capability in self.capabilities),
        )
