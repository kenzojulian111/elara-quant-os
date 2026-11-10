from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt


@dataclass(frozen=True)
class CrossMarketSeries:
    name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class CrossMarketRelation:
    left: str
    right: str
    correlation: float


@dataclass(frozen=True)
class CrossMarketCorrelationReport:
    relations: tuple[CrossMarketRelation, ...] = field(default_factory=tuple)
    systemic_pairs: tuple[CrossMarketRelation, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


class CrossMarketCorrelationEngine:
    def assess(
        self,
        series: tuple[CrossMarketSeries, ...],
        *,
        systemic_threshold: float = 0.75,
    ) -> CrossMarketCorrelationReport:
        relations: list[CrossMarketRelation] = []
        for index, left in enumerate(series):
            for right in series[index + 1 :]:
                correlation = _correlation(left.values, right.values)
                relations.append(CrossMarketRelation(left.name, right.name, correlation))
        systemic = tuple(
            relation for relation in relations if abs(relation.correlation) >= abs(float(systemic_threshold))
        )
        return CrossMarketCorrelationReport(
            relations=tuple(relations),
            systemic_pairs=systemic,
            reasons=tuple(
                f"systemic_pair={relation.left}:{relation.right}:{relation.correlation:.4f}" for relation in systemic
            ),
        )


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    count = min(len(left), len(right))
    if count < 2:
        return 0.0
    left_values = tuple(float(value) for value in left[:count])
    right_values = tuple(float(value) for value in right[:count])
    left_mean = sum(left_values) / count
    right_mean = sum(right_values) / count
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values))
    left_variance = sum((value - left_mean) ** 2 for value in left_values)
    right_variance = sum((value - right_mean) ** 2 for value in right_values)
    denominator = sqrt(left_variance * right_variance)
    return covariance / denominator if denominator else 0.0
