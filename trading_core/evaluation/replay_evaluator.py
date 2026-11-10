from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from trading_core.coordination.event_stream import EventRecord


@dataclass(frozen=True)
class ReplayEvaluationFinding:
    code: str
    severity: str
    message: str

    def to_dict(self) -> Mapping[str, object]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ReplayEvaluationReport:
    status: str
    event_count: int
    trace_ids: tuple[str, ...]
    findings: tuple[ReplayEvaluationFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warning")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "status": self.status,
            "event_count": int(self.event_count),
            "trace_ids": tuple(self.trace_ids),
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "findings": tuple(finding.to_dict() for finding in self.findings),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ReplayEvaluationConfig:
    required_events: tuple[str, ...] = ("SIGNAL_RECEIVED", "REASONING_SYNTHESIZED")
    terminal_events: tuple[str, ...] = (
        "EXECUTION_APPROVED",
        "DECISION_BLOCKED",
        "GOVERNANCE_BLOCKED",
        "RISK_SUPPRESSED",
    )


class ReplayDecisionEvaluator:
    def __init__(self, config: ReplayEvaluationConfig | None = None):
        self.config = config or ReplayEvaluationConfig()

    def evaluate_records(self, records: Iterable[EventRecord]) -> ReplayEvaluationReport:
        record_tuple = tuple(records)
        findings: list[ReplayEvaluationFinding] = []
        trace_ids = tuple(sorted({record.trace_id for record in record_tuple if record.trace_id}))
        if not record_tuple:
            findings.append(_critical("replay_empty", "Replay contains no events."))
            return _report(record_tuple, trace_ids, findings, metrics={"timeline_count": 0})

        sequences = tuple(record.sequence for record in record_tuple)
        if sequences != tuple(sorted(sequences)):
            findings.append(_critical("replay_sequence_not_monotonic", "Replay events are not sequence ordered."))

        for trace_id in trace_ids:
            trace_records = tuple(record for record in record_tuple if record.trace_id == trace_id)
            event_types = tuple(record.event_type for record in trace_records)
            for required in self.config.required_events:
                if required not in event_types:
                    findings.append(_critical("replay_required_event_missing", f"{trace_id} missing {required}."))
            if not any(event_type in self.config.terminal_events for event_type in event_types):
                findings.append(
                    ReplayEvaluationFinding(
                        code="replay_terminal_event_missing",
                        severity="warning",
                        message=f"{trace_id} has no terminal event.",
                    )
                )
            if "REASONING_SYNTHESIZED" in event_types and event_types[-1] not in {
                "REASONING_SYNTHESIZED",
                *self.config.terminal_events,
            }:
                findings.append(
                    ReplayEvaluationFinding(
                        code="replay_event_after_reasoning",
                        severity="warning",
                        message=f"{trace_id} has non-terminal events after final reasoning.",
                    )
                )

        return _report(
            record_tuple,
            trace_ids,
            findings,
            metrics={
                "timeline_count": len(trace_ids),
                "required_events": tuple(self.config.required_events),
                "terminal_events": tuple(self.config.terminal_events),
            },
        )


def _report(
    records: tuple[EventRecord, ...],
    trace_ids: tuple[str, ...],
    findings: list[ReplayEvaluationFinding],
    *,
    metrics: Mapping[str, object],
) -> ReplayEvaluationReport:
    critical_count = sum(1 for finding in findings if finding.severity == "critical")
    warning_count = sum(1 for finding in findings if finding.severity == "warning")
    status = "critical" if critical_count else "warning" if warning_count else "ok"
    return ReplayEvaluationReport(
        status=status,
        event_count=len(records),
        trace_ids=trace_ids,
        findings=tuple(findings),
        metrics=metrics,
    )


def _critical(code: str, message: str) -> ReplayEvaluationFinding:
    return ReplayEvaluationFinding(code=code, severity="critical", message=message)
