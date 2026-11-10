from __future__ import annotations

from dataclasses import dataclass
from typing import List

from trading_core.data_contracts import StrategyAllocation
from trading_core.portfolio.performance_tracker import StrategyPerformanceTracker


@dataclass
class AllocationPolicy:
    max_weight: float = 0.6
    min_weight: float = 0.0


class CapitalAllocator:
    def __init__(self, policy: AllocationPolicy):
        self.policy = policy

    def allocate(self, tracker: StrategyPerformanceTracker, strategy_ids: List[str]) -> List[StrategyAllocation]:
        if not strategy_ids:
            return []
        scores = {sid: max(0.0, tracker.effective_score(sid)) for sid in strategy_ids}
        total = sum(scores.values())
        if total <= 0:
            weight = 1.0 / float(len(strategy_ids))
            return [StrategyAllocation(strategy_id=sid, weight=weight) for sid in strategy_ids]

        allocations = []
        for sid in strategy_ids:
            weight = scores[sid] / total
            weight = min(self.policy.max_weight, max(self.policy.min_weight, weight))
            allocations.append(StrategyAllocation(strategy_id=sid, weight=weight))

        return self._renormalize(allocations)

    def _renormalize(self, allocations: List[StrategyAllocation]) -> List[StrategyAllocation]:
        total = sum(a.weight for a in allocations)
        if total <= 0:
            return allocations
        return [StrategyAllocation(strategy_id=a.strategy_id, weight=a.weight / total) for a in allocations]

