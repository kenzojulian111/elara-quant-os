from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

from trading_core.coordination.event_stream import EventRecord


@dataclass(frozen=True)
class DecisionReadModel:
    trace_id: str
    status: str = "pending"
    last_event: str = ""
    event_count: int = 0
    sequence: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


class DecisionProjector:
    TERMINAL_STATUS = {
        "EXECUTION_APPROVED": "approved",
        "DECISION_BLOCKED": "blocked",
        "GOVERNANCE_BLOCKED": "blocked",
        "RISK_SUPPRESSED": "suppressed",
    }

    def __init__(self) -> None:
        self._models: dict[str, DecisionReadModel] = {}

    def apply(self, record: EventRecord) -> DecisionReadModel:
        trace_id = str(record.trace_id or record.correlation_id or record.event_id)
        current = self._models.get(trace_id, DecisionReadModel(trace_id=trace_id))
        status = self.TERMINAL_STATUS.get(record.event_type, current.status)
        model = replace(
            current,
            status=status,
            last_event=record.event_type,
            event_count=current.event_count + 1,
            sequence=record.sequence,
            payload=dict(record.payload),
        )
        self._models[trace_id] = model
        return model

    def replay(self, records: Iterable[EventRecord]) -> tuple[DecisionReadModel, ...]:
        for record in records:
            self.apply(record)
        return self.all()

    def get(self, trace_id: str) -> DecisionReadModel | None:
        return self._models.get(str(trace_id))

    def all(self) -> tuple[DecisionReadModel, ...]:
        return tuple(sorted(self._models.values(), key=lambda model: model.trace_id))
