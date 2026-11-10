from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class InstitutionalExposure:
    symbol: str
    usd_value: float
    beta: float = 1.0
    liquidity_score: float = 1.0


@dataclass(frozen=True)
class PortfolioStressReport:
    gross_exposure: float
    stressed_loss: float
    drawdown_probability: float
    hedge_required: bool
    hedge_targets: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


class InstitutionalPortfolioCognition:
    def assess(
        self,
        exposures: tuple[InstitutionalExposure, ...],
        *,
        stress_loss_by_symbol: Mapping[str, float],
        max_drawdown_ratio: float = 0.15,
    ) -> PortfolioStressReport:
        gross = sum(abs(float(exposure.usd_value)) for exposure in exposures)
        stressed = sum(
            abs(float(exposure.usd_value))
            * abs(float(stress_loss_by_symbol.get(exposure.symbol, 0.0)))
            * max(0.0, float(exposure.beta))
            for exposure in exposures
        )
        ratio = stressed / gross if gross else 0.0
        hedge_required = ratio >= max(0.0, float(max_drawdown_ratio))
        targets = tuple(
            exposure.symbol
            for exposure in sorted(exposures, key=lambda item: abs(item.usd_value), reverse=True)
            if hedge_required and exposure.usd_value > 0
        )
        return PortfolioStressReport(
            gross_exposure=gross,
            stressed_loss=stressed,
            drawdown_probability=min(1.0, ratio / max(0.01, float(max_drawdown_ratio))),
            hedge_required=hedge_required,
            hedge_targets=targets,
            reasons=(f"stress_ratio={ratio:.4f}", f"hedge_required={int(hedge_required)}"),
        )
