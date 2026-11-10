from __future__ import annotations

from trading_core.data_contracts import StrategyProfile
from trading_core.strategy.base import Strategy
from trading_core.strategy.mean_reversion import MeanReversionStrategy
from trading_core.strategy.trend_following import TrendFollowingStrategy


class StrategyFactory:
    def create(self, profile: StrategyProfile) -> Strategy:
        strategy_id = str(profile.strategy_id or "").strip()
        tags = {str(tag).lower() for tag in (profile.tags or [])}

        if "trend" in tags or "trend_following" in strategy_id:
            return TrendFollowingStrategy(strategy_id=strategy_id, params=profile.params)
        if "mean_reversion" in tags or "mean_reversion" in strategy_id:
            return MeanReversionStrategy(strategy_id=strategy_id, params=profile.params)

        raise ValueError(f"Unknown strategy profile: {strategy_id}")

