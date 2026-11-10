from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import pandas as pd

from trading_core.library.strategy_library import StrategyLibrary
from trading_core.market.replay_engine import DataFrameReplayEngine
from trading_core.simulation.runner import SimulationRunner
from trading_core.simulation.scenario_runner import ScenarioSimulation
from trading_core.strategy.base import Strategy


@dataclass
class DiscoveryConfig:
    max_candidates: int = 16
    max_drawdown_pct: float = 15.0
    risk_per_trade_pct: float = 0.01
    objective: str = "min_drawdown"


class StrategyDiscoveryPipeline:
    def __init__(
        self,
        df: pd.DataFrame,
        symbol: str,
        strategies: List[Strategy],
        library_path: str,
        config: DiscoveryConfig | None = None,
    ):
        self.df = df
        self.symbol = str(symbol or "").strip().upper() or "BTC"
        self.strategies = list(strategies)
        self.library_path = str(library_path or "").strip()
        self.config = config or DiscoveryConfig()
        self.scenario_sim = ScenarioSimulation(risk_per_trade_pct=self.config.risk_per_trade_pct)
        self.runner = SimulationRunner(risk_per_trade_pct=self.config.risk_per_trade_pct)

    def run(self) -> StrategyLibrary:
        library = StrategyLibrary()
        library.metadata = {
            "objective": self.config.objective,
            "symbol": self.symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        candidates = self.strategies[: self.config.max_candidates]
        for strategy in candidates:
            report = self.scenario_sim.run(strategy, self.df, self.symbol)
            worst_dd = _worst_drawdown(report)
            if worst_dd > self.config.max_drawdown_pct:
                continue
            replay = DataFrameReplayEngine(self.df, symbol=self.symbol)
            result = self.runner.run(strategy, replay)
            library.register_profile(strategy.profile)
            library.update_performance(result.performance)

        if self.library_path:
            library.save(self.library_path)
        return library


def _worst_drawdown(report) -> float:
    values = [float(item.metrics.get("max_drawdown_pct", 0.0)) for item in report.reports]
    return max(values) if values else 0.0

