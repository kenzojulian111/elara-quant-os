from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from trading_core.data_contracts import DecisionEnvelope


@dataclass(frozen=True)
class DecisionNarrative:
    trace_id: str
    headline: str
    summary: str
    probability_line: str
    risk_line: str
    governance_line: str
    reasons: tuple[str, ...]
    constraints: tuple[str, ...]
    timeline: tuple[str, ...]

    def to_text(self) -> str:
        lines = [
            self.headline,
            "",
            self.summary,
            self.probability_line,
        ]
        if self.risk_line:
            lines.append(self.risk_line)
        if self.governance_line:
            lines.append(self.governance_line)
        if self.reasons:
            lines.append("")
            lines.append("Why:")
            lines.extend(f"- {reason}" for reason in self.reasons)
        if self.constraints:
            lines.append("")
            lines.append("Constraints:")
            lines.extend(f"- {constraint}" for constraint in self.constraints)
        if self.timeline:
            lines.append("")
            lines.append("Timeline:")
            lines.extend(f"- {item}" for item in self.timeline)
        return "\n".join(lines).strip() + "\n"

    def to_dict(self) -> Mapping[str, object]:
        return {
            "trace_id": self.trace_id,
            "headline": self.headline,
            "summary": self.summary,
            "probability_line": self.probability_line,
            "risk_line": self.risk_line,
            "governance_line": self.governance_line,
            "reasons": tuple(self.reasons),
            "constraints": tuple(self.constraints),
            "timeline": tuple(self.timeline),
        }


def build_decision_narrative(envelope: DecisionEnvelope, *, max_items: int = 8) -> DecisionNarrative:
    reasoned = envelope.reasoned_decision
    final_action = str(reasoned.action if reasoned else envelope.decision)
    approved = bool(reasoned.approved if reasoned else envelope.execution_plan is not None)
    approval_word = "APPROVED" if approved else "BLOCKED"
    headline = f"{approval_word} {final_action.upper()} for {envelope.symbol}"
    if envelope.decision != final_action:
        headline += f" (requested {envelope.decision})"

    summary = str(reasoned.summary if reasoned else envelope.status)
    probability_line = _probability_line(envelope)
    risk_line = _risk_line(envelope)
    governance_line = _governance_line(envelope)
    reasons = _limit_items(tuple(reasoned.reasons if reasoned else envelope.reasons), max_items)
    constraints = _limit_items(tuple(reasoned.constraints if reasoned else ()), max_items)
    timeline = _limit_items(_timeline(envelope), max_items)
    return DecisionNarrative(
        trace_id=envelope.trace_id,
        headline=headline,
        summary=summary,
        probability_line=probability_line,
        risk_line=risk_line,
        governance_line=governance_line,
        reasons=reasons,
        constraints=constraints,
        timeline=timeline,
    )


def _probability_line(envelope: DecisionEnvelope) -> str:
    reasoned = envelope.reasoned_decision
    if reasoned is None:
        return "Probability: unavailable"
    return (
        "Probability: "
        f"{float(reasoned.probability):.2f}, "
        f"uncertainty {float(reasoned.uncertainty):.2f}, "
        f"expected utility {float(reasoned.expected_utility):.2f}"
    )


def _risk_line(envelope: DecisionEnvelope) -> str:
    risk = envelope.risk
    if risk is None:
        return ""
    status = "approved" if risk.approved else "blocked"
    return f"Risk: {status} ({risk.reason}) size={float(risk.size_pct):.4f}"


def _governance_line(envelope: DecisionEnvelope) -> str:
    governance = envelope.governance
    if governance is None:
        return ""
    status = "approved" if governance.approved else "blocked"
    runtime = f", runtime={governance.runtime_mode}" if governance.runtime_mode else ""
    return f"Governance: {status} mode={governance.mode}{runtime} ({governance.reason})"


def _timeline(envelope: DecisionEnvelope) -> tuple[str, ...]:
    items: list[str] = []
    for step in envelope.pipeline_trace:
        reason_text = ", ".join(step.reasons[:2])
        suffix = f" because {reason_text}" if reason_text else ""
        items.append(f"{step.step} -> {step.status}{suffix}")
    return tuple(items)


def _limit_items(items: tuple[str, ...], max_items: int) -> tuple[str, ...]:
    limit = max(1, int(max_items))
    return tuple(str(item) for item in items[:limit])
