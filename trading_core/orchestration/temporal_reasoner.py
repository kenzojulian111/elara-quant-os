from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

from trading_core.data_contracts import MarketState, StrategyProfile
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan


@dataclass(frozen=True)
class TemporalConfig:
    regime_window: int = 6
    persistent_regime_streak: int = 3
    persistent_regime_ratio: float = 0.67
    loss_memory: int = 5
    loss_streak_penalty_threshold: int = 2
    loss_streak_block_threshold: int = 3
    loss_streak_penalty_multiplier: float = 0.75


@dataclass(frozen=True)
class StrategyTemporalBias:
    strategy_id: str
    confidence_multiplier: float = 1.0
    block_new_entries: bool = False
    loss_streak: int = 0
    recent_outcomes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemporalContext:
    symbol: str
    current_regime: str
    dominant_regime: str
    regime_streak: int
    regime_persistence: float
    recent_regimes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    strategy_biases: dict[str, StrategyTemporalBias] = field(default_factory=dict)


class TemporalReasoner:
    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or TemporalConfig()
        self._regime_history: dict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=max(1, int(self.config.regime_window)))
        )
        self._outcome_history: dict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=max(1, int(self.config.loss_memory)))
        )

    def observe_state(
        self,
        state: MarketState,
        *,
        portfolio_plan: PortfolioDecisionPlan | None = None,
    ) -> TemporalContext:
        regime = self._resolve_regime(state, portfolio_plan)
        history = self._regime_history[state.symbol]
        history.append(regime)
        recent_regimes = tuple(history)
        dominant_regime = self._dominant_regime(recent_regimes)
        regime_streak = self._regime_streak(recent_regimes)
        regime_persistence = self._regime_persistence(recent_regimes, dominant_regime)
        reasons = self._combine_reasons(
            portfolio_plan.reasons if portfolio_plan else (),
            (
                f"temporal_dominant_regime={dominant_regime}",
                f"temporal_regime_streak={regime}:{regime_streak}",
                f"temporal_regime_persistence={dominant_regime}:{regime_persistence:.2f}",
            ),
        )
        return TemporalContext(
            symbol=state.symbol,
            current_regime=regime,
            dominant_regime=dominant_regime,
            regime_streak=regime_streak,
            regime_persistence=regime_persistence,
            recent_regimes=recent_regimes,
            reasons=reasons,
        )

    def record_trade_outcome(self, strategy_id: str, *, pnl_pct: float, reason: str = "") -> None:
        normalized_id = str(strategy_id or "").strip()
        if not normalized_id:
            return
        outcome = "flat"
        if float(pnl_pct) > 0:
            outcome = "win"
        elif float(pnl_pct) < 0:
            outcome = "loss"
        history = self._outcome_history[normalized_id]
        history.append(outcome)

    def assess_strategy(
        self,
        strategy_id: str,
        strategy_profile: StrategyProfile | None,
        context: TemporalContext,
    ) -> StrategyTemporalBias:
        normalized_id = str(strategy_id or "").strip()
        tags = {self._normalize(tag) for tag in ((strategy_profile.tags or set()) if strategy_profile else set())}
        outcomes = tuple(self._outcome_history.get(normalized_id, ()))
        loss_streak = self._loss_streak(outcomes)
        multiplier = 1.0
        block_new_entries = False
        reasons: list[str] = []

        if self._is_persistent_range(context) and "trend" in tags:
            block_new_entries = True
            reasons.append("temporal_regime_block=trend_in_persistent_range")
        if self._is_persistent_trend(context) and "mean_reversion" in tags:
            block_new_entries = True
            reasons.append("temporal_regime_block=mean_reversion_in_persistent_trend")

        if loss_streak >= int(self.config.loss_streak_block_threshold):
            block_new_entries = True
            reasons.append(f"temporal_loss_streak_block={loss_streak}")
        elif loss_streak >= int(self.config.loss_streak_penalty_threshold):
            multiplier = min(multiplier, float(self.config.loss_streak_penalty_multiplier))
            reasons.append(f"temporal_loss_streak_penalty={loss_streak}")

        if not reasons:
            reasons.append("temporal_context_clear")

        return StrategyTemporalBias(
            strategy_id=normalized_id,
            confidence_multiplier=multiplier,
            block_new_entries=block_new_entries,
            loss_streak=loss_streak,
            recent_outcomes=outcomes,
            reasons=tuple(reasons),
        )

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

    @staticmethod
    def _normalize(value) -> str:
        return str(value or "").strip().lower().replace(" ", "_")

    def _resolve_regime(self, state: MarketState, portfolio_plan: PortfolioDecisionPlan | None) -> str:
        plan_regime = self._normalize(getattr(portfolio_plan, "market_regime", ""))
        if plan_regime:
            return plan_regime
        trend = self._normalize(state.trend)
        volatility = self._normalize(state.volatility)
        if trend in {"trend", "bullish", "bearish", "breakout", "momentum"}:
            return "trend"
        if trend in {"range", "sideways", "flat", "chop"}:
            return "range"
        if volatility in {"high", "extreme", "chaotic"}:
            return "high_volatility"
        return "mixed"

    @staticmethod
    def _dominant_regime(recent_regimes: tuple[str, ...]) -> str:
        if not recent_regimes:
            return "mixed"
        counts: dict[str, int] = {}
        for regime in recent_regimes:
            counts[regime] = counts.get(regime, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0]

    @staticmethod
    def _regime_streak(recent_regimes: tuple[str, ...]) -> int:
        if not recent_regimes:
            return 0
        target = recent_regimes[-1]
        streak = 0
        for regime in reversed(recent_regimes):
            if regime != target:
                break
            streak += 1
        return streak

    @staticmethod
    def _regime_persistence(recent_regimes: tuple[str, ...], dominant_regime: str) -> float:
        if not recent_regimes:
            return 0.0
        matches = sum(1 for regime in recent_regimes if regime == dominant_regime)
        return float(matches) / float(len(recent_regimes))

    @staticmethod
    def _loss_streak(outcomes: tuple[str, ...]) -> int:
        streak = 0
        for outcome in reversed(outcomes):
            if outcome != "loss":
                break
            streak += 1
        return streak

    def _is_persistent_range(self, context: TemporalContext) -> bool:
        return (
            context.dominant_regime == "range"
            and int(context.regime_streak) >= int(self.config.persistent_regime_streak)
            and float(context.regime_persistence) >= float(self.config.persistent_regime_ratio)
        )

    def _is_persistent_trend(self, context: TemporalContext) -> bool:
        return (
            context.dominant_regime == "trend"
            and int(context.regime_streak) >= int(self.config.persistent_regime_streak)
            and float(context.regime_persistence) >= float(self.config.persistent_regime_ratio)
        )

