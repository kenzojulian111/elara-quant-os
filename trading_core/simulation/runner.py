from __future__ import annotations

from dataclasses import dataclass
from typing import List

from trading_core.data_contracts import MarketState, StrategyDecision, StrategyPerformance
from trading_core.evaluation.metrics import summarize_performance
from trading_core.indicators.basic import IndicatorCalculator
from trading_core.strategy.base import PositionState, Strategy


@dataclass
class SimulationResult:
    performance: StrategyPerformance
    equity_curve: List[float]
    trade_pnls: List[float]


class SimulationRunner:
    def __init__(self, risk_per_trade_pct: float = 0.01):
        self.risk_per_trade_pct = float(risk_per_trade_pct)

    def run(self, strategy: Strategy, replay_engine) -> SimulationResult:
        calculator = IndicatorCalculator()
        position = PositionState()
        equity = 0.0
        trade_pnls: List[float] = []
        equity_curve: List[float] = []

        initial_capital = float(getattr(strategy.profile, "params", {}).get("initial_capital", 1000.0))
        equity = float(initial_capital)
        last_state: MarketState | None = None

        for state in replay_engine:
            last_state = state
            indicators = calculator.update(state)

            if position.direction:
                exit_price = None
                if position.direction == "long":
                    if position.stop_loss is not None and state.low <= position.stop_loss:
                        exit_price = position.stop_loss
                    elif position.take_profit is not None and state.high >= position.take_profit:
                        exit_price = position.take_profit
                elif position.direction == "short":
                    if position.stop_loss is not None and state.high >= position.stop_loss:
                        exit_price = position.stop_loss
                    elif position.take_profit is not None and state.low <= position.take_profit:
                        exit_price = position.take_profit

                if exit_price is not None and position.entry_price is not None:
                    pnl = self._calc_pnl(position.direction, position.entry_price, exit_price, position.units)
                    equity += pnl
                    trade_pnls.append(pnl)
                    position = PositionState()

            decision = strategy.decide(state, indicators, position)
            if decision.action in {"enter_long", "enter_short"} and position.direction is None:
                position = self._open_position(state, decision, equity)
            elif decision.action == "exit" and position.direction:
                exit_price = state.close
                pnl = self._calc_pnl(position.direction, position.entry_price, exit_price, position.units)
                equity += pnl
                trade_pnls.append(pnl)
                position = PositionState()

            equity_curve.append(float(equity))

        if position.direction and last_state and position.entry_price is not None:
            exit_price = last_state.close
            pnl = self._calc_pnl(position.direction, position.entry_price, exit_price, position.units)
            equity += pnl
            trade_pnls.append(pnl)
            equity_curve.append(float(equity))

        performance = summarize_performance(strategy.profile.strategy_id, equity_curve, trade_pnls)
        return SimulationResult(performance=performance, equity_curve=equity_curve, trade_pnls=trade_pnls)

    def _open_position(self, state: MarketState, decision: StrategyDecision, equity: float) -> PositionState:
        stop_loss = decision.stop_loss if decision.stop_loss is not None else state.close
        take_profit = decision.take_profit
        units = self._calc_units(state.close, stop_loss, equity)
        return PositionState(
            direction="long" if decision.action == "enter_long" else "short",
            entry_price=state.close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            units=units,
        )

    def _calc_units(self, entry: float, stop_loss: float, equity: float) -> float:
        risk_amount = max(0.0, equity * float(self.risk_per_trade_pct))
        risk_per_unit = abs(float(entry) - float(stop_loss))
        if risk_per_unit <= 1e-9:
            return 0.0
        return risk_amount / risk_per_unit

    def _calc_pnl(self, direction: str, entry: float | None, exit_price: float | None, units: float | None) -> float:
        # If any required value is missing, treat pnl as zero
        if entry is None or exit_price is None or units is None:
            return 0.0
        size = float(units)
        if direction == "long":
            return (float(exit_price) - float(entry)) * size
        return (float(entry) - float(exit_price)) * size

