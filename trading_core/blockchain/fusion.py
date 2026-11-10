from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class HybridFinancialState:
    risk_score: float
    situational_mode: str
    component_scores: Mapping[str, float]
    reasons: tuple[str, ...]


class HybridFinancialFusion:
    DEFAULT_WEIGHTS = {"market": 0.25, "macro": 0.15, "onchain": 0.20, "portfolio": 0.15, "governance": 0.15, "runtime": 0.10}

    def fuse(self, scores: Mapping[str, float]) -> HybridFinancialState:
        normalized = {key: _clamp(value) for key, value in scores.items()}
        weighted = sum(normalized.get(key, 0.0) * weight for key, weight in self.DEFAULT_WEIGHTS.items())
        total = sum(weight for key, weight in self.DEFAULT_WEIGHTS.items() if key in normalized)
        risk = weighted / total if total else 0.0
        mode = "crisis" if risk >= 0.75 else "defensive" if risk >= 0.50 else "balanced" if risk >= 0.25 else "opportunistic"
        return HybridFinancialState(risk, mode, normalized, (f"hybrid_risk={risk:.4f}", f"situational_mode={mode}"))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
