from __future__ import annotations

from typing import Iterable

from trading_core.library.strategy_library import StrategyLibrary
from trading_core.simulation.runner import SimulationRunner
from trading_core.strategy.base import Strategy


class ResearchPipeline:
    def __init__(
        self,
        replay_engine,
        strategies: Iterable[Strategy],
        library_path: str,
        *,
        risk_per_trade_pct: float = 0.01,
    ):
        self.replay_engine = replay_engine
        self.strategies = list(strategies)
        self.library_path = str(library_path or "").strip()
        self.runner = SimulationRunner(risk_per_trade_pct=risk_per_trade_pct)

    def run(self) -> StrategyLibrary:
        library = StrategyLibrary()
        for strategy in self.strategies:
            library.register_profile(strategy.profile)
            result = self.runner.run(strategy, self.replay_engine)
            library.update_performance(result.performance)
        if self.library_path:
            library.save(self.library_path)
        return library

