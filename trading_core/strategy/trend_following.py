from __future__ import annotations

from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision, StrategyProfile
from trading_core.signals.technical import build_technical_signal
from trading_core.strategy.base import PositionState, Strategy


class TrendFollowingStrategy(Strategy):
    def __init__(self, strategy_id: str = "trend_following_v1", params: dict | None = None):
        params = dict(params or {})
        self.profile = StrategyProfile(
            strategy_id=strategy_id,
            name="Trend Following",
            tags={"trend"},
            params={
                "atr_stop_mult": params.get("atr_stop_mult", 1.5),
                "atr_tp_mult": params.get("atr_tp_mult", 3.0),
                "rsi_exit_long": params.get("rsi_exit_long", 75),
                "rsi_exit_short": params.get("rsi_exit_short", 25),
                "adx_min": params.get("adx_min", 18),
                "bb_width_min": params.get("bb_width_min", 0.01),
                "macd_exit_zero": params.get("macd_exit_zero", True),
            },
        )

    def decide(self, state: MarketState, indicators: IndicatorSnapshot, position: PositionState) -> StrategyDecision:
        signal = build_technical_signal(indicators)
        atr = float(indicators.atr)
        stop_mult = float(self.profile.params.get("atr_stop_mult", 1.5))
        tp_mult = float(self.profile.params.get("atr_tp_mult", 3.0))
        adx_min = float(self.profile.params.get("adx_min", 18))
        bb_width_min = float(self.profile.params.get("bb_width_min", 0.01))
        macd_exit_zero = bool(self.profile.params.get("macd_exit_zero", True))
        adx = float(indicators.adx or 0.0)
        bb_width = float(indicators.bb_width or 0.0)
        macd_hist = float(indicators.macd_hist or 0.0)

        if position.direction is None:
            if signal.direction == "bullish" and adx >= adx_min and bb_width >= bb_width_min:
                stop = state.close - (atr * stop_mult)
                take_profit = state.close + (atr * tp_mult)
                return StrategyDecision(
                    action="enter_long",
                    confidence=signal.confidence,
                    stop_loss=stop,
                    take_profit=take_profit,
                    reasons=signal.reasons + ("trend_following_entry_long", "adx_confirmed", "bb_width_confirmed"),
                    metadata={
                        "adx": adx,
                        "adx_min": adx_min,
                        "bb_width": bb_width,
                        "bb_width_min": bb_width_min,
                    },
                )
            if signal.direction == "bearish" and adx >= adx_min and bb_width >= bb_width_min:
                stop = state.close + (atr * stop_mult)
                take_profit = state.close - (atr * tp_mult)
                return StrategyDecision(
                    action="enter_short",
                    confidence=signal.confidence,
                    stop_loss=stop,
                    take_profit=take_profit,
                    reasons=signal.reasons + ("trend_following_entry_short", "adx_confirmed", "bb_width_confirmed"),
                    metadata={
                        "adx": adx,
                        "adx_min": adx_min,
                        "bb_width": bb_width,
                        "bb_width_min": bb_width_min,
                    },
                )
            return StrategyDecision(
                action="hold",
                confidence=0.0,
                reasons=("trend_entry_conditions_not_met",),
                metadata={
                    "signal_direction": signal.direction,
                    "adx": adx,
                    "adx_min": adx_min,
                    "bb_width": bb_width,
                    "bb_width_min": bb_width_min,
                },
            )

        if position.direction == "long":
            if indicators.ema_fast < indicators.ema_slow or indicators.rsi > float(self.profile.params.get("rsi_exit_long", 75)):
                return StrategyDecision(
                    action="exit",
                    confidence=0.6,
                    reasons=("trend_long_exit_reversal",),
                )
            if macd_exit_zero and macd_hist < 0:
                return StrategyDecision(
                    action="exit",
                    confidence=0.6,
                    reasons=("trend_long_exit_macd_flip",),
                )
        elif position.direction == "short":
            if indicators.ema_fast > indicators.ema_slow or indicators.rsi < float(self.profile.params.get("rsi_exit_short", 25)):
                return StrategyDecision(
                    action="exit",
                    confidence=0.6,
                    reasons=("trend_short_exit_reversal",),
                )
            if macd_exit_zero and macd_hist > 0:
                return StrategyDecision(
                    action="exit",
                    confidence=0.6,
                    reasons=("trend_short_exit_macd_flip",),
                )
        return StrategyDecision(action="hold", confidence=0.0, reasons=("trend_position_held",))

