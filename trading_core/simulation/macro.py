from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import MarketState


@dataclass(frozen=True)
class MacroShock:
    name: str
    rate_delta_bps: float = 0.0
    inflation_delta_pct: float = 0.0
    liquidity_delta_pct: float = 0.0
    risk_sentiment_delta: float = 0.0


@dataclass(frozen=True)
class MacroProjection:
    shock: MacroShock
    expected_price_impact_pct: float
    expected_volatility: str
    expected_liquidity: str
    reasons: tuple[str, ...]


class MacroShockSimulator:
    def project(self, shock: MacroShock, state: MarketState | None = None) -> MacroProjection:
        impact = 0.0
        reasons: list[str] = []
        if shock.rate_delta_bps:
            impact -= float(shock.rate_delta_bps) / 100.0 * 0.8
            reasons.append(f"rate_delta_bps={shock.rate_delta_bps:.1f}")
        if shock.inflation_delta_pct:
            impact -= float(shock.inflation_delta_pct) * 0.6
            reasons.append(f"inflation_delta_pct={shock.inflation_delta_pct:.2f}")
        if shock.liquidity_delta_pct:
            impact += float(shock.liquidity_delta_pct) * 0.5
            reasons.append(f"liquidity_delta_pct={shock.liquidity_delta_pct:.2f}")
        if shock.risk_sentiment_delta:
            impact += float(shock.risk_sentiment_delta) * 2.0
            reasons.append(f"risk_sentiment_delta={shock.risk_sentiment_delta:.2f}")
        volatility = "high" if abs(impact) >= 3.0 else "normal"
        liquidity = "thin" if shock.liquidity_delta_pct < -10 else str(getattr(state, "liquidity", "") or "normal")
        return MacroProjection(
            shock=shock,
            expected_price_impact_pct=impact,
            expected_volatility=volatility,
            expected_liquidity=liquidity,
            reasons=tuple(reasons or ("macro_neutral",)),
        )
