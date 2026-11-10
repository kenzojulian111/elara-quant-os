from __future__ import annotations

from dataclasses import replace

from trading_core.data_contracts import (
    DecisionEnvelope,
    DecisionTraceStep,
    GovernanceDecision,
    ReasonedDecision,
    ReasoningComponent,
)
from trading_core.orchestration.objective_engine import ObjectiveProfile, StrategyObjectiveBias
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.temporal_reasoner import StrategyTemporalBias, TemporalContext
from trading_core.intelligence.cognitive_modes import CognitiveModeEngine
from trading_core.intelligence.probabilistic import ProbabilisticDecisionEngine
from utils.health import RuntimeModeAssessment


class DecisionReasoner:
    _WEIGHTS = {
        "strategy": 0.45,
        "portfolio": 0.15,
        "objective": 0.10,
        "temporal": 0.10,
        "risk": 0.20,
        "runtime": 0.10,
        "governance": 0.10,
    }

    def synthesize(
        self,
        envelope: DecisionEnvelope,
        *,
        portfolio_plan: PortfolioDecisionPlan | None = None,
        objective_profile: ObjectiveProfile | None = None,
        objective_bias: StrategyObjectiveBias | None = None,
        temporal_context: TemporalContext | None = None,
        temporal_bias: StrategyTemporalBias | None = None,
        runtime_assessment: RuntimeModeAssessment | None = None,
        governance: GovernanceDecision | None = None,
    ) -> DecisionEnvelope:
        components = list(
            self._build_components(
                envelope,
                portfolio_plan,
                objective_profile,
                objective_bias,
                temporal_context,
                temporal_bias,
                runtime_assessment,
                governance,
            )
        )
        constraints = self._derive_constraints(
            envelope,
            governance,
            portfolio_plan=portfolio_plan,
            objective_profile=objective_profile,
            objective_bias=objective_bias,
            temporal_context=temporal_context,
            temporal_bias=temporal_bias,
            runtime_assessment=runtime_assessment,
        )
        final_action = self._effective_action(
            envelope,
            governance,
            portfolio_plan=portfolio_plan,
            objective_bias=objective_bias,
            temporal_bias=temporal_bias,
            runtime_assessment=runtime_assessment,
        )
        approved = final_action != "hold"
        confidence = self._weighted_confidence(components)
        reasons = self._combine_reasons(
            envelope.reasons,
            portfolio_plan.reasons if portfolio_plan else (),
            objective_profile.reasons if objective_profile else (),
            objective_bias.reasons if objective_bias else (),
            temporal_context.reasons if temporal_context else (),
            temporal_bias.reasons if temporal_bias else (),
            governance.reasons if governance else (),
            runtime_assessment.reasons if runtime_assessment else (),
        )
        regime = str(getattr(portfolio_plan, "market_regime", "") or "")
        risk_score = self._risk_score(envelope)
        summary = self._build_summary(final_action, approved, regime, confidence)
        explanation = self._build_explanation(final_action, reasons, constraints)
        component_scores = self._build_component_scores(components, governance)
        cognitive_mode = ""
        if runtime_assessment is not None:
            cognitive_mode = CognitiveModeEngine.from_runtime_assessment(
                runtime_assessment,
                current_drawdown_pct=float(envelope.current_drawdown_pct),
            ).mode
        base_reasoned_decision = ReasonedDecision(
            action=final_action,
            approved=approved,
            confidence=confidence,
            summary=summary,
            explanation=explanation,
            reasons=reasons,
            constraints=constraints,
            regime=regime,
            risk_score=risk_score,
            cognitive_mode=cognitive_mode,
            component_scores=component_scores,
            components=tuple(components),
        )
        probability = ProbabilisticDecisionEngine().assess(
            replace(envelope, reasoned_decision=base_reasoned_decision)
        )
        reasoned_decision = replace(
            base_reasoned_decision,
            probability=probability.probability,
            uncertainty=probability.uncertainty,
            expected_utility=probability.expected_utility,
            component_scores={
                **component_scores,
                "probability": round(float(probability.probability), 4),
                "uncertainty": round(float(probability.uncertainty), 4),
                "expected_utility": round(float(probability.expected_utility), 4),
                "propagated_confidence": round(float(probability.propagated_confidence), 4),
                "raw_uncertainty": round(float(probability.raw_uncertainty), 4),
                "probability_model": probability.probability_model,
                "bayesian_posterior": probability.posterior.to_dict() if probability.posterior else {},
                "confidence_propagation": probability.confidence_propagation.to_dict()
                if probability.confidence_propagation
                else {},
                "uncertainty_calibration": probability.uncertainty_calibration.to_dict()
                if probability.uncertainty_calibration
                else {},
            },
        )
        return replace(
            envelope,
            pipeline_trace=self._append_reasoning_trace(envelope.pipeline_trace, reasoned_decision),
            reasoned_decision=reasoned_decision,
        )

    def _build_components(
        self,
        envelope: DecisionEnvelope,
        portfolio_plan: PortfolioDecisionPlan | None,
        objective_profile: ObjectiveProfile | None,
        objective_bias: StrategyObjectiveBias | None,
        temporal_context: TemporalContext | None,
        temporal_bias: StrategyTemporalBias | None,
        runtime_assessment: RuntimeModeAssessment | None,
        governance: GovernanceDecision | None,
    ) -> tuple[ReasoningComponent, ...]:
        strategy_reasons: tuple[str, ...] = ()
        if envelope.pipeline_trace:
            strategy_reasons = envelope.pipeline_trace[0].reasons
        components: list[ReasoningComponent] = [
            ReasoningComponent(
                source="strategy",
                label=envelope.decision,
                confidence=self._clamp01(envelope.confidence),
                weight=self._WEIGHTS["strategy"],
                verdict="support" if envelope.decision in {"enter_long", "enter_short", "exit"} else "neutral",
                reasons=self._combine_reasons(strategy_reasons, envelope.reasons),
                details={"requested_action": envelope.decision, "status": envelope.status},
            )
        ]

        if portfolio_plan is not None:
            selected = set(portfolio_plan.selected_ids or ())
            blocked = set(portfolio_plan.blocked_ids or ())
            suppressed = set(portfolio_plan.suppressed_ids or ())
            if envelope.strategy_id in blocked:
                confidence = 0.0
                verdict = "block"
            elif envelope.strategy_id in suppressed:
                confidence = 0.35
                verdict = "degrade"
            elif envelope.strategy_id in selected:
                confidence = 0.85
                verdict = "support"
            else:
                confidence = self._clamp01(envelope.allocation_weight)
                verdict = "neutral"
            components.append(
                ReasoningComponent(
                    source="portfolio",
                    label=portfolio_plan.market_regime,
                    confidence=confidence,
                    weight=self._WEIGHTS["portfolio"],
                    verdict=verdict,
                    reasons=self._combine_reasons(portfolio_plan.reasons),
                    details={
                        "selected_ids": portfolio_plan.selected_ids,
                        "blocked_ids": portfolio_plan.blocked_ids,
                        "suppressed_ids": portfolio_plan.suppressed_ids,
                    },
                )
            )

        if objective_profile is not None or objective_bias is not None:
            objective_label = str(getattr(objective_profile, "objective", "") or "objective")
            objective_confidence = 0.9
            objective_verdict = "support"
            if objective_bias is not None:
                objective_confidence = self._clamp01(float(objective_bias.confidence_multiplier))
                if objective_bias.block_new_entries:
                    objective_confidence = 0.1
                    objective_verdict = "block"
                elif float(objective_bias.confidence_multiplier) < 1.0:
                    objective_confidence = min(objective_confidence, 0.45)
                    objective_verdict = "degrade"
            components.append(
                ReasoningComponent(
                    source="objective",
                    label=objective_label,
                    confidence=objective_confidence,
                    weight=self._WEIGHTS["objective"],
                    verdict=objective_verdict,
                    reasons=self._combine_reasons(
                        objective_profile.reasons if objective_profile else (),
                        objective_bias.reasons if objective_bias else (),
                    ),
                    details={
                        "requested_objective": getattr(objective_profile, "requested_objective", ""),
                        "confidence_floor": getattr(objective_profile, "confidence_floor", 0.0),
                        "block_new_entries": getattr(objective_bias, "block_new_entries", False),
                    },
                )
            )

        if temporal_context is not None or temporal_bias is not None:
            temporal_label = ""
            if temporal_context is not None:
                temporal_label = str(temporal_context.dominant_regime or temporal_context.current_regime)
            if not temporal_label and temporal_bias is not None:
                temporal_label = f"loss_streak={int(temporal_bias.loss_streak)}"
            temporal_confidence = 0.85
            temporal_verdict = "support"
            if temporal_bias is not None:
                temporal_confidence = self._clamp01(float(temporal_bias.confidence_multiplier))
                if temporal_bias.block_new_entries:
                    temporal_confidence = 0.1
                    temporal_verdict = "block"
                elif float(temporal_bias.confidence_multiplier) < 1.0:
                    temporal_confidence = min(temporal_confidence, 0.45)
                    temporal_verdict = "degrade"
            components.append(
                ReasoningComponent(
                    source="temporal",
                    label=temporal_label or "temporal",
                    confidence=temporal_confidence,
                    weight=self._WEIGHTS["temporal"],
                    verdict=temporal_verdict,
                    reasons=self._combine_reasons(
                        temporal_context.reasons if temporal_context else (),
                        temporal_bias.reasons if temporal_bias else (),
                    ),
                    details={
                        "current_regime": getattr(temporal_context, "current_regime", ""),
                        "dominant_regime": getattr(temporal_context, "dominant_regime", ""),
                        "regime_streak": getattr(temporal_context, "regime_streak", 0),
                        "regime_persistence": getattr(temporal_context, "regime_persistence", 0.0),
                        "loss_streak": getattr(temporal_bias, "loss_streak", 0),
                        "recent_outcomes": getattr(temporal_bias, "recent_outcomes", ()),
                    },
                )
            )

        if envelope.risk is not None:
            risk = envelope.risk
            risk_confidence = 0.85 if risk.approved else 0.1
            if risk.reason == "size_capped_to_limit":
                risk_confidence = 0.7
            components.append(
                ReasoningComponent(
                    source="risk",
                    label=risk.reason,
                    confidence=risk_confidence,
                    weight=self._WEIGHTS["risk"],
                    verdict="support" if risk.approved else "block",
                    reasons=self._combine_reasons(risk.reasons or (risk.reason,)),
                    details={"approved": risk.approved, "size_pct": float(risk.size_pct)},
                )
            )

        if runtime_assessment is not None:
            runtime_confidence = {
                "full": 1.0,
                "degraded": 0.55,
                "safe": 0.1,
            }.get(str(runtime_assessment.mode or ""), 0.4)
            components.append(
                ReasoningComponent(
                    source="runtime",
                    label=runtime_assessment.mode,
                    confidence=runtime_confidence,
                    weight=self._WEIGHTS["runtime"],
                    verdict="support" if runtime_assessment.allow_new_entries else "block",
                    reasons=self._combine_reasons(runtime_assessment.reasons),
                    details={"allow_new_entries": runtime_assessment.allow_new_entries},
                )
            )

        if governance is not None:
            governance_confidence = {
                "green": 1.0,
                "yellow": 0.35,
                "red": 0.05,
            }.get(str(governance.mode or ""), 0.4)
            if governance.approved and governance.mode == "yellow":
                governance_confidence = 0.55
            components.append(
                ReasoningComponent(
                    source="governance",
                    label=governance.mode,
                    confidence=governance_confidence,
                    weight=self._WEIGHTS["governance"],
                    verdict="support" if governance.approved else "block",
                    reasons=self._combine_reasons(governance.reasons or (governance.reason,)),
                    details={"approved": governance.approved, "runtime_mode": governance.runtime_mode},
                )
            )

        return tuple(components)

    @classmethod
    def _effective_action(
        cls,
        envelope: DecisionEnvelope,
        governance: GovernanceDecision | None,
        *,
        portfolio_plan: PortfolioDecisionPlan | None = None,
        objective_bias: StrategyObjectiveBias | None = None,
        temporal_bias: StrategyTemporalBias | None = None,
        runtime_assessment: RuntimeModeAssessment | None = None,
    ) -> str:
        if envelope.status == "exit_requested" and (governance is None or governance.approved):
            return "exit"
        if cls._is_entry_action(envelope) and cls._portfolio_blocks_entry(envelope.strategy_id, portfolio_plan):
            return "hold"
        if cls._is_entry_action(envelope) and cls._objective_blocks_entry(objective_bias):
            return "hold"
        if cls._is_entry_action(envelope) and cls._temporal_blocks_entry(temporal_bias):
            return "hold"
        if cls._is_entry_action(envelope) and runtime_assessment is not None and not runtime_assessment.allow_new_entries:
            return "hold"
        if governance is not None and not governance.approved and envelope.decision in {"enter_long", "enter_short"}:
            return "hold"
        # A plan is deliberately absent while the governed pipeline is still
        # deciding. Approval depends on the decision state and gates, not on a
        # prematurely-created execution object.
        if (
            envelope.status == "ready"
            and envelope.risk is not None
            and envelope.risk.approved
            and (governance is None or governance.approved)
        ):
            return envelope.decision
        return "hold"

    @staticmethod
    def _weighted_confidence(components: list[ReasoningComponent]) -> float:
        total_weight = sum(max(0.0, float(component.weight)) for component in components)
        if total_weight <= 0:
            return 0.0
        score = sum(float(component.confidence) * float(component.weight) for component in components)
        return max(0.0, min(1.0, score / total_weight))

    @staticmethod
    def _risk_score(envelope: DecisionEnvelope) -> float:
        risk = envelope.risk
        if risk is None:
            return 0.5
        if not risk.approved:
            return 1.0
        if risk.reason == "size_capped_to_limit":
            return 0.35
        return 0.2

    @staticmethod
    def _build_summary(action: str, approved: bool, regime: str, confidence: float) -> str:
        regime_text = f" under {regime} regime" if regime else ""
        verb = "approved" if approved else "blocked"
        return f"{action} {verb}{regime_text} at confidence {confidence:.2f}"

    @classmethod
    def _build_explanation(cls, action: str, reasons: tuple[str, ...], constraints: tuple[str, ...]) -> str:
        reason_text = ", ".join(reasons[:4]) if reasons else "no strong supporting reasons"
        if constraints:
            return f"{action} because {reason_text}; constraints: {', '.join(constraints[:4])}"
        return f"{action} because {reason_text}"

    @classmethod
    def _derive_constraints(
        cls,
        envelope: DecisionEnvelope,
        governance: GovernanceDecision | None,
        *,
        portfolio_plan: PortfolioDecisionPlan | None = None,
        objective_profile: ObjectiveProfile | None = None,
        objective_bias: StrategyObjectiveBias | None = None,
        temporal_context: TemporalContext | None = None,
        temporal_bias: StrategyTemporalBias | None = None,
        runtime_assessment: RuntimeModeAssessment | None = None,
    ) -> tuple[str, ...]:
        blocked_markers = (
            "block",
            "blocked",
            "limit",
            "disabled",
            "mismatch",
            "invalid",
            "below",
        )
        items = list(envelope.reasons or ())
        if governance is not None:
            items.extend(governance.reasons or (governance.reason,))
        if objective_profile is not None:
            items.extend(objective_profile.reasons)
        if objective_bias is not None:
            items.extend(objective_bias.reasons)
        if temporal_context is not None:
            items.extend(temporal_context.reasons)
        if temporal_bias is not None:
            items.extend(temporal_bias.reasons)
        if runtime_assessment is not None and not runtime_assessment.allow_new_entries:
            items.extend(runtime_assessment.reasons)
        constraints = [
            text
            for text in (str(item or "").strip() for item in items)
            if text and any(marker in text for marker in blocked_markers) and "within_limits" not in text
        ]
        if objective_bias is not None and objective_bias.block_new_entries:
            constraints.extend(objective_bias.reasons)
        constraints.extend(cls._portfolio_constraints(envelope.strategy_id, portfolio_plan))
        return cls._combine_reasons(constraints)

    @staticmethod
    def _portfolio_constraints(strategy_id: str, portfolio_plan: PortfolioDecisionPlan | None) -> tuple[str, ...]:
        if portfolio_plan is None:
            return ()
        selected = set(portfolio_plan.selected_ids or ())
        blocked = set(portfolio_plan.blocked_ids or ())
        suppressed = set(portfolio_plan.suppressed_ids or ())
        constraints: list[str] = []
        if strategy_id in blocked:
            constraints.append("portfolio_strategy_blocked")
        if strategy_id in suppressed:
            constraints.append("portfolio_strategy_suppressed")
        if selected and strategy_id not in selected:
            constraints.append("portfolio_strategy_not_selected")
        return tuple(constraints)

    @staticmethod
    def _portfolio_blocks_entry(strategy_id: str, portfolio_plan: PortfolioDecisionPlan | None) -> bool:
        return bool(DecisionReasoner._portfolio_constraints(strategy_id, portfolio_plan))

    @staticmethod
    def _temporal_blocks_entry(temporal_bias: StrategyTemporalBias | None) -> bool:
        return bool(temporal_bias and temporal_bias.block_new_entries)

    @staticmethod
    def _objective_blocks_entry(objective_bias: StrategyObjectiveBias | None) -> bool:
        return bool(objective_bias and objective_bias.block_new_entries)

    @staticmethod
    def _is_entry_action(envelope: DecisionEnvelope) -> bool:
        return envelope.decision in {"enter_long", "enter_short"}

    @classmethod
    def _build_component_scores(cls, components: list[ReasoningComponent], governance: GovernanceDecision | None) -> dict[str, object]:
        scores: dict[str, object] = {}
        for component in components:
            scores[f"{component.source}_confidence"] = round(float(component.confidence), 4)
            scores[f"{component.source}_verdict"] = component.verdict
        if governance is not None:
            scores["governance_mode"] = governance.mode
            scores["runtime_mode"] = governance.runtime_mode
        return scores

    @staticmethod
    def _append_reasoning_trace(
        pipeline_trace: tuple[DecisionTraceStep, ...],
        reasoned_decision: ReasonedDecision,
    ) -> tuple[DecisionTraceStep, ...]:
        prior_steps = tuple(step for step in pipeline_trace if step.step != "reasoning")
        status = "approved" if reasoned_decision.approved else "blocked"
        reasons = reasoned_decision.reasons or reasoned_decision.constraints or (reasoned_decision.summary,)
        return prior_steps + (
            DecisionTraceStep(
                step="reasoning",
                status=status,
                reasons=reasons,
                details={
                    "action": reasoned_decision.action,
                    "probability": round(float(reasoned_decision.probability), 4),
                    "uncertainty": round(float(reasoned_decision.uncertainty), 4),
                    "expected_utility": round(float(reasoned_decision.expected_utility), 4),
                    "cognitive_mode": reasoned_decision.cognitive_mode,
                },
            ),
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
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

