from __future__ import annotations

from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision, StrategyProfile
from trading_core.strategy.base import PositionState, Strategy


class MeanReversionStrategy(Strategy):
    def __init__(self, strategy_id: str = "mean_reversion_v1", params: dict | None = None):
        params = dict(params or {})
        self.profile = StrategyProfile(
            strategy_id=strategy_id,
            name="Mean Reversion",
            tags={"mean_reversion"},
            params={
                "rsi_overbought": params.get("rsi_overbought", 70),
                "rsi_oversold": params.get("rsi_oversold", 30),
                "rsi_exit": params.get("rsi_exit", 50),
                "atr_stop_mult": params.get("atr_stop_mult", 1.2),
                "atr_tp_mult": params.get("atr_tp_mult", 2.0),
                "adx_max": params.get("adx_max", 22),
                "bb_width_max": params.get("bb_width_max", 0.04),
                "bb_buffer": params.get("bb_buffer", 0.002),
            },
        )

    def decide(self, state: MarketState, indicators: IndicatorSnapshot, position: PositionState) -> StrategyDecision:
        atr = float(indicators.atr)
        stop_mult = float(self.profile.params.get("atr_stop_mult", 1.2))
        tp_mult = float(self.profile.params.get("atr_tp_mult", 2.0))
        rsi_overbought = float(self.profile.params.get("rsi_overbought", 70))
        rsi_oversold = float(self.profile.params.get("rsi_oversold", 30))
        rsi_exit = float(self.profile.params.get("rsi_exit", 50))
        adx_max = float(self.profile.params.get("adx_max", 22))
        bb_width_max = float(self.profile.params.get("bb_width_max", 0.04))
        bb_buffer = float(self.profile.params.get("bb_buffer", 0.002))
        adx = float(indicators.adx or 0.0)
        bb_upper = float(indicators.bb_upper or state.close)
        bb_lower = float(indicators.bb_lower or state.close)
        bb_mid = float(indicators.bb_mid or state.close)
        bb_width = float(indicators.bb_width or 0.0)
        range_ok = adx <= adx_max and bb_width <= bb_width_max

        if position.direction is None:
            if indicators.rsi <= rsi_oversold and state.close <= (bb_lower * (1.0 + bb_buffer)) and range_ok:
                stop = state.close - (atr * stop_mult)
                take_profit = state.close + (atr * tp_mult)
                return StrategyDecision(
                    action="enter_long",
                    confidence=0.55,
                    stop_loss=stop,
                    take_profit=take_profit,
                    reasons=("rsi_oversold", "price_near_lower_band", "range_regime_confirmed"),
                    metadata={"adx": adx, "bb_width": bb_width},
                )
            if indicators.rsi >= rsi_overbought and state.close >= (bb_upper * (1.0 - bb_buffer)) and range_ok:
                stop = state.close + (atr * stop_mult)
                take_profit = state.close - (atr * tp_mult)
                return StrategyDecision(
                    action="enter_short",
                    confidence=0.55,
                    stop_loss=stop,
                    take_profit=take_profit,
                    reasons=("rsi_overbought", "price_near_upper_band", "range_regime_confirmed"),
                    metadata={"adx": adx, "bb_width": bb_width},
                )
            return StrategyDecision(
                action="hold",
                confidence=0.0,
                reasons=("mean_reversion_entry_conditions_not_met",),
                metadata={"adx": adx, "bb_width": bb_width},
            )

        if position.direction == "long" and indicators.rsi >= rsi_exit:
            return StrategyDecision(action="exit", confidence=0.6, reasons=("mean_reversion_long_exit_rsi",))
        if position.direction == "short" and indicators.rsi <= rsi_exit:
            return StrategyDecision(action="exit", confidence=0.6, reasons=("mean_reversion_short_exit_rsi",))
        if position.direction == "long" and state.close >= bb_mid:
            return StrategyDecision(action="exit", confidence=0.55, reasons=("mean_reversion_long_exit_mid_band",))
        if position.direction == "short" and state.close <= bb_mid:
            return StrategyDecision(action="exit", confidence=0.55, reasons=("mean_reversion_short_exit_mid_band",))
        return StrategyDecision(action="hold", confidence=0.0, reasons=("mean_reversion_position_held",))

