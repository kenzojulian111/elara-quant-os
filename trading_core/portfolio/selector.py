from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from trading_core.data_contracts import MarketState, StrategyProfile
from trading_core.portfolio.performance_tracker import StrategyHealth


@dataclass(frozen=True)
class SelectionDecision:
    market_regime: str
    selected_ids: tuple[str, ...]
    blocked_ids: tuple[str, ...] = ()
    suppressed_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class StrategySelector:
    def select(self, strategies: Iterable[StrategyProfile], market_state: MarketState) -> List[str]:
        return list(self.decide(strategies, market_state).selected_ids)

    def decide(
        self,
        strategies: Iterable[StrategyProfile],
        market_state: MarketState,
        reviews: dict[str, StrategyHealth] | None = None,
    ) -> SelectionDecision:
        strategies = list(strategies)
        if not strategies:
            return SelectionDecision(market_regime="empty", selected_ids=(), reasons=("no_strategies_registered",))

        market_regime = self._classify_regime(market_state)
        blocked_ids = tuple(
            profile.strategy_id
            for profile in strategies
            if str((reviews or {}).get(profile.strategy_id, StrategyHealth(profile.strategy_id, "", 1.0)).status) == "blocked"
        )
        eligible = [profile for profile in strategies if profile.strategy_id not in set(blocked_ids)]
        if not eligible:
            return SelectionDecision(
                market_regime=market_regime,
                selected_ids=(),
                blocked_ids=blocked_ids,
                reasons=("all_strategies_blocked_by_self_review", f"market_regime={market_regime}"),
            )

        preferred = self._preferred_for_regime(eligible, market_regime)
        if preferred:
            selected = tuple(profile.strategy_id for profile in preferred)
            suppressed = tuple(profile.strategy_id for profile in eligible if profile.strategy_id not in set(selected))
            return SelectionDecision(
                market_regime=market_regime,
                selected_ids=selected,
                blocked_ids=blocked_ids,
                suppressed_ids=suppressed,
                reasons=(f"market_regime={market_regime}", "meta_decision_selected_regime_matched_strategies"),
            )

        selected = tuple(profile.strategy_id for profile in eligible)
        return SelectionDecision(
            market_regime=market_regime,
            selected_ids=selected,
            blocked_ids=blocked_ids,
            reasons=(f"market_regime={market_regime}", "fallback_selected_all_eligible_strategies"),
        )

    @staticmethod
    def _classify_regime(market_state: MarketState) -> str:
        trend = str(market_state.trend or "").strip().lower()
        volatility = str(market_state.volatility or "").strip().lower()

        if trend in {"bullish", "bearish", "trend", "breakout", "momentum"}:
            return "trend"
        if trend in {"range", "sideways", "chop", "flat"}:
            return "range"
        if volatility in {"low", "normal", "medium"}:
            return "range"
        if volatility in {"high", "extreme", "chaotic"}:
            return "high_volatility"
        return "mixed"

    @staticmethod
    def _preferred_for_regime(strategies: list[StrategyProfile], market_regime: str) -> list[StrategyProfile]:
        if market_regime == "trend":
            return [profile for profile in strategies if "trend" in profile.tags]
        if market_regime == "range":
            return [profile for profile in strategies if "mean_reversion" in profile.tags]
        return []

