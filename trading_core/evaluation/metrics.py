from __future__ import annotations

from typing import Iterable, List

from trading_core.data_contracts import StrategyPerformance


def _returns_from_equity(equity: List[float]) -> List[float]:
    if len(equity) < 2:
        return []
    out = []
    for prev, curr in zip(equity[:-1], equity[1:]):
        if prev <= 0:
            continue
        out.append((curr - prev) / prev)
    return out


def compute_max_drawdown_pct(equity: List[float]) -> float:
    if not equity:
        return 0.0
    peak = float(equity[0])
    max_dd = 0.0
    for val in equity:
        peak = max(peak, float(val))
        if peak > 0:
            max_dd = max(max_dd, (peak - float(val)) / peak)
    return float(max_dd * 100.0)


def compute_sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    avg = sum(returns) / float(len(returns))
    variance = sum((r - avg) ** 2 for r in returns) / float(len(returns))
    std = variance ** 0.5
    if std <= 1e-12:
        return 0.0
    return float((avg / std) * (252.0 ** 0.5))


def compute_win_rate(trade_pnls: Iterable[float]) -> float:
    pnls = list(trade_pnls)
    if not pnls:
        return 0.0
    wins = sum(1 for pnl in pnls if pnl > 0)
    return float(wins) / float(len(pnls))


def summarize_performance(strategy_id: str, equity: List[float], trade_pnls: List[float]) -> StrategyPerformance:
    returns = _returns_from_equity(equity)
    return StrategyPerformance(
        strategy_id=strategy_id,
        trades=len(trade_pnls),
        pnl=float(sum(trade_pnls) if trade_pnls else 0.0),
        max_drawdown_pct=compute_max_drawdown_pct(equity),
        sharpe=compute_sharpe(returns),
        win_rate=compute_win_rate(trade_pnls),
    )

