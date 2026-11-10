from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from trading_core.market.replay_engine import DataFrameReplayEngine
from trading_core.simulation.runner import SimulationRunner
from trading_core.simulation.scenario_generator import generate_scenarios
from trading_core.strategy.base import Strategy


@dataclass
class ScenarioReport:
    scenario: str
    metrics: Dict[str, float]


@dataclass
class StrategyScenarioReport:
    strategy_id: str
    reports: List[ScenarioReport] = field(default_factory=list)


class ScenarioSimulation:
    def __init__(self, *, risk_per_trade_pct: float = 0.01):
        self.runner = SimulationRunner(risk_per_trade_pct=risk_per_trade_pct)

    def run(self, strategy: Strategy, df, symbol: str) -> StrategyScenarioReport:
        scenarios = generate_scenarios(df)
        output = StrategyScenarioReport(strategy_id=strategy.profile.strategy_id)
        for scenario in scenarios:
            replay = DataFrameReplayEngine(scenario.frame, symbol=symbol)
            result = self.runner.run(strategy, replay)
            metrics = {
                "pnl": result.performance.pnl,
                "max_drawdown_pct": result.performance.max_drawdown_pct,
                "sharpe": result.performance.sharpe,
                "win_rate": result.performance.win_rate,
                "trades": float(result.performance.trades),
            }
            output.reports.append(ScenarioReport(scenario=scenario.name, metrics=metrics))
        return output

