from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from trading_core.data_contracts import DecisionEnvelope, GovernanceDecision, MarketState, StrategyProfile
from utils.system_policy import resolve_dependency_impact


@dataclass(frozen=True)
class GovernanceContext:
    operator_allows_trading: bool = True
    operator_reason: str = ""
    system_health_score: float = 100.0
    dependency_states: Dict[str, str] = field(default_factory=dict)
    runtime_mode: str = ""
    runtime_reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DecisionGovernorConfig:
    yellow_health_score: float = 75.0
    red_health_score: float = 50.0
    blocked_volatility: tuple[str, ...] = ("extreme", "chaotic", "halted")
    blocked_liquidity: tuple[str, ...] = ("thin", "illiquid", "dry")
    degraded_dependency_states: tuple[str, ...] = ("degraded", "slow", "stale", "partial")
    blocking_dependency_states: tuple[str, ...] = ("down", "failed", "unavailable", "offline")
    trend_regime_labels: tuple[str, ...] = ("trend", "bull", "bear", "breakout", "momentum")
    range_regime_labels: tuple[str, ...] = ("range", "sideways", "chop", "mean_revert", "flat")


class DecisionGovernor:
    def __init__(self, config: DecisionGovernorConfig | None = None):
        self.config = config or DecisionGovernorConfig()

    def assess(
        self,
        *,
        envelope: DecisionEnvelope,
        state: MarketState,
        strategy_profile: StrategyProfile | None,
        context: GovernanceContext | None = None,
    ) -> GovernanceDecision:
        context = context or GovernanceContext()
        runtime_mode = self._normalize_runtime_mode(context.runtime_mode)
        if self._is_exit_request(envelope):
            mode, reasons = self._derive_mode_and_reasons(state, strategy_profile, context)
            reasons = self._combine_reasons((*reasons, "exit_actions_allowed_in_any_mode"))
            return GovernanceDecision(
                approved=True,
                mode=mode,
                reason="exit_permitted",
                reasons=reasons,
                severity=self._severity_for_mode(mode),
                runtime_mode=runtime_mode or self._runtime_mode_for_governance(mode),
                runtime_reasons=self._combine_reasons(context.runtime_reasons),
            )

        mode, reasons = self._derive_mode_and_reasons(state, strategy_profile, context)
        entry_action = self._is_entry_request(envelope)
        if entry_action and mode == "red":
            return GovernanceDecision(
                approved=False,
                mode="red",
                reason="governor_red_mode_blocks_entries",
                reasons=self._combine_reasons((*reasons, "new_entries_disabled")),
                severity="critical",
                runtime_mode=runtime_mode or self._runtime_mode_for_governance("red"),
                runtime_reasons=self._combine_reasons(context.runtime_reasons),
            )
        if entry_action and mode == "yellow":
            return GovernanceDecision(
                approved=False,
                mode="yellow",
                reason="governor_yellow_mode_blocks_entries",
                reasons=self._combine_reasons((*reasons, "new_entries_disabled")),
                severity="warning",
                runtime_mode=runtime_mode or self._runtime_mode_for_governance("yellow"),
                runtime_reasons=self._combine_reasons(context.runtime_reasons),
            )
        return GovernanceDecision(
            approved=True,
            mode=mode,
            reason="governor_clear",
            reasons=self._combine_reasons((*reasons, "governor_clear")),
            severity=self._severity_for_mode(mode),
            runtime_mode=runtime_mode or self._runtime_mode_for_governance(mode),
            runtime_reasons=self._combine_reasons(context.runtime_reasons),
        )

    def _derive_mode_and_reasons(
        self,
        state: MarketState,
        strategy_profile: StrategyProfile | None,
        context: GovernanceContext,
    ) -> tuple[str, tuple[str, ...]]:
        reasons: list[str] = []
        mode = "green"

        if not bool(context.operator_allows_trading):
            return "red", self._combine_reasons(
                (
                    "operator_state_blocked",
                    str(context.operator_reason or "operator_not_ready"),
                )
            )

        runtime_mode = self._normalize_runtime_mode(context.runtime_mode)
        if runtime_mode:
            runtime_reasons = self._combine_reasons(context.runtime_reasons or (f"runtime_mode={runtime_mode}",))
            if runtime_mode == "safe":
                return "red", runtime_reasons
            if runtime_mode == "degraded":
                mode = self._max_mode(mode, "yellow")
                reasons.extend(runtime_reasons)
            elif runtime_mode == "full":
                reasons.extend(runtime_reasons)

        dependency_mode, dependency_reasons = self._dependency_mode(context.dependency_states)
        mode = self._max_mode(mode, dependency_mode)
        reasons.extend(dependency_reasons)

        health_score = float(context.system_health_score)
        if health_score <= float(self.config.red_health_score):
            mode = self._max_mode(mode, "red")
            reasons.append(f"system_health_below_red={health_score:.1f}")
        elif health_score <= float(self.config.yellow_health_score):
            mode = self._max_mode(mode, "yellow")
            reasons.append(f"system_health_below_yellow={health_score:.1f}")

        volatility = self._normalize_label(state.volatility)
        if volatility and volatility in {self._normalize_label(v) for v in self.config.blocked_volatility}:
            mode = self._max_mode(mode, "yellow")
            reasons.append(f"volatility_blocked={volatility}")

        liquidity = self._normalize_label(state.liquidity)
        if liquidity and liquidity in {self._normalize_label(v) for v in self.config.blocked_liquidity}:
            mode = self._max_mode(mode, "yellow")
            reasons.append(f"liquidity_blocked={liquidity}")

        regime_reason = self._regime_reason(strategy_profile, state)
        if regime_reason:
            mode = self._max_mode(mode, "yellow")
            reasons.append(regime_reason)

        if not reasons:
            reasons.append("operating_green")
        return mode, self._combine_reasons(reasons)

    def _dependency_mode(self, dependency_states: Dict[str, str]) -> tuple[str, tuple[str, ...]]:
        mode = "green"
        reasons: list[str] = []
        for name, raw_state in (dependency_states or {}).items():
            impact = resolve_dependency_impact(
                name,
                raw_state,
                blocking_states=self.config.blocking_dependency_states,
                degraded_states=self.config.degraded_dependency_states,
            )
            if impact.effect == "safe":
                mode = self._max_mode(mode, "red")
            elif impact.effect == "degraded":
                mode = self._max_mode(mode, "yellow")
            if impact.reason:
                reasons.append(impact.reason)
        return mode, self._combine_reasons(reasons)

    def _regime_reason(self, strategy_profile: StrategyProfile | None, state: MarketState) -> str:
        if strategy_profile is None:
            return ""
        tags = {self._normalize_label(tag) for tag in (strategy_profile.tags or set())}
        trend_label = self._normalize_label(state.trend)
        trend_regimes = {self._normalize_label(v) for v in self.config.trend_regime_labels}
        range_regimes = {self._normalize_label(v) for v in self.config.range_regime_labels}
        if not trend_label:
            return ""
        if "trend" in tags and trend_label in range_regimes:
            return f"strategy_regime_mismatch=trend_vs_{trend_label}"
        if "mean_reversion" in tags and trend_label in trend_regimes:
            return f"strategy_regime_mismatch=mean_reversion_vs_{trend_label}"
        return ""

    @staticmethod
    def _is_entry_request(envelope: DecisionEnvelope) -> bool:
        return envelope.decision in {"enter_long", "enter_short"}

    @staticmethod
    def _is_exit_request(envelope: DecisionEnvelope) -> bool:
        return envelope.decision == "exit" or envelope.status == "exit_requested"

    @staticmethod
    def _severity_for_mode(mode: str) -> str:
        return {"green": "info", "yellow": "warning", "red": "critical"}.get(mode, "info")

    @staticmethod
    def _normalize_label(value) -> str:
        return str(value or "").strip().lower().replace(" ", "_")

    @staticmethod
    def _combine_reasons(reasons) -> tuple[str, ...]:
        seen = set()
        out: list[str] = []
        for item in reasons or ():
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return tuple(out)

    @staticmethod
    def _max_mode(left: str, right: str) -> str:
        order = {"green": 0, "yellow": 1, "red": 2}
        return left if order.get(left, 0) >= order.get(right, 0) else right

    @staticmethod
    def _normalize_runtime_mode(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"full", "degraded", "safe"}:
            return normalized
        return ""

    @staticmethod
    def _runtime_mode_for_governance(mode: str) -> str:
        return {"green": "full", "yellow": "degraded", "red": "safe"}.get(mode, "")

