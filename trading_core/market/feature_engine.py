from __future__ import annotations

import pandas as pd


def add_market_features(df: pd.DataFrame, *, trend_window: int = 20, vol_window: int = 20) -> pd.DataFrame:
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    returns = close.pct_change().fillna(0.0)

    trend_score = close.pct_change(periods=trend_window).fillna(0.0)
    vol_score = returns.rolling(vol_window).std().fillna(0.0)
    # Ensure volume is a Series with the same index as out. If no volume column,
    # create a zero-filled Series to avoid calling Series methods on a float.
    if "volume" in out:
        volume = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    else:
        volume = pd.Series(0.0, index=out.index)
    volume_z = (volume - volume.rolling(vol_window).mean()) / volume.rolling(vol_window).std().replace(0, pd.NA)
    volume_z = volume_z.fillna(0.0)

    out["trend_score"] = trend_score
    out["volatility_score"] = vol_score
    out["liquidity_score"] = volume_z
    out["trend"] = trend_score.apply(_trend_label)
    out["volatility"] = vol_score.apply(_volatility_label)
    out["liquidity"] = volume_z.apply(_liquidity_label)
    return out


def _trend_label(value: float) -> str:
    if value >= 0.02:
        return "bullish"
    if value <= -0.02:
        return "bearish"
    return "neutral"


def _volatility_label(value: float) -> str:
    if value >= 0.05:
        return "high"
    if value >= 0.02:
        return "medium"
    return "low"


def _liquidity_label(value: float) -> str:
    if value >= 0.5:
        return "strong"
    if value <= -0.5:
        return "weak"
    return "medium"

