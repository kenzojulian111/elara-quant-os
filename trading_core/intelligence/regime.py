from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable

from trading_core.data_contracts import MarketState


@dataclass(frozen=True)
class RegimeAssessment:
    trend: str
    volatility: str
    liquidity: str
    confidence: float
    reasons: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.trend}:{self.volatility}:{self.liquidity}"


@dataclass(frozen=True)
class RegimeDetectorConfig:
    min_points: int = 5
    trend_threshold_pct: float = 1.5
    range_threshold_pct: float = 0.6
    high_volatility_pct: float = 2.5
    low_volatility_pct: float = 0.6
    thin_liquidity_volume: float = 100.0


class RegimeDetector:
    def __init__(self, config: RegimeDetectorConfig | None = None):
        self.config = config or RegimeDetectorConfig()

    def assess(self, states: Iterable[MarketState]) -> RegimeAssessment:
        rows = list(states or ())
        if len(rows) < max(2, int(self.config.min_points)):
            return RegimeAssessment(
                trend="unknown",
                volatility="unknown",
                liquidity="unknown",
                confidence=0.0,
                reasons=("regime_insufficient_history",),
            )

        closes = [float(row.close) for row in rows if float(row.close or 0.0) > 0]
        volumes = [float(row.volume or 0.0) for row in rows]
        if len(closes) < 2:
            return RegimeAssessment("unknown", "unknown", "unknown", 0.0, ("regime_invalid_close_history",))

        move_pct = ((closes[-1] - closes[0]) / closes[0]) * 100.0
        returns = []
        for prev, cur in zip(closes, closes[1:]):
            if prev:
                returns.append(((cur - prev) / prev) * 100.0)
        realized_vol = pstdev(returns) if len(returns) > 1 else abs(returns[0]) if returns else 0.0

        if abs(move_pct) >= float(self.config.trend_threshold_pct):
            trend = "trend_up" if move_pct > 0 else "trend_down"
        elif abs(move_pct) <= float(self.config.range_threshold_pct):
            trend = "range"
        else:
            trend = "transition"

        if realized_vol >= float(self.config.high_volatility_pct):
            volatility = "high"
        elif realized_vol <= float(self.config.low_volatility_pct):
            volatility = "low"
        else:
            volatility = "normal"

        avg_volume = mean(volumes) if volumes else 0.0
        liquidity = "thin" if avg_volume <= float(self.config.thin_liquidity_volume) else "deep"

        confidence = self._confidence(move_pct, realized_vol, avg_volume)
        reasons = (
            f"move_pct={move_pct:.4f}",
            f"realized_volatility_pct={realized_vol:.4f}",
            f"avg_volume={avg_volume:.4f}",
        )
        return RegimeAssessment(trend, volatility, liquidity, confidence, reasons)

    def apply_to_state(self, state: MarketState, assessment: RegimeAssessment) -> MarketState:
        return MarketState(
            ts=state.ts,
            symbol=state.symbol,
            open=state.open,
            high=state.high,
            low=state.low,
            close=state.close,
            volume=state.volume,
            order_book_imbalance=state.order_book_imbalance,
            funding_rate=state.funding_rate,
            macro_signal=state.macro_signal,
            trend=assessment.trend,
            volatility=assessment.volatility,
            liquidity=assessment.liquidity,
        )

    @staticmethod
    def _confidence(move_pct: float, realized_vol: float, avg_volume: float) -> float:
        trend_score = min(1.0, abs(float(move_pct)) / 5.0)
        vol_score = min(1.0, float(realized_vol) / 4.0)
        liquidity_score = 1.0 if avg_volume > 0 else 0.0
        return max(0.0, min(1.0, (trend_score * 0.4) + (vol_score * 0.3) + (liquidity_score * 0.3)))
