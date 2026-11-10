from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxAction:
    action: str
    protocol: str
    amount_usd: float = 0.0


@dataclass(frozen=True)
class SandboxDecision:
    accepted: bool
    mode: str
    reason: str


class OnChainAgentSandbox:
    def __init__(self, *, allow_simulation: bool = True, allow_live: bool = False):
        self.allow_simulation = bool(allow_simulation)
        self.allow_live = bool(allow_live)

    def authorize(self, action: SandboxAction, *, mode: str = "simulation") -> SandboxDecision:
        normalized = str(mode or "simulation").lower()
        if normalized != "simulation":
            return SandboxDecision(False, normalized, "live_onchain_agent_disabled")
        if not self.allow_simulation:
            return SandboxDecision(False, normalized, "simulation_disabled")
        return SandboxDecision(True, normalized, f"sandbox_only:{action.action}:{action.protocol}")
