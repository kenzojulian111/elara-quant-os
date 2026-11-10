from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    available_quantity: float
    slippage_bps: float
    latency_ms: float
    healthy: bool = True


@dataclass(frozen=True)
class ExecutionRoute:
    venue: str
    quantity: float
    expected_cost_bps: float
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


class SmartExecutionRouter:
    def route(self, *, quantity: float, quotes: tuple[VenueQuote, ...]) -> ExecutionRoute:
        required = max(0.0, float(quantity))
        eligible = tuple(
            quote for quote in quotes if quote.healthy and float(quote.available_quantity) >= required and required > 0
        )
        if not eligible:
            return ExecutionRoute("", required, 0.0, False, ("no_healthy_liquid_venue",))
        selected = min(eligible, key=lambda quote: float(quote.slippage_bps) + float(quote.latency_ms) / 10.0)
        cost = float(selected.slippage_bps) + float(selected.latency_ms) / 10.0
        return ExecutionRoute(selected.venue, required, cost, True, (f"venue={selected.venue}",))
