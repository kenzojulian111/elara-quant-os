from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from trading_core.data_contracts import StrategyAllocation


CorrelationMatrix = Dict[Tuple[str, str], float]


@dataclass
class RiskBalancePolicy:
    max_correlation: float = 0.85
    correlation_penalty: float = 0.2


class RiskBalancer:
    def __init__(self, policy: RiskBalancePolicy):
        self.policy = policy

    def balance(
        self,
        allocations: Iterable[StrategyAllocation],
        correlations: CorrelationMatrix | None = None,
    ) -> list[StrategyAllocation]:
        out = [StrategyAllocation(strategy_id=a.strategy_id, weight=float(a.weight)) for a in allocations]
        if not correlations:
            return self._renormalize(out)

        weights = {a.strategy_id: a.weight for a in out}
        ids = list(weights.keys())
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                corr = correlations.get((left, right))
                if corr is None:
                    corr = correlations.get((right, left))
                if corr is None:
                    continue
                if float(corr) >= self.policy.max_correlation:
                    weights[left] *= (1.0 - self.policy.correlation_penalty)
                    weights[right] *= (1.0 - self.policy.correlation_penalty)

        balanced = [StrategyAllocation(strategy_id=sid, weight=weights[sid]) for sid in ids]
        return self._renormalize(balanced)

    def _renormalize(self, allocations: list[StrategyAllocation]) -> list[StrategyAllocation]:
        total = sum(a.weight for a in allocations)
        if total <= 0:
            return allocations
        return [StrategyAllocation(strategy_id=a.strategy_id, weight=a.weight / total) for a in allocations]

