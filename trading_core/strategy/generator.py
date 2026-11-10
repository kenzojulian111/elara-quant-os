from __future__ import annotations

from typing import List

from trading_core.strategy.mean_reversion import MeanReversionStrategy
from trading_core.strategy.trend_following import TrendFollowingStrategy


def generate_strategies(max_candidates: int = 16) -> List:
    strategies = []

    trend_params = [
        {"atr_stop_mult": 1.2, "atr_tp_mult": 2.0, "rsi_exit_long": 70, "rsi_exit_short": 30},
        {"atr_stop_mult": 1.5, "atr_tp_mult": 3.0, "rsi_exit_long": 75, "rsi_exit_short": 25},
        {"atr_stop_mult": 2.0, "atr_tp_mult": 4.0, "rsi_exit_long": 78, "rsi_exit_short": 22},
    ]
    for idx, params in enumerate(trend_params, start=1):
        strategies.append(TrendFollowingStrategy(strategy_id=f"trend_following_v{idx}", params=params))

    mean_params = [
        {"rsi_overbought": 70, "rsi_oversold": 30, "rsi_exit": 50, "atr_stop_mult": 1.2, "atr_tp_mult": 2.0},
        {"rsi_overbought": 68, "rsi_oversold": 32, "rsi_exit": 52, "atr_stop_mult": 1.0, "atr_tp_mult": 1.8},
        {"rsi_overbought": 72, "rsi_oversold": 28, "rsi_exit": 50, "atr_stop_mult": 1.4, "atr_tp_mult": 2.2},
    ]
    for idx, params in enumerate(mean_params, start=1):
        strategies.append(MeanReversionStrategy(strategy_id=f"mean_reversion_v{idx}", params=params))

    return strategies[:max_candidates]

