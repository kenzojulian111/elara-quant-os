from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GovernanceV2Context:
    runtime_mode: str
    uncertainty: float
    portfolio_drawdown_probability: float
    blockchain_risk: float = 0.0
    signed_artifact: bool = False


@dataclass(frozen=True)
class GovernanceV2Decision:
    allowed: bool
    mode: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


class GovernanceEngineV2:
    def evaluate(self, context: GovernanceV2Context) -> GovernanceV2Decision:
        reasons: list[str] = []
        if context.runtime_mode.lower() in {"red", "safe", "halted"}:
            reasons.append("runtime_mode_blocks_execution")
        if float(context.uncertainty) >= 0.75:
            reasons.append("uncertainty_above_limit")
        if float(context.portfolio_drawdown_probability) >= 0.80:
            reasons.append("portfolio_drawdown_risk_above_limit")
        if float(context.blockchain_risk) >= 0.80:
            reasons.append("blockchain_risk_above_limit")
        if not context.signed_artifact:
            reasons.append("signed_artifact_required")
        return GovernanceV2Decision(
            allowed=not reasons,
            mode="allow" if not reasons else "block",
            reasons=tuple(reasons),
        )
