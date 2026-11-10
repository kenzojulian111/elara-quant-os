from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from trading_core.data_contracts import IndicatorSnapshot, MarketState


def _ema(prev: Optional[float], value: float, period: int) -> float:
    if prev is None:
        return float(value)
    alpha = 2.0 / (float(period) + 1.0)
    return (alpha * float(value)) + ((1.0 - alpha) * float(prev))


@dataclass
class IndicatorCalculator:
    ema_fast_period: int = 12
    ema_slow_period: int = 26
    macd_signal_period: int = 9
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    bb_period: int = 20
    bb_std_mult: float = 2.0

    _ema_fast: Optional[float] = None
    _ema_slow: Optional[float] = None
    _macd_signal: Optional[float] = None
    _avg_gain: Optional[float] = None
    _avg_loss: Optional[float] = None
    _avg_tr: Optional[float] = None
    _dm_plus_smoothed: Optional[float] = None
    _dm_minus_smoothed: Optional[float] = None
    _adx: Optional[float] = None
    _prev_close: Optional[float] = None
    _prev_high: Optional[float] = None
    _prev_low: Optional[float] = None
    _prev_rsi: Optional[float] = None
    _rsi_warmup: int = 0
    _atr_warmup: int = 0
    _adx_warmup: int = 0
    _close_window: deque = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._close_window = deque(maxlen=int(self.bb_period))

    def update(self, state: MarketState) -> IndicatorSnapshot:
        close = float(state.close)
        high = float(state.high)
        low = float(state.low)

        self._ema_fast = _ema(self._ema_fast, close, self.ema_fast_period)
        self._ema_slow = _ema(self._ema_slow, close, self.ema_slow_period)
        macd_line = float((self._ema_fast or close) - (self._ema_slow or close))
        self._macd_signal = _ema(self._macd_signal, macd_line, self.macd_signal_period)
        macd_signal = float(self._macd_signal or 0.0)
        macd_hist = float(macd_line - macd_signal)

        rsi = 50.0
        if self._prev_close is not None:
            delta = close - self._prev_close
            gain = max(0.0, delta)
            loss = max(0.0, -delta)

            if self._avg_gain is None or self._avg_loss is None:
                self._avg_gain = gain
                self._avg_loss = loss
            else:
                period = float(self.rsi_period)
                self._avg_gain = ((self._avg_gain * (period - 1.0)) + gain) / period
                self._avg_loss = ((self._avg_loss * (period - 1.0)) + loss) / period

            self._rsi_warmup += 1
            if self._rsi_warmup >= self.rsi_period and self._avg_loss is not None:
                if self._avg_loss <= 1e-12:
                    rsi = 100.0
                else:
                    rs = self._avg_gain / self._avg_loss
                    rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_slope = 0.0
        if self._prev_rsi is not None:
            rsi_slope = float(rsi - self._prev_rsi)
        self._prev_rsi = float(rsi)

        tr = 0.0
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))

        if self._avg_tr is None:
            self._avg_tr = tr
        else:
            period = float(self.atr_period)
            self._avg_tr = ((self._avg_tr * (period - 1.0)) + tr) / period

        self._atr_warmup += 1
        atr = float(self._avg_tr or 0.0)
        atr_pct = (atr / close * 100.0) if close > 0 else 0.0

        plus_dm = 0.0
        minus_dm = 0.0
        if self._prev_high is not None and self._prev_low is not None:
            up_move = high - self._prev_high
            down_move = self._prev_low - low
            if up_move > down_move and up_move > 0:
                plus_dm = up_move
            if down_move > up_move and down_move > 0:
                minus_dm = down_move

        if self._dm_plus_smoothed is None:
            self._dm_plus_smoothed = plus_dm
        else:
            period = float(self.adx_period)
            self._dm_plus_smoothed = ((self._dm_plus_smoothed * (period - 1.0)) + plus_dm) / period

        if self._dm_minus_smoothed is None:
            self._dm_minus_smoothed = minus_dm
        else:
            period = float(self.adx_period)
            self._dm_minus_smoothed = ((self._dm_minus_smoothed * (period - 1.0)) + minus_dm) / period

        adx_val = 0.0
        if self._avg_tr is not None and self._avg_tr > 1e-12:
            di_plus = 100.0 * (float(self._dm_plus_smoothed) / float(self._avg_tr))
            di_minus = 100.0 * (float(self._dm_minus_smoothed) / float(self._avg_tr))
            denom = di_plus + di_minus
            if denom > 1e-12:
                dx = 100.0 * abs(di_plus - di_minus) / denom
                if self._adx is None:
                    self._adx = dx
                else:
                    period = float(self.adx_period)
                    self._adx = ((self._adx * (period - 1.0)) + dx) / period
        adx_val = float(self._adx or 0.0)
        self._adx_warmup += 1

        self._close_window.append(close)
        bb_mid = close
        bb_upper = close
        bb_lower = close
        bb_width = 0.0
        if len(self._close_window) >= max(5, int(self.bb_period)):
            arr = np.array(self._close_window, dtype=float)
            bb_mid = float(arr.mean())
            bb_std = float(arr.std(ddof=0))
            bb_upper = bb_mid + (self.bb_std_mult * bb_std)
            bb_lower = bb_mid - (self.bb_std_mult * bb_std)
            if bb_mid > 1e-12:
                bb_width = float((bb_upper - bb_lower) / bb_mid)

        self._prev_close = close
        self._prev_high = high
        self._prev_low = low

        return IndicatorSnapshot(
            ema_fast=float(self._ema_fast or close),
            ema_slow=float(self._ema_slow or close),
            rsi=float(rsi),
            atr=float(atr),
            atr_pct=float(atr_pct),
            macd=float(macd_line),
            macd_signal=float(macd_signal),
            macd_hist=float(macd_hist),
            adx=float(adx_val),
            bb_upper=float(bb_upper),
            bb_lower=float(bb_lower),
            bb_mid=float(bb_mid),
            bb_width=float(bb_width),
            rsi_slope=float(rsi_slope),
        )

