from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

try:
    from hypothesis import HealthCheck, settings
except Exception:  # pragma: no cover - optional dependency
    HealthCheck = None
    settings = None

if settings is not None and HealthCheck is not None:
    try:
        settings.register_profile(
            "elara_relaxed",
            suppress_health_check=[HealthCheck.too_slow],
        )
    except Exception:
        pass
    try:
        settings.load_profile("elara_relaxed")
    except Exception:
        pass


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class TradeOutcome:
    pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal


class RiskManager:
    def __init__(self, total_balance: Decimal, risk_per_trade_pct: Decimal, fee_pct: Decimal):
        self.total_balance = Decimal(total_balance)
        self.risk_per_trade_pct = Decimal(risk_per_trade_pct)
        self.fee_pct = Decimal(fee_pct)

    def calculate_trade_outcome(
        self,
        entry: Decimal,
        exit_p: Decimal,
        size: Decimal,
        direction: TradeDirection,
    ) -> TradeOutcome:
        entry = Decimal(entry)
        exit_p = Decimal(exit_p)
        size = Decimal(size)
        if direction == TradeDirection.SHORT:
            pnl = (entry - exit_p) * size
        else:
            pnl = (exit_p - entry) * size
        fee_rate = self.fee_pct / Decimal("100")
        total_fees = (abs(entry) * size * fee_rate) + (abs(exit_p) * size * fee_rate)
        net_pnl = pnl - total_fees
        return TradeOutcome(pnl=pnl, total_fees=total_fees, net_pnl=net_pnl)

    def calculate_position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        entry = Decimal(entry)
        stop = Decimal(stop)
        per_unit_risk = abs(entry - stop)
        if per_unit_risk <= Decimal("0"):
            return Decimal("0")
        risk_budget = self.total_balance * (self.risk_per_trade_pct / Decimal("100"))
        return max(Decimal("0"), risk_budget / per_unit_risk)


class MarketRegimeAnalyzer:
    def __init__(self, logger=None, cfg=None):
        self.log = logger
        self.cfg = cfg
        try:
            self.adx_trend_min = float(getattr(cfg, "ADX_TREND_MIN", 20.0) or 20.0)
        except Exception:
            self.adx_trend_min = 20.0

    def get_regime(self, ltf_slice):
        try:
            if ltf_slice is None or getattr(ltf_slice, "empty", True):
                return "unknown"
            row = ltf_slice.iloc[-1]
            adx_val = row.get("adx")
            if adx_val is None:
                return "unknown"
            adx = float(adx_val)
            if adx >= self.adx_trend_min:
                return "trending"
            return "range"
        except Exception:
            return "unknown"

