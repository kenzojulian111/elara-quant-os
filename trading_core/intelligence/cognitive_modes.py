from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CognitiveMode:
    mode: str
    confidence_multiplier: float
    max_reasoning_depth: str
    allow_new_entries: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CognitiveModeConfig:
    defensive_drawdown_pct: float = 5.0
    crisis_drawdown_pct: float = 12.0
    opportunistic_health_score: float = 90.0


class CognitiveModeEngine:
    def __init__(self, config: CognitiveModeConfig | None = None):
        self.config = config or CognitiveModeConfig()

    def assess(
        self,
        *,
        runtime_mode: str,
        system_health_score: float,
        current_drawdown_pct: float,
        market_volatility: str = "",
        recovery: bool = False,
    ) -> CognitiveMode:
        runtime = str(runtime_mode or "").strip().lower()
        volatility = str(market_volatility or "").strip().lower()
        drawdown = max(0.0, float(current_drawdown_pct or 0.0))
        health = float(system_health_score or 0.0)

        if runtime == "safe" or drawdown >= float(self.config.crisis_drawdown_pct):
            return CognitiveMode(
                mode="crisis",
                confidence_multiplier=0.25,
                max_reasoning_depth="minimal",
                allow_new_entries=False,
                reasons=("cognitive_mode=crisis", f"runtime_mode={runtime}", f"drawdown_pct={drawdown:.2f}"),
            )
        if recovery or runtime == "recovery":
            return CognitiveMode(
                mode="recovery",
                confidence_multiplier=0.55,
                max_reasoning_depth="low",
                allow_new_entries=False,
                reasons=("cognitive_mode=recovery", f"runtime_mode={runtime}"),
            )
        if runtime == "degraded" or drawdown >= float(self.config.defensive_drawdown_pct) or volatility in {"high", "extreme", "chaotic"}:
            return CognitiveMode(
                mode="defensive",
                confidence_multiplier=0.7,
                max_reasoning_depth="medium",
                allow_new_entries=runtime != "degraded",
                reasons=("cognitive_mode=defensive", f"runtime_mode={runtime}", f"volatility={volatility}", f"drawdown_pct={drawdown:.2f}"),
            )
        if health >= float(self.config.opportunistic_health_score) and volatility in {"normal", "low"}:
            return CognitiveMode(
                mode="opportunistic",
                confidence_multiplier=1.05,
                max_reasoning_depth="high",
                allow_new_entries=True,
                reasons=("cognitive_mode=opportunistic", f"health_score={health:.1f}", f"volatility={volatility}"),
            )
        return CognitiveMode(
            mode="balanced",
            confidence_multiplier=1.0,
            max_reasoning_depth="medium",
            allow_new_entries=True,
            reasons=("cognitive_mode=balanced",),
        )

    @staticmethod
    def from_runtime_assessment(runtime_assessment: Any, *, current_drawdown_pct: float, market_volatility: str = "", system_health_score: float = 100.0) -> CognitiveMode:
        return CognitiveModeEngine().assess(
            runtime_mode=str(getattr(runtime_assessment, "mode", "") or ""),
            system_health_score=system_health_score,
            current_drawdown_pct=current_drawdown_pct,
            market_volatility=market_volatility,
        )
