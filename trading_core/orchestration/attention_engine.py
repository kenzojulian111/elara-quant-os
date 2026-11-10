from __future__ import annotations

from dataclasses import dataclass, replace

from trading_core.data_contracts import MarketState, StrategyAllocation
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.strategy.base import PositionState
from utils.health import RuntimeModeAssessment


@dataclass(frozen=True)
class AttentionProfile:
    mode: str
    priority: str
    max_new_entry_strategies: int
    reasons: tuple[str, ...] = ()
    focused_strategy_ids: tuple[str, ...] = ()
    skipped_strategy_ids: tuple[str, ...] = ()
    forced_strategy_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttentionConfig:
    full_max_new_entry_strategies: int = 3
    simple_max_new_entry_strategies: int = 1


class AttentionEngine:
    def __init__(self, config: AttentionConfig | None = None):
        self.config = config or AttentionConfig()

    def assess(
        self,
        *,
        state: MarketState,
        runtime_assessment: RuntimeModeAssessment,
        portfolio_plan: PortfolioDecisionPlan | None,
    ) -> AttentionProfile:
        reasons = list(portfolio_plan.reasons if portfolio_plan else ())
        volatility = self._normalize(state.volatility)
        trend = self._normalize(state.trend)
        runtime_mode = self._normalize(runtime_assessment.mode)

        if runtime_mode == "safe":
            return AttentionProfile(
                mode="critical",
                priority="safety",
                max_new_entry_strategies=1,
                reasons=self._combine_reasons(reasons, runtime_assessment.reasons, ("attention_shift=safety",)),
            )
        if runtime_mode == "degraded":
            return AttentionProfile(
                mode="simple",
                priority="stability",
                max_new_entry_strategies=int(self.config.simple_max_new_entry_strategies),
                reasons=self._combine_reasons(reasons, runtime_assessment.reasons, ("attention_shift=degraded_runtime",)),
            )
        if trend in {"range", "sideways", "flat", "chop"} or volatility in {"low", "normal"}:
            return AttentionProfile(
                mode="simple",
                priority="signal_quality",
                max_new_entry_strategies=int(self.config.simple_max_new_entry_strategies),
                reasons=self._combine_reasons(reasons, ("attention_shift=low_signal_market",)),
            )
        if volatility in {"high", "extreme", "chaotic"}:
            return AttentionProfile(
                mode="simple",
                priority="noise_control",
                max_new_entry_strategies=int(self.config.simple_max_new_entry_strategies),
                reasons=self._combine_reasons(reasons, ("attention_shift=high_noise_market",)),
            )
        return AttentionProfile(
            mode="full",
            priority="opportunity",
            max_new_entry_strategies=max(1, int(self.config.full_max_new_entry_strategies)),
            reasons=self._combine_reasons(reasons, ("attention_shift=full_scan",)),
        )

    def focus_allocations(
        self,
        *,
        allocations: list[StrategyAllocation],
        positions: dict[str, PositionState],
        profile: AttentionProfile,
    ) -> tuple[AttentionProfile, list[StrategyAllocation]]:
        if not allocations:
            return profile, []

        forced_ids = tuple(
            allocation.strategy_id
            for allocation in allocations
            if getattr(positions.get(allocation.strategy_id), "direction", None)
        )
        forced = [allocation for allocation in allocations if allocation.strategy_id in set(forced_ids)]
        candidates = [allocation for allocation in allocations if allocation.strategy_id not in set(forced_ids)]
        ranked = sorted(candidates, key=lambda allocation: float(allocation.weight), reverse=True)

        limit = max(0, int(profile.max_new_entry_strategies))
        focused_new = ranked[:limit]
        skipped = ranked[limit:]
        focused = forced + focused_new
        focused_ids = tuple(allocation.strategy_id for allocation in focused)
        skipped_ids = tuple(allocation.strategy_id for allocation in skipped)
        updated = replace(
            profile,
            focused_strategy_ids=focused_ids,
            skipped_strategy_ids=skipped_ids,
            forced_strategy_ids=forced_ids,
        )
        return updated, focused

    @staticmethod
    def _normalize(value) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _combine_reasons(*groups) -> tuple[str, ...]:
        seen = set()
        out: list[str] = []
        for group in groups:
            for item in group or ():
                text = str(item or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                out.append(text)
        return tuple(out)

