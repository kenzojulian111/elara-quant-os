from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import RiskDecision, TradeCandidate


@dataclass
class RiskLimits:
    max_risk_per_trade_pct: float = 0.02
    max_drawdown_pct: float = 15.0
    max_leverage: float = 3.0
    uncertainty_size_floor: float = 0.25
    thin_liquidity_multiplier: float = 0.5
    high_volatility_multiplier: float = 0.65


@dataclass(frozen=True)
class RiskContext:
    uncertainty: float = 0.0
    liquidity: str = ""
    volatility: str = ""


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def evaluate(self, candidate: TradeCandidate, current_drawdown_pct: float) -> RiskDecision:
        if current_drawdown_pct >= self.limits.max_drawdown_pct:
            return RiskDecision(
                approved=False,
                reason="drawdown_limit_reached",
                size_pct=0.0,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                reasons=(
                    "drawdown_limit_reached",
                    f"current_drawdown_pct={float(current_drawdown_pct):.4f}",
                    f"max_drawdown_pct={float(self.limits.max_drawdown_pct):.4f}",
                ),
            )

        requested_size_pct = float(candidate.size_pct)
        size_pct = min(requested_size_pct, float(self.limits.max_risk_per_trade_pct))
        if size_pct <= 0:
            return RiskDecision(
                approved=False,
                reason="invalid_position_size",
                size_pct=0.0,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                reasons=("invalid_position_size", f"requested_size_pct={requested_size_pct:.6f}"),
            )

        approved_reason = (
            "size_capped_to_limit"
            if size_pct < requested_size_pct
            else "risk_within_limits"
        )
        return RiskDecision(
            approved=True,
            reason=approved_reason,
            size_pct=size_pct,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            reasons=(
                approved_reason,
                f"requested_size_pct={requested_size_pct:.6f}",
                f"approved_size_pct={size_pct:.6f}",
            ),
            risk_budget_pct=size_pct,
        )

    def evaluate_contextual(
        self,
        candidate: TradeCandidate,
        current_drawdown_pct: float,
        context: RiskContext | None = None,
    ) -> RiskDecision:
        base = self.evaluate(candidate, current_drawdown_pct)
        if not base.approved:
            return base
        context = context or RiskContext()
        multiplier = 1.0
        reasons = list(base.reasons)
        uncertainty = max(0.0, min(1.0, float(context.uncertainty or 0.0)))
        if uncertainty > 0:
            uncertainty_multiplier = max(
                float(self.limits.uncertainty_size_floor),
                1.0 - uncertainty,
            )
            multiplier *= uncertainty_multiplier
            reasons.append(f"uncertainty_size_multiplier={uncertainty_multiplier:.4f}")
        liquidity = str(context.liquidity or "").strip().lower()
        if liquidity in {"thin", "illiquid", "dry"}:
            multiplier *= float(self.limits.thin_liquidity_multiplier)
            reasons.append(f"liquidity_size_multiplier={float(self.limits.thin_liquidity_multiplier):.4f}")
        volatility = str(context.volatility or "").strip().lower()
        if volatility in {"high", "extreme", "chaotic"}:
            multiplier *= float(self.limits.high_volatility_multiplier)
            reasons.append(f"volatility_size_multiplier={float(self.limits.high_volatility_multiplier):.4f}")
        adjusted_size = max(0.0, float(base.size_pct) * multiplier)
        if adjusted_size <= 0:
            return RiskDecision(
                approved=False,
                reason="contextual_size_zero",
                size_pct=0.0,
                stop_loss=base.stop_loss,
                take_profit=base.take_profit,
                reasons=tuple((*reasons, "contextual_size_zero")),
                risk_budget_pct=0.0,
                trace_id=base.trace_id,
            )
        return RiskDecision(
            approved=True,
            reason="contextual_size_adjusted" if adjusted_size < float(base.size_pct) else base.reason,
            size_pct=adjusted_size,
            stop_loss=base.stop_loss,
            take_profit=base.take_profit,
            reasons=tuple((*reasons, f"contextual_approved_size_pct={adjusted_size:.6f}")),
            risk_budget_pct=adjusted_size,
            trace_id=base.trace_id,
        )

