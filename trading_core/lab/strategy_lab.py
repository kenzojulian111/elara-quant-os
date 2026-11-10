from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class StrategyVariant:
    variant_id: str
    parent_id: str
    params: dict[str, Any]
    mutation_reason: str = ""


@dataclass(frozen=True)
class StrategyExperiment:
    variant: StrategyVariant
    score: float
    validation_score: float
    overfit_penalty: float
    accepted: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


class StrategyLaboratory:
    def __init__(self, *, seed: int | None = None):
        self.random = random.Random(seed)

    def mutate(self, parent_id: str, params: dict[str, Any], *, scale: float = 0.1) -> StrategyVariant:
        mutated = {}
        for key, value in dict(params or {}).items():
            if isinstance(value, (int, float)):
                jitter = 1.0 + self.random.uniform(-abs(scale), abs(scale))
                mutated[key] = float(value) * jitter
            else:
                mutated[key] = value
        return StrategyVariant(uuid.uuid4().hex, str(parent_id or "strategy"), mutated, f"scale={scale:.4f}")

    def evaluate(self, variant: StrategyVariant, *, train_score: float, validation_score: float) -> StrategyExperiment:
        gap = max(0.0, float(train_score) - float(validation_score))
        overfit_penalty = min(1.0, gap)
        adjusted = float(validation_score) - overfit_penalty
        accepted = adjusted > 0.0 and overfit_penalty < 0.25
        reasons = (f"train_score={train_score:.4f}", f"validation_score={validation_score:.4f}", f"overfit_penalty={overfit_penalty:.4f}")
        return StrategyExperiment(variant, adjusted, float(validation_score), overfit_penalty, accepted, reasons)

    def tournament(self, experiments: Iterable[StrategyExperiment], *, limit: int = 3) -> tuple[StrategyExperiment, ...]:
        rows = sorted(tuple(experiments or ()), key=lambda item: (-item.score, item.overfit_penalty, item.variant.variant_id))
        return tuple(row for row in rows if row.accepted)[: max(1, int(limit or 3))]
