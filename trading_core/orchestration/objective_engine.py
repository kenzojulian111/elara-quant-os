from __future__ import annotations

from dataclasses import dataclass

from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from utils.health import RuntimeModeAssessment


@dataclass(frozen=True)
class ObjectiveConfig:
    preserve_capital_drawdown_trigger: float = 8.0
    preserve_capital_confidence_floor: float = 0.80
    maximize_profit_confidence_floor: float = 0.55


@dataclass(frozen=True)
class ObjectiveProfile:
    objective: str
    requested_objective: str
    confidence_floor: float = 0.0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyObjectiveBias:
    strategy_id: str
    confidence_multiplier: float = 1.0
    block_new_entries: bool = False
    reasons: tuple[str, ...] = ()


class ObjectiveEngine:
    def __init__(self, config: ObjectiveConfig | None = None):
        self.config = config or ObjectiveConfig()

    def assess(
        self,
        *,
        preferred_objective: str,
        current_drawdown_pct: float,
        runtime_assessment: RuntimeModeAssessment,
        portfolio_plan: PortfolioDecisionPlan | None,
    ) -> ObjectiveProfile:
        requested = self._normalize(preferred_objective) or "adaptive"
        reasons = list(portfolio_plan.reasons if portfolio_plan else ())
        drawdown_pct = max(0.0, float(current_drawdown_pct))
        runtime_mode = self._normalize(getattr(runtime_assessment, "mode", ""))

        if requested == "adaptive":
            if drawdown_pct >= float(self.config.preserve_capital_drawdown_trigger):
                return ObjectiveProfile(
                    objective="preserve_capital",
                    requested_objective=requested,
                    confidence_floor=float(self.config.preserve_capital_confidence_floor),
                    reasons=self._combine_reasons(
                        reasons,
                        (f"objective_shift=preserve_capital_drawdown", f"current_drawdown_pct={drawdown_pct:.2f}"),
                    ),
                )
            if runtime_mode in {"safe", "degraded"}:
                return ObjectiveProfile(
                    objective="preserve_capital",
                    requested_objective=requested,
                    confidence_floor=float(self.config.preserve_capital_confidence_floor),
                    reasons=self._combine_reasons(reasons, (f"objective_shift=preserve_capital_runtime={runtime_mode}",)),
                )
            return ObjectiveProfile(
                objective="balanced",
                requested_objective=requested,
                confidence_floor=0.0,
                reasons=self._combine_reasons(reasons, ("objective_shift=balanced_default",)),
            )

        if requested == "preserve_capital":
            return ObjectiveProfile(
                objective="preserve_capital",
                requested_objective=requested,
                confidence_floor=float(self.config.preserve_capital_confidence_floor),
                reasons=self._combine_reasons(reasons, ("objective_explicit=preserve_capital",)),
            )

        if requested == "maximize_profit":
            return ObjectiveProfile(
                objective="maximize_profit",
                requested_objective=requested,
                confidence_floor=float(self.config.maximize_profit_confidence_floor),
                reasons=self._combine_reasons(reasons, ("objective_explicit=maximize_profit",)),
            )

        return ObjectiveProfile(
            objective="balanced",
            requested_objective=requested,
            confidence_floor=0.0,
            reasons=self._combine_reasons(reasons, ("objective_explicit=balanced",)),
        )

    def assess_strategy(
        self,
        *,
        strategy_profile,
        objective_profile: ObjectiveProfile,
        decision_action: str,
        decision_confidence: float,
        allocation_weight: float,
        market_regime: str,
    ) -> StrategyObjectiveBias:
        strategy_id = str(getattr(strategy_profile, "strategy_id", "") or "").strip()
        tags = {self._normalize(tag) for tag in ((getattr(strategy_profile, "tags", None) or set()))}
        action = self._normalize(decision_action)
        regime = self._normalize(market_regime)
        confidence = float(decision_confidence)
        objective = self._normalize(objective_profile.objective)

        reasons: list[str] = []
        multiplier = 1.0
        block = False

        if action not in {"enter_long", "enter_short"}:
            return StrategyObjectiveBias(
                strategy_id=strategy_id,
                confidence_multiplier=1.0,
                block_new_entries=False,
                reasons=("objective_non_entry_neutral",),
            )

        if objective == "preserve_capital":
            floor = float(objective_profile.confidence_floor or self.config.preserve_capital_confidence_floor)
            if confidence < floor:
                block = True
                reasons.extend(
                    (
                        f"objective_block=preserve_capital",
                        f"objective_preserve_capital_confidence_floor={floor:.2f}",
                    )
                )
            elif "trend" in tags:
                multiplier = min(multiplier, 0.85)
                reasons.append("objective_preserve_capital_deprioritize_trend")
        elif objective == "maximize_profit":
            if "mean_reversion" in tags and regime == "trend":
                multiplier = min(multiplier, 0.75)
                reasons.append("objective_maximize_profit_deprioritize_mean_reversion")
            elif "trend" in tags and regime == "trend":
                reasons.append("objective_maximize_profit_aligns_with_trend")
        else:
            reasons.append("objective_balanced_neutral")

        if not reasons:
            reasons.append("objective_clear")

        return StrategyObjectiveBias(
            strategy_id=strategy_id,
            confidence_multiplier=multiplier,
            block_new_entries=block,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _normalize(value) -> str:
        return str(value or "").strip().lower().replace(" ", "_")

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

