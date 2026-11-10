from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trading_core.data_contracts import IndicatorSnapshot, MarketState, StrategyDecision, StrategyProfile


@dataclass
class PositionState:
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    units: Optional[float] = None


class Strategy:
    profile: StrategyProfile

    def decide(self, state: MarketState, indicators: IndicatorSnapshot, position: PositionState) -> StrategyDecision:
        raise NotImplementedError

