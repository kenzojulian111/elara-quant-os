from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import IndicatorSnapshot


@dataclass(frozen=True)
class TechnicalSignal:
    direction: str
    confidence: float
    reasons: tuple[str, ...] = ()


def build_technical_signal(snapshot: IndicatorSnapshot) -> TechnicalSignal:
    ema_fast = snapshot.ema_fast
    ema_slow = snapshot.ema_slow
    rsi = snapshot.rsi
    adx = float(snapshot.adx or 0.0)
    macd_hist = float(snapshot.macd_hist or 0.0)
    bb_width = float(snapshot.bb_width or 0.0)

    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    spread = (ema_fast - ema_slow) / max(1e-9, abs(ema_slow))
    trend_strength = _clamp((adx - 15.0) / 25.0, 0.0, 1.0)
    momentum = _clamp(macd_hist / max(1e-9, abs(ema_slow)), -1.0, 1.0)
    vol_ok = bb_width >= 0.01

    if ema_fast > ema_slow and macd_hist >= 0 and rsi < 72 and adx >= 18 and vol_ok:
        confidence = 0.55 + _clamp(abs(spread) * 3.0, 0.0, 0.20)
        confidence += _clamp(trend_strength * 0.20, 0.0, 0.20)
        confidence += 0.05 if momentum > 0 else 0.0
        return TechnicalSignal(
            direction="bullish",
            confidence=float(_clamp(confidence, 0.0, 0.95)),
            reasons=(
                "ema_fast_above_slow",
                "macd_hist_non_negative",
                "rsi_below_overbought",
                "adx_trending",
                "volatility_confirmed",
            ),
        )
    if ema_fast < ema_slow and macd_hist <= 0 and rsi > 28 and adx >= 18 and vol_ok:
        confidence = 0.55 + _clamp(abs(spread) * 3.0, 0.0, 0.20)
        confidence += _clamp(trend_strength * 0.20, 0.0, 0.20)
        confidence += 0.05 if momentum < 0 else 0.0
        return TechnicalSignal(
            direction="bearish",
            confidence=float(_clamp(confidence, 0.0, 0.95)),
            reasons=(
                "ema_fast_below_slow",
                "macd_hist_non_positive",
                "rsi_above_oversold",
                "adx_trending",
                "volatility_confirmed",
            ),
        )
    return TechnicalSignal(direction="neutral", confidence=0.4, reasons=("no_directional_consensus",))

