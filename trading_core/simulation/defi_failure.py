from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeFiFailureScenario:
    kind: str
    severity: float
    protocol: str = "unknown"


@dataclass(frozen=True)
class DeFiFailureImpact:
    protocol: str
    liquidity_multiplier: float
    trust_multiplier: float
    validator_availability: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


class DeFiFailureSimulator:
    _IMPACTS = {
        "bridge_exploit": (0.55, 0.45, 0.95),
        "stablecoin_depeg": (0.45, 0.35, 0.98),
        "oracle_manipulation": (0.65, 0.50, 0.98),
        "liquidity_collapse": (0.25, 0.55, 0.99),
        "validator_outage": (0.75, 0.65, 0.35),
    }

    def run(self, scenario: DeFiFailureScenario) -> DeFiFailureImpact:
        severity = _clamp(scenario.severity)
        liquidity, trust, validators = self._IMPACTS.get(scenario.kind, (0.80, 0.80, 0.90))
        return DeFiFailureImpact(
            protocol=scenario.protocol,
            liquidity_multiplier=_weighted_multiplier(liquidity, severity),
            trust_multiplier=_weighted_multiplier(trust, severity),
            validator_availability=_weighted_multiplier(validators, severity),
            reasons=(f"scenario={scenario.kind}", f"severity={severity:.4f}"),
        )


def _weighted_multiplier(impact: float, severity: float) -> float:
    return max(0.0, min(1.0, 1.0 - ((1.0 - float(impact)) * severity)))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
