from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Iterable


@dataclass(frozen=True)
class PositionExposure:
    symbol: str
    asset_class: str
    notional: float
    strategy_id: str = ""


@dataclass(frozen=True)
class PortfolioIntelligenceReport:
    total_notional: float
    exposure_by_asset_class: dict[str, float]
    concentration_symbol: str
    concentration_pct: float
    correlation_clusters: tuple[tuple[str, ...], ...] = ()
    reasons: tuple[str, ...] = ()


class PortfolioIntelligence:
    def assess(
        self,
        exposures: Iterable[PositionExposure],
        *,
        returns_by_symbol: dict[str, list[float]] | None = None,
    ) -> PortfolioIntelligenceReport:
        rows = tuple(exposures or ())
        total = sum(abs(float(row.notional)) for row in rows)
        by_class: dict[str, float] = {}
        by_symbol: dict[str, float] = {}
        for row in rows:
            by_class[row.asset_class] = by_class.get(row.asset_class, 0.0) + abs(float(row.notional))
            by_symbol[row.symbol] = by_symbol.get(row.symbol, 0.0) + abs(float(row.notional))
        top_symbol = ""
        top_value = 0.0
        if by_symbol:
            top_symbol, top_value = sorted(by_symbol.items(), key=lambda item: (-item[1], item[0]))[0]
        concentration = (top_value / total) if total else 0.0
        clusters = self._correlation_clusters(returns_by_symbol or {})
        reasons = []
        if concentration >= 0.5:
            reasons.append(f"portfolio_concentration_high={top_symbol}:{concentration:.4f}")
        if clusters:
            reasons.append("correlation_cluster_detected")
        return PortfolioIntelligenceReport(
            total_notional=total,
            exposure_by_asset_class=by_class,
            concentration_symbol=top_symbol,
            concentration_pct=concentration,
            correlation_clusters=clusters,
            reasons=tuple(reasons or ("portfolio_balanced",)),
        )

    def _correlation_clusters(self, returns_by_symbol: dict[str, list[float]]) -> tuple[tuple[str, ...], ...]:
        symbols = sorted(returns_by_symbol)
        visited: set[str] = set()
        clusters: list[tuple[str, ...]] = []
        for symbol in symbols:
            if symbol in visited:
                continue
            cluster = {symbol}
            for other in symbols:
                if other == symbol:
                    continue
                if self._corr(returns_by_symbol.get(symbol, []), returns_by_symbol.get(other, [])) >= 0.75:
                    cluster.add(other)
            visited.update(cluster)
            if len(cluster) > 1:
                clusters.append(tuple(sorted(cluster)))
        return tuple(clusters)

    @staticmethod
    def _corr(left: list[float], right: list[float]) -> float:
        n = min(len(left), len(right))
        if n < 2:
            return 0.0
        a = [float(x) for x in left[-n:]]
        b = [float(x) for x in right[-n:]]
        ma = sum(a) / n
        mb = sum(b) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sqrt(sum((x - ma) ** 2 for x in a))
        db = sqrt(sum((y - mb) ** 2 for y in b))
        if da <= 0 or db <= 0:
            return 0.0
        return num / (da * db)
