from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from trading_core.data_contracts import StrategyAllocation


def _write_json_atomic(path: str, payload: dict) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
    os.replace(tmp_path, path)


@dataclass
class WeightStore:
    path: str
    weights: Dict[str, float] = field(default_factory=dict)
    updated_at: str = ""

    @classmethod
    def load(cls, path: str) -> "WeightStore":
        if not path or not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls(
            path=path,
            weights={k: float(v) for k, v in (raw.get("weights") or {}).items()},
            updated_at=str(raw.get("updated_at") or ""),
        )

    def save(self) -> None:
        payload = {
            "weights": self.weights,
            "updated_at": self.updated_at or datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(self.path, payload)

    def apply(self, allocations: List[StrategyAllocation]) -> List[StrategyAllocation]:
        if not allocations:
            return allocations
        adjusted = []
        for allocation in allocations:
            mult = float(self.weights.get(allocation.strategy_id, 1.0))
            adjusted.append(
                StrategyAllocation(
                    strategy_id=allocation.strategy_id,
                    weight=float(allocation.weight) * mult,
                )
            )
        return self._renormalize(adjusted)

    def _renormalize(self, allocations: List[StrategyAllocation]) -> List[StrategyAllocation]:
        total = sum(a.weight for a in allocations)
        if total <= 0:
            return allocations
        return [StrategyAllocation(strategy_id=a.strategy_id, weight=a.weight / total) for a in allocations]

