from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

from trading_core.coordination.action_context import ActionContext, DecisionRecord
from trading_core.data_contracts import (
    DecisionTraceStep,
    DecisionEnvelope,
    ExecutionPlan,
    IndicatorSnapshot,
    MarketState,
    RiskDecision,
    StrategyDecision,
    TradeCandidate,
)
from trading_core.orchestration.live_pipeline import LiveTradingPipeline
from trading_core.orchestration.decision_reasoner import DecisionReasoner
from trading_core.strategy.base import PositionState, Strategy


@dataclass(frozen=True)
class DecisionCoreConfig:
    risk_per_trade_pct: float = 0.01
    min_entry_confidence: float = 0.5


class DecisionCore:
    def __init__(self, live_pipeline: LiveTradingPipeline, config: DecisionCoreConfig | None = None):
        self.live_pipeline = live_pipeline
        self.config = config or DecisionCoreConfig()
        self.reasoner = DecisionReasoner()

    def evaluate(
        self,
        *,
        context: ActionContext,
        state: MarketState,
        strategy_id: str,
        strategy: Strategy,
        indicators: IndicatorSnapshot,
        position: PositionState,
        allocation_weight: float,
        current_drawdown_pct: float,
    ) -> DecisionEnvelope:
        eval_started_at = perf_counter()
        trace_id = self._build_trace_id(context, strategy_id, state.symbol)
        strategy_started_at = perf_counter()
        decision = strategy.decide(state, indicators, position)
        decision_reasons = self._combine_reasons(decision.reasons)
        pipeline_trace = [
            self._trace_step(
                "signal",
                "observed",
                decision_reasons or ("strategy_emitted_signal",),
                perf_counter() - strategy_started_at,
                {
                    "action": decision.action,
                    "confidence": float(decision.confidence),
                },
            )
        ]
        if decision.action == "exit":
            return self.reasoner.synthesize(DecisionEnvelope(
                trace_id=trace_id,
                strategy_id=strategy_id,
                symbol=state.symbol,
                status="exit_requested",
                pipeline_trace=tuple(
                    pipeline_trace
                    + [
                        self._trace_step(
                            "decision",
                            "exit_requested",
                            decision_reasons or ("strategy_requested_exit",),
                            perf_counter() - eval_started_at,
                        )
                    ]
                ),
                decision=decision.action,
                confidence=float(decision.confidence),
                reasons=decision_reasons or ("strategy_requested_exit",),
                allocation_weight=float(allocation_weight),
                current_drawdown_pct=float(current_drawdown_pct),
            ))

        if decision.action not in {"enter_long", "enter_short"}:
            status = "hold" if decision.action == "hold" else "blocked_invalid_action"
            fallback_reasons = ("no_actionable_edge",) if status == "hold" else ("unsupported_action",)
            pipeline_trace.append(
                self._trace_step(
                    "decision",
                    status,
                    decision_reasons or fallback_reasons,
                    perf_counter() - eval_started_at,
                )
            )
            return self.reasoner.synthesize(
                DecisionEnvelope(
                    trace_id=trace_id,
                    strategy_id=strategy_id,
                    symbol=state.symbol,
                    status=status,
                    decision=decision.action,
                    confidence=float(decision.confidence),
                    reasons=decision_reasons or fallback_reasons,
                    pipeline_trace=tuple(pipeline_trace),
                    allocation_weight=float(allocation_weight),
                    current_drawdown_pct=float(current_drawdown_pct),
                )
            )

        if position.direction:
            reasons = self._combine_reasons((*decision_reasons, "position_already_open"))
            pipeline_trace.append(
                self._trace_step(
                    "position_gate",
                    "blocked",
                    reasons,
                    perf_counter() - eval_started_at,
                )
            )
            return self.reasoner.synthesize(DecisionEnvelope(
                trace_id=trace_id,
                strategy_id=strategy_id,
                symbol=state.symbol,
                status="blocked_existing_position",
                decision=decision.action,
                confidence=float(decision.confidence),
                reasons=reasons,
                pipeline_trace=tuple(pipeline_trace),
                allocation_weight=float(allocation_weight),
                current_drawdown_pct=float(current_drawdown_pct),
            ))

        min_entry_confidence = float(self.config.min_entry_confidence)
        if float(decision.confidence) < min_entry_confidence:
            reasons = self._combine_reasons(
                (*decision_reasons, "confidence_below_min_entry", f"min_entry_confidence={min_entry_confidence:.2f}")
            )
            pipeline_trace.append(
                self._trace_step(
                    "confidence_gate",
                    "blocked",
                    reasons,
                    perf_counter() - eval_started_at,
                    {"min_entry_confidence": min_entry_confidence},
                )
            )
            return self.reasoner.synthesize(DecisionEnvelope(
                trace_id=trace_id,
                strategy_id=strategy_id,
                symbol=state.symbol,
                status="blocked_low_confidence",
                decision=decision.action,
                confidence=float(decision.confidence),
                reasons=reasons,
                pipeline_trace=tuple(pipeline_trace),
                allocation_weight=float(allocation_weight),
                current_drawdown_pct=float(current_drawdown_pct),
            ))

        candidate_started_at = perf_counter()
        candidate = self._build_candidate(state, strategy_id, decision, allocation_weight)
        if candidate is None:
            reasons = self._combine_reasons((*decision_reasons, "candidate_build_failed"))
            pipeline_trace.append(
                self._trace_step(
                    "candidate_build",
                    "blocked",
                    reasons,
                    perf_counter() - candidate_started_at,
                )
            )
            return self.reasoner.synthesize(DecisionEnvelope(
                trace_id=trace_id,
                strategy_id=strategy_id,
                symbol=state.symbol,
                status="blocked_invalid_candidate",
                decision=decision.action,
                confidence=float(decision.confidence),
                reasons=reasons,
                pipeline_trace=tuple(pipeline_trace),
                allocation_weight=float(allocation_weight),
                current_drawdown_pct=float(current_drawdown_pct),
            ))
        pipeline_trace.append(
            self._trace_step(
                "candidate_build",
                "passed",
                ("candidate_constructed",),
                perf_counter() - candidate_started_at,
                {
                    "direction": candidate.direction,
                    "size_pct": float(candidate.size_pct),
                },
            )
        )

        risk_started_at = perf_counter()
        risk = self.live_pipeline.evaluate(candidate, current_drawdown_pct)
        risk = replace(risk, trace_id=trace_id)
        plan = self._build_plan(candidate, risk, trace_id, decision_reasons)
        combined_reasons = self._combine_reasons((*decision_reasons, *(risk.reasons or (risk.reason,))))
        pipeline_trace.append(
            self._trace_step(
                "risk_gate",
                "approved" if risk.approved else "blocked",
                risk.reasons or (risk.reason,),
                perf_counter() - risk_started_at,
                {
                    "risk_reason": risk.reason,
                    "size_pct": float(risk.size_pct),
                },
            )
        )
        pipeline_trace.append(
            self._trace_step(
                "decision",
                "ready" if plan else "blocked_risk",
                combined_reasons,
                perf_counter() - eval_started_at,
            )
        )
        return self.reasoner.synthesize(DecisionEnvelope(
            trace_id=trace_id,
            strategy_id=strategy_id,
            symbol=state.symbol,
            status="ready" if plan else "blocked_risk",
            decision=decision.action,
            confidence=float(decision.confidence),
            reasons=combined_reasons,
            pipeline_trace=tuple(pipeline_trace),
            allocation_weight=float(allocation_weight),
            current_drawdown_pct=float(current_drawdown_pct),
            candidate=candidate,
            risk=risk,
            execution_plan=plan,
        ))

    def to_decision_record(self, envelope: DecisionEnvelope, context: ActionContext) -> DecisionRecord:
        risk = envelope.risk
        governance = envelope.governance
        return DecisionRecord(
            action_id=context.action_id,
            trace_id=envelope.trace_id,
            strategy_id=envelope.strategy_id,
            symbol=envelope.symbol,
            decision=envelope.decision,
            status=envelope.status,
            confidence=float(envelope.confidence),
            reasons=envelope.reasons,
            pipeline_trace=envelope.pipeline_trace,
            risk_approved=bool(risk.approved) if risk else False,
            risk_reason=str(risk.reason or "") if risk else "",
            governance_approved=bool(governance.approved) if governance else False,
            governance_mode=str(governance.mode or "") if governance else "",
            governance_reason=str(governance.reason or "") if governance else "",
            runtime_mode=str(governance.runtime_mode or "") if governance else "",
            allocation_weight=float(envelope.allocation_weight),
            current_drawdown_pct=float(envelope.current_drawdown_pct),
            created_at=context.created_at,
            reasoned_decision=envelope.reasoned_decision,
        )

    def _build_plan(
        self,
        candidate: TradeCandidate,
        risk: RiskDecision,
        trace_id: str,
        decision_reasons: tuple[str, ...],
    ) -> ExecutionPlan | None:
        return self.live_pipeline.build_execution_plan(
            candidate,
            risk,
            trace_id=trace_id,
            decision_reasons=decision_reasons,
        )

    def _build_candidate(
        self,
        state: MarketState,
        strategy_id: str,
        decision: StrategyDecision,
        allocation_weight: float,
    ) -> TradeCandidate | None:
        size_pct = float(allocation_weight) * float(self.config.risk_per_trade_pct)
        if size_pct <= 0:
            return None
        stop_loss = decision.stop_loss if decision.stop_loss is not None else state.close
        direction = "long" if decision.action == "enter_long" else "short"
        return TradeCandidate(
            strategy_id=strategy_id,
            symbol=state.symbol,
            direction=direction,
            entry_price=float(state.close),
            stop_loss=float(stop_loss),
            take_profit=decision.take_profit,
            size_pct=size_pct,
        )

    @staticmethod
    def _build_trace_id(context: ActionContext, strategy_id: str, symbol: str) -> str:
        safe_strategy = str(strategy_id or "strategy").replace(":", "_")
        safe_symbol = str(symbol or "symbol").replace("/", "_").replace(":", "_")
        return f"{context.action_id}:{safe_strategy}:{safe_symbol}"

    @staticmethod
    def _combine_reasons(reasons) -> tuple[str, ...]:
        seen = set()
        normalized: list[str] = []
        for item in reasons or ():
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    @staticmethod
    def _trace_step(
        step: str,
        status: str,
        reasons,
        duration_sec: float,
        details: dict | None = None,
    ) -> DecisionTraceStep:
        return DecisionTraceStep(
            step=str(step or "").strip() or "unknown",
            status=str(status or "").strip() or "observed",
            reasons=DecisionCore._combine_reasons(reasons),
            duration_ms=max(0.0, float(duration_sec) * 1000.0),
            details=dict(details or {}),
        )

