from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List

from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.event_types import META_EVENT
from trading_core.data_contracts import StrategyPerformance
from trading_core.library.strategy_library import StrategyLibrary
from trading_core.portfolio.weight_store import WeightStore


@dataclass(frozen=True)
class MetaCognitionConfig:
    drawdown_limit_pct: float = 12.0
    sharpe_boost_threshold: float = 1.0
    win_rate_boost_threshold: float = 0.55
    penalty_multiplier: float = 0.6
    boost_multiplier: float = 1.15
    min_multiplier: float = 0.2
    max_multiplier: float = 2.0
    weight_store_path: str = "runtime/strategy_weights.json"
    ledger_path: str = "runtime/meta_ledger.jsonl"


@dataclass(frozen=True)
class StrategyAdjustment:
    strategy_id: str
    multiplier: float
    reason: str
    max_drawdown_pct: float
    sharpe: float
    win_rate: float


@dataclass(frozen=True)
class MetaCognitionReport:
    updated_at: str
    adjustments: List[StrategyAdjustment]


class MetaCognitionEngine:
    def __init__(self, config: MetaCognitionConfig | None = None, ledger: EventLedger | None = None):
        self.config = config or MetaCognitionConfig()
        self.ledger = ledger or (EventLedger(self.config.ledger_path) if self.config.ledger_path else None)

    def run(self, library: StrategyLibrary) -> MetaCognitionReport:
        adjustments = self._score_strategies(list(library.performance.values()))
        weights = {adj.strategy_id: adj.multiplier for adj in adjustments if abs(adj.multiplier - 1.0) > 1e-6}
        store = WeightStore.load(self.config.weight_store_path)
        store.weights = weights
        store.updated_at = datetime.now(timezone.utc).isoformat()
        store.save()

        report = MetaCognitionReport(updated_at=store.updated_at, adjustments=adjustments)
        if self.ledger:
            self.ledger.append(META_EVENT, {"updated_at": report.updated_at, "adjustments": [asdict(adj) for adj in report.adjustments]})
        return report

    def _score_strategies(self, performances: List[StrategyPerformance]) -> List[StrategyAdjustment]:
        adjustments = []
        for perf in performances:
            multiplier, reason = self._score(perf)
            adjustments.append(
                StrategyAdjustment(
                    strategy_id=perf.strategy_id,
                    multiplier=multiplier,
                    reason=reason,
                    max_drawdown_pct=float(perf.max_drawdown_pct),
                    sharpe=float(perf.sharpe),
                    win_rate=float(perf.win_rate),
                )
            )
        return adjustments

    def _score(self, perf: StrategyPerformance) -> tuple[float, str]:
        multiplier = 1.0
        reasons = []
        if float(perf.max_drawdown_pct) >= float(self.config.drawdown_limit_pct):
            multiplier *= float(self.config.penalty_multiplier)
            reasons.append("drawdown_limit")
        if float(perf.sharpe) >= float(self.config.sharpe_boost_threshold) and float(perf.win_rate) >= float(
            self.config.win_rate_boost_threshold
        ):
            multiplier *= float(self.config.boost_multiplier)
            reasons.append("strong_performance")
        multiplier = min(float(self.config.max_multiplier), max(float(self.config.min_multiplier), multiplier))
        reason = ",".join(reasons) if reasons else "neutral"
        return multiplier, reason

