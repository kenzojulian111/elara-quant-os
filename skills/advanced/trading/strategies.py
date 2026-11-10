from __future__ import annotations

from typing import Tuple

import pandas as pd


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, float) and pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


class _BaseConfluenceStrategy:
    def __init__(self, config, logger=None, overrides=None, name: str = "swing", default_threshold: int = 4):
        self.config = config
        self.log = logger
        self.name = name
        cfg_map = (getattr(config, "STRATEGY_CONFIGS", {}) or {}).get(name, {}) or {}
        self.threshold = int(cfg_map.get("confluence_threshold", default_threshold) or default_threshold)
        self.params = dict(cfg_map.get("params", {}) or {})
        if overrides:
            self.params.update(overrides)

    def _latest_row(self, ltf_slice):
        if ltf_slice is None or getattr(ltf_slice, "empty", True):
            return None
        return ltf_slice.iloc[-1]

    def _score_trend_bias(self, close_px: float, ema_trend: float, htf_trend: str) -> Tuple[int, int]:
        score_long = 0
        score_short = 0
        if close_px > ema_trend and str(htf_trend or "").upper() == "UP":
            score_long += 2
        elif close_px < ema_trend and str(htf_trend or "").upper() == "DOWN":
            score_short += 2
        return score_long, score_short

    def _final_signal(self, score_long: int, score_short: int, market_regime: str):
        score = max(score_long, score_short)
        if score_long >= self.threshold and score_long > score_short:
            return score, "BUY", f"long={score_long} short={score_short} regime={market_regime}"
        if score_short >= self.threshold and score_short > score_long:
            return score, "SELL", f"long={score_long} short={score_short} regime={market_regime}"
        return score, "HOLD", f"long={score_long} short={score_short} regime={market_regime}"


class SwingStrategy(_BaseConfluenceStrategy):
    def __init__(self, config, logger=None, overrides=None):
        super().__init__(config, logger, overrides, name="swing", default_threshold=4)

    def calculate_confluence(self, ltf_slice, htf_trend: str, market_regime: str):
        row = self._latest_row(ltf_slice)
        if row is None:
            return 0, "HOLD", "no_data"

        close_px = _safe_float(row.get("close"), 0.0)
        ema_short = _safe_float(row.get("ema_short"), close_px)
        ema_long = _safe_float(row.get("ema_long"), close_px)
        ema_trend = _safe_float(row.get("ema_trend"), close_px)
        rsi = _safe_float(row.get("rsi"), 50.0)
        macd = _safe_float(row.get("macd"), 0.0)
        macd_signal = _safe_float(row.get("macd_signal"), 0.0)
        volume = _safe_float(row.get("volume"), 0.0)
        volume_ma = _safe_float(row.get("volume_ma"), volume)

        score_long, score_short = self._score_trend_bias(close_px, ema_trend, htf_trend)

        if ema_short > ema_long:
            score_long += 1
        elif ema_short < ema_long:
            score_short += 1

        buy_min = float(getattr(self.config, "RSI_BUY_MIN", 52.0) or 52.0)
        buy_max = float(getattr(self.config, "RSI_BUY_MAX", 68.0) or 68.0)
        sell_min = float(getattr(self.config, "RSI_SELL_MIN", 28.0) or 28.0)
        sell_max = float(getattr(self.config, "RSI_SELL_MAX", 48.0) or 48.0)
        if buy_min <= rsi <= buy_max:
            score_long += 1
        if sell_min <= rsi <= sell_max:
            score_short += 1

        if macd > macd_signal:
            score_long += 1
        elif macd < macd_signal:
            score_short += 1

        if volume_ma > 0 and volume >= volume_ma:
            if close_px >= ema_trend:
                score_long += 1
            else:
                score_short += 1

        if str(market_regime or "") == "trending":
            if str(htf_trend or "").upper() == "UP":
                score_long += 1
            elif str(htf_trend or "").upper() == "DOWN":
                score_short += 1

        return self._final_signal(score_long, score_short, market_regime)


class ScalpingStrategy(_BaseConfluenceStrategy):
    def __init__(self, config, logger=None, overrides=None):
        super().__init__(config, logger, overrides, name="scalping", default_threshold=3)

    def calculate_confluence(self, ltf_slice, htf_trend: str, market_regime: str):
        row = self._latest_row(ltf_slice)
        if row is None:
            return 0, "HOLD", "no_data"

        close_px = _safe_float(row.get("close"), 0.0)
        ema_short = _safe_float(row.get("ema_short"), close_px)
        ema_long = _safe_float(row.get("ema_long"), close_px)
        rsi = _safe_float(row.get("rsi"), 50.0)
        macd = _safe_float(row.get("macd"), 0.0)
        macd_signal = _safe_float(row.get("macd_signal"), 0.0)

        score_long = 0
        score_short = 0

        if ema_short > ema_long:
            score_long += 1
        elif ema_short < ema_long:
            score_short += 1

        rsi_buy = float(getattr(self.config, "SCALP_RSI_BUY_BUFFER", 4.0) or 4.0)
        rsi_sell = float(getattr(self.config, "SCALP_RSI_SELL_BUFFER", 4.0) or 4.0)
        buy_min = float(getattr(self.config, "RSI_BUY_MIN", 52.0) or 52.0) - rsi_buy
        sell_max = float(getattr(self.config, "RSI_SELL_MAX", 48.0) or 48.0) + rsi_sell
        if rsi <= buy_min:
            score_long += 1
        if rsi >= sell_max:
            score_short += 1

        if macd > macd_signal:
            score_long += 1
        elif macd < macd_signal:
            score_short += 1

        if str(market_regime or "") == "range":
            if score_long > score_short:
                score_long += 1
            elif score_short > score_long:
                score_short += 1

        return self._final_signal(score_long, score_short, market_regime)

