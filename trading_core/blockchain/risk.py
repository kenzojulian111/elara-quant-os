from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SmartContractRiskInput:
    protocol: str
    liquidity_usd: float
    governance_concentration: float = 0.0
    oracle_dependency: float = 0.0
    bridge_dependency: float = 0.0
    audit_age_days: int = 0
    exploit_history: int = 0


@dataclass(frozen=True)
class BlockchainRiskAssessment:
    protocol: str
    risk_score: float
    exploit_probability: float
    classification: str
    reasons: tuple[str, ...]


class SmartContractRiskEngine:
    def assess(self, item: SmartContractRiskInput) -> BlockchainRiskAssessment:
        liquidity_penalty = 0.25 if float(item.liquidity_usd) < 1_000_000 else 0.0
        score = (
            liquidity_penalty
            + _clamp(item.governance_concentration) * 0.20
            + _clamp(item.oracle_dependency) * 0.20
            + _clamp(item.bridge_dependency) * 0.20
            + min(0.15, max(0, int(item.audit_age_days)) / 3650.0)
            + min(0.30, max(0, int(item.exploit_history)) * 0.15)
        )
        score = _clamp(score)
        classification = "critical" if score >= 0.75 else "high" if score >= 0.50 else "medium" if score >= 0.25 else "low"
        reasons = (
            f"liquidity_penalty={liquidity_penalty:.4f}",
            f"governance_concentration={_clamp(item.governance_concentration):.4f}",
            f"oracle_dependency={_clamp(item.oracle_dependency):.4f}",
            f"bridge_dependency={_clamp(item.bridge_dependency):.4f}",
        )
        return BlockchainRiskAssessment(item.protocol, score, min(0.95, score * 0.65), classification, reasons)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
