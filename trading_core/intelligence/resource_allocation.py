from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceBudget:
    reasoning_depth: str
    max_parallel_tasks: int
    market_scan_interval_sec: float
    enable_expensive_models: bool
    reasons: tuple[str, ...]


class AdaptiveResourceAllocator:
    def allocate(self, *, runtime_mode: str, market_volatility: str, queue_depth: int = 0) -> ResourceBudget:
        runtime = str(runtime_mode or "").lower()
        volatility = str(market_volatility or "").lower()
        if runtime == "safe":
            return ResourceBudget("minimal", 1, 30.0, False, ("runtime_safe_minimal_compute",))
        if queue_depth >= 1000:
            return ResourceBudget("low", 2, 15.0, False, ("queue_backpressure_reduce_compute",))
        if volatility in {"high", "extreme", "chaotic"}:
            return ResourceBudget("high", 4, 3.0, True, ("volatile_market_increase_attention",))
        if runtime == "degraded":
            return ResourceBudget("medium", 2, 10.0, False, ("runtime_degraded_conserve_compute",))
        return ResourceBudget("medium", 3, 5.0, False, ("balanced_compute",))
