from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BlackSwanScenario:
    kind: str
    severity: float


@dataclass(frozen=True)
class BlackSwanImpact:
    liquidity_multiplier: float
    volatility_multiplier: float
    price_impact_ratio: float
    execution_allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


class BlackSwanSimulator:
    _SCENARIOS = {
        "flash_crash": (0.45, 2.8, 0.18),
        "liquidity_collapse": (0.20, 2.2, 0.24),
        "exchange_outage": (0.05, 1.8, 0.12),
        "liquidation_cascade": (0.30, 3.1, 0.28),
    }

    def run(self, scenario: BlackSwanScenario) -> BlackSwanImpact:
        severity = _clamp(scenario.severity)
        liquidity, volatility, price_impact = self._SCENARIOS.get(scenario.kind, (0.70, 1.5, 0.08))
        weighted_liquidity = 1.0 - ((1.0 - liquidity) * severity)
        weighted_volatility = 1.0 + ((volatility - 1.0) * severity)
        weighted_impact = price_impact * severity
        allowed = weighted_liquidity >= 0.50 and weighted_impact < 0.12
        return BlackSwanImpact(
            liquidity_multiplier=weighted_liquidity,
            volatility_multiplier=weighted_volatility,
            price_impact_ratio=weighted_impact,
            execution_allowed=allowed,
            reasons=(f"scenario={scenario.kind}", f"severity={severity:.4f}"),
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
