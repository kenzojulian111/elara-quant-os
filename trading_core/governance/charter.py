from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyPrinciple:
    principle_id: str
    statement: str
    severity: str = "critical"


@dataclass(frozen=True)
class GovernanceCharter:
    version: str
    principles: tuple[SafetyPrinciple, ...]

    def requires_block(self, reason: str) -> bool:
        text = str(reason or "").lower()
        blocking_markers = ("duplicate", "unsafe", "stale", "drawdown", "operator_block", "runtime_safe")
        return any(marker in text for marker in blocking_markers)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "principles": tuple(principle.__dict__ for principle in self.principles),
        }


def default_governance_charter() -> GovernanceCharter:
    return GovernanceCharter(
        version="elara.governance.charter.v1",
        principles=(
            SafetyPrinciple("capital_preservation", "Preserve capital before pursuing return."),
            SafetyPrinciple("explainability", "Every trade must be explainable after the fact.", "high"),
            SafetyPrinciple("idempotency", "Execution must be protected against duplicate commands."),
            SafetyPrinciple("degraded_safety", "Degraded runtime may preserve exits but should block new risk."),
            SafetyPrinciple("operator_override", "Operator and governance controls outrank strategy output."),
        ),
    )
