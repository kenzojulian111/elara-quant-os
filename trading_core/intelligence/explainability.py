from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_core.data_contracts import DecisionEnvelope
from trading_core.intelligence.probabilistic import ProbabilisticDecisionEngine, ProbabilityAssessment


@dataclass(frozen=True)
class DecisionExplanation:
    trace_id: str
    symbol: str
    requested_action: str
    final_action: str
    approved: bool
    confidence: float
    probability: float
    uncertainty: float
    expected_utility: float
    probability_model: str
    posterior: dict[str, Any]
    summary: str
    reasons: tuple[str, ...]
    constraints: tuple[str, ...]
    component_breakdown: tuple[dict[str, Any], ...]
    pipeline: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "symbol": self.symbol,
            "requested_action": self.requested_action,
            "final_action": self.final_action,
            "approved": self.approved,
            "confidence": self.confidence,
            "probability": self.probability,
            "uncertainty": self.uncertainty,
            "expected_utility": self.expected_utility,
            "probability_model": self.probability_model,
            "posterior": dict(self.posterior),
            "summary": self.summary,
            "reasons": tuple(self.reasons),
            "constraints": tuple(self.constraints),
            "component_breakdown": tuple(self.component_breakdown),
            "pipeline": tuple(self.pipeline),
        }


def explain_decision(
    envelope: DecisionEnvelope,
    *,
    probability: ProbabilityAssessment | None = None,
) -> DecisionExplanation:
    reasoned = envelope.reasoned_decision
    probability = probability or ProbabilisticDecisionEngine().assess(envelope)
    components = tuple(
        {
            "source": component.source,
            "label": component.label,
            "confidence": float(component.confidence),
            "weight": float(component.weight),
            "verdict": component.verdict,
            "reasons": tuple(component.reasons),
        }
        for component in (reasoned.components if reasoned else ())
    )
    pipeline = tuple(
        {
            "step": step.step,
            "status": step.status,
            "reasons": tuple(step.reasons),
            "duration_ms": float(step.duration_ms),
            "details": dict(step.details),
        }
        for step in envelope.pipeline_trace
    )
    return DecisionExplanation(
        trace_id=envelope.trace_id,
        symbol=envelope.symbol,
        requested_action=envelope.decision,
        final_action=reasoned.action if reasoned else envelope.decision,
        approved=bool(reasoned.approved if reasoned else envelope.execution_plan is not None),
        confidence=float(reasoned.confidence if reasoned else envelope.confidence),
        probability=float(probability.probability),
        uncertainty=float(probability.uncertainty),
        expected_utility=float(probability.expected_utility),
        probability_model=str(probability.probability_model),
        posterior=probability.posterior.to_dict() if probability.posterior else {},
        summary=str(reasoned.summary if reasoned else envelope.status),
        reasons=tuple(reasoned.reasons if reasoned else envelope.reasons),
        constraints=tuple(reasoned.constraints if reasoned else ()),
        component_breakdown=components,
        pipeline=pipeline,
    )
