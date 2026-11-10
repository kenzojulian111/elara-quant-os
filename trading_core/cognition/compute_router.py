from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from trading_core.cognition.reasoning_node import ReasoningNodeHeartbeat


@dataclass(frozen=True)
class ComputeRouteRequest:
    task: str
    uncertainty: float = 0.0
    runtime_mode: str = "full"
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    max_latency_ms: float = 250.0


@dataclass(frozen=True)
class ComputeRoute:
    target: str
    mode: str
    accepted: bool
    reason: str
    fallback: str = ""

    def to_dict(self) -> Mapping[str, object]:
        return {
            "target": self.target,
            "mode": self.mode,
            "accepted": bool(self.accepted),
            "reason": self.reason,
            "fallback": self.fallback,
        }


class AdaptiveComputeRouter:
    def route(
        self,
        request: ComputeRouteRequest,
        heartbeats: Iterable[ReasoningNodeHeartbeat],
    ) -> ComputeRoute:
        mode = str(request.runtime_mode or "full").lower()
        if mode == "safe":
            return ComputeRoute("local", "minimal", True, "runtime_safe_local_minimal", fallback="skip_remote")
        healthy = tuple(
            heartbeat
            for heartbeat in heartbeats
            if heartbeat.healthy
            and heartbeat.latency_ms <= float(request.max_latency_ms)
            and _supports(heartbeat, request.required_capabilities)
        )
        if not healthy:
            fallback_mode = "minimal" if request.uncertainty < 0.7 else "defer"
            return ComputeRoute("local", fallback_mode, fallback_mode != "defer", "no_healthy_remote_node")
        ranked = sorted(healthy, key=lambda item: (item.queue_depth, item.latency_ms))
        target = ranked[0]
        route_mode = "deep" if float(request.uncertainty) >= 0.45 and mode == "full" else "standard"
        if mode == "degraded":
            route_mode = "standard"
        return ComputeRoute(target.node_id, route_mode, True, "remote_node_selected")


def _supports(heartbeat: ReasoningNodeHeartbeat, required: tuple[str, ...]) -> bool:
    if not required:
        return True
    names = {token.split(":", 1)[0] for token in heartbeat.capabilities}
    return all(item in names for item in required)
