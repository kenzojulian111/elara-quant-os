from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from trading_core.data_contracts import DecisionEnvelope, DecisionTraceStep


@dataclass(frozen=True)
class DecisionLifecycleEvent:
    sequence: int
    event_type: str
    trace_id: str
    strategy_id: str
    symbol: str
    step: str
    status: str
    reasons: tuple[str, ...]
    payload: Mapping[str, object]

    def to_dict(self) -> Mapping[str, object]:
        return {
            "sequence": int(self.sequence),
            "event_type": self.event_type,
            "trace_id": self.trace_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "step": self.step,
            "status": self.status,
            "reasons": tuple(self.reasons),
            "payload": dict(self.payload),
        }


def decision_lifecycle_events(envelope: DecisionEnvelope) -> tuple[DecisionLifecycleEvent, ...]:
    events: list[DecisionLifecycleEvent] = []
    for sequence, step in enumerate(envelope.pipeline_trace, start=1):
        events.append(_from_trace_step(envelope, step, sequence))
    if envelope.execution_plan is not None:
        events.append(
            DecisionLifecycleEvent(
                sequence=len(events) + 1,
                event_type="EXECUTION_APPROVED",
                trace_id=envelope.trace_id,
                strategy_id=envelope.strategy_id,
                symbol=envelope.symbol,
                step="execution_plan",
                status="approved",
                reasons=tuple(envelope.execution_plan.decision_reasons),
                payload={
                    "direction": envelope.execution_plan.direction,
                    "size_pct": float(envelope.execution_plan.size_pct),
                    "entry_price": float(envelope.execution_plan.entry_price),
                },
            )
        )
    return tuple(events)


def _from_trace_step(
    envelope: DecisionEnvelope,
    step: DecisionTraceStep,
    sequence: int,
) -> DecisionLifecycleEvent:
    return DecisionLifecycleEvent(
        sequence=sequence,
        event_type=_event_type_for_step(step),
        trace_id=envelope.trace_id,
        strategy_id=envelope.strategy_id,
        symbol=envelope.symbol,
        step=step.step,
        status=step.status,
        reasons=tuple(step.reasons),
        payload=dict(step.details),
    )


def _event_type_for_step(step: DecisionTraceStep) -> str:
    if step.step == "signal":
        return "SIGNAL_RECEIVED"
    if step.step == "attention":
        return "ATTENTION_SELECTED"
    if step.step == "objective":
        return "OBJECTIVE_SELECTED"
    if step.step == "temporal":
        return "TEMPORAL_CONTEXT_APPLIED"
    if step.step == "candidate_build":
        return "CANDIDATE_BUILT" if step.status == "passed" else "CANDIDATE_REJECTED"
    if step.step == "risk_gate":
        return "RISK_APPROVED" if step.status == "approved" else "RISK_SUPPRESSED"
    if step.step == "runtime_mode":
        return "RUNTIME_MODE_ASSESSED"
    if step.step == "governance":
        return "GOVERNANCE_APPROVED" if step.status == "approved" else "GOVERNANCE_BLOCKED"
    if step.step == "reasoning":
        return "REASONING_SYNTHESIZED"
    if step.step == "decision":
        return "DECISION_APPROVED" if step.status in {"ready", "exit_requested"} else "DECISION_BLOCKED"
    if step.status == "blocked":
        return "DECISION_BLOCKED"
    return f"{step.step.upper()}_{step.status.upper()}"
