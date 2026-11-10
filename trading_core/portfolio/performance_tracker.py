from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from trading_core.data_contracts import StrategyPerformance


@dataclass(frozen=True)
class SelfReviewPolicy:
    min_trades: int = 5
    weak_win_rate: float = 0.45
    strong_win_rate: float = 0.58
    weak_sharpe: float = 0.2
    strong_sharpe: float = 1.0
    soft_drawdown_pct: float = 8.0
    hard_drawdown_pct: float = 12.0
    degraded_multiplier: float = 0.5
    strong_multiplier: float = 1.15


@dataclass(frozen=True)
class StrategyHealth:
    strategy_id: str
    status: str
    score_multiplier: float
    reasons: tuple[str, ...] = ()


@dataclass
class StrategyPerformanceTracker:
    strategies: Dict[str, StrategyPerformance] = field(default_factory=dict)
    review_policy: SelfReviewPolicy = field(default_factory=SelfReviewPolicy)

    def update(self, performance: StrategyPerformance) -> None:
        self.strategies[performance.strategy_id] = performance

    def score(self, strategy_id: str) -> float:
        perf = self.strategies.get(strategy_id)
        if not perf:
            return 0.0
        dd = max(0.0, float(perf.max_drawdown_pct))
        return 1.0 / (1.0 + dd)

    def review(self, strategy_id: str) -> StrategyHealth:
        perf = self.strategies.get(strategy_id)
        if not perf:
            return StrategyHealth(
                strategy_id=strategy_id,
                status="unproven",
                score_multiplier=1.0,
                reasons=("no_performance_history",),
            )

        trades = int(perf.trades)
        if trades < int(self.review_policy.min_trades):
            return StrategyHealth(
                strategy_id=strategy_id,
                status="warming_up",
                score_multiplier=1.0,
                reasons=("insufficient_trade_history", f"trades={trades}"),
            )

        reasons: list[str] = []
        drawdown_pct = max(0.0, float(perf.max_drawdown_pct))
        win_rate = float(perf.win_rate)
        sharpe = float(perf.sharpe)
        pnl = float(perf.pnl)

        if drawdown_pct >= float(self.review_policy.hard_drawdown_pct):
            return StrategyHealth(
                strategy_id=strategy_id,
                status="blocked",
                score_multiplier=0.0,
                reasons=(f"drawdown_above_hard={drawdown_pct:.2f}", "auto_disabled_for_capital_protection"),
            )

        degraded = False
        if drawdown_pct >= float(self.review_policy.soft_drawdown_pct):
            degraded = True
            reasons.append(f"drawdown_above_soft={drawdown_pct:.2f}")
        if win_rate < float(self.review_policy.weak_win_rate):
            degraded = True
            reasons.append(f"win_rate_below_soft={win_rate:.2f}")
        if sharpe < float(self.review_policy.weak_sharpe):
            degraded = True
            reasons.append(f"sharpe_below_soft={sharpe:.2f}")
        if pnl <= 0.0:
            degraded = True
            reasons.append(f"non_positive_pnl={pnl:.2f}")

        if degraded:
            return StrategyHealth(
                strategy_id=strategy_id,
                status="degraded",
                score_multiplier=float(self.review_policy.degraded_multiplier),
                reasons=tuple(reasons),
            )

        if (
            win_rate >= float(self.review_policy.strong_win_rate)
            and sharpe >= float(self.review_policy.strong_sharpe)
            and drawdown_pct <= (float(self.review_policy.soft_drawdown_pct) / 2.0)
            and pnl > 0.0
        ):
            return StrategyHealth(
                strategy_id=strategy_id,
                status="strong",
                score_multiplier=float(self.review_policy.strong_multiplier),
                reasons=("performance_above_target",),
            )

        return StrategyHealth(
            strategy_id=strategy_id,
            status="healthy",
            score_multiplier=1.0,
            reasons=("performance_within_band",),
        )

    def effective_score(self, strategy_id: str) -> float:
        return float(self.score(strategy_id)) * float(self.review(strategy_id).score_multiplier)

    def reviews(self, strategy_ids: List[str] | None = None) -> Dict[str, StrategyHealth]:
        target_ids = strategy_ids or list(self.strategies.keys())
        return {strategy_id: self.review(strategy_id) for strategy_id in target_ids}

    def ranked(self) -> List[StrategyPerformance]:
        return sorted(self.strategies.values(), key=lambda perf: self.effective_score(perf.strategy_id), reverse=True)

