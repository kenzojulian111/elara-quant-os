from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from trading_core.coordination.decision_lifecycle import DecisionLifecycleEvent, decision_lifecycle_events
from trading_core.coordination.event_stream import EventRecord, EventStream, StreamReplayCursor
from trading_core.data_contracts import DecisionEnvelope


@dataclass(frozen=True)
class EventSourcingWriteResult:
    trace_id: str
    written: int
    first_sequence: int = 0
    last_sequence: int = 0
    event_hashes: tuple[str, ...] = ()

    def to_dict(self) -> Mapping[str, object]:
        return {
            "trace_id": self.trace_id,
            "written": int(self.written),
            "first_sequence": int(self.first_sequence),
            "last_sequence": int(self.last_sequence),
            "event_hashes": tuple(self.event_hashes),
        }


class DecisionEventSourcingWriter:
    def __init__(self, stream: EventStream, *, source: str = "decision_lifecycle"):
        self.stream = stream
        self.source = str(source or "decision_lifecycle")

    def append_envelope(self, envelope: DecisionEnvelope) -> EventSourcingWriteResult:
        records: list[EventRecord] = []
        for event in decision_lifecycle_events(envelope):
            records.append(self.append_event(event))
        sequences = tuple(record.sequence for record in records)
        return EventSourcingWriteResult(
            trace_id=envelope.trace_id,
            written=len(records),
            first_sequence=min(sequences) if sequences else 0,
            last_sequence=max(sequences) if sequences else 0,
            event_hashes=tuple(record.event_hash for record in records),
        )

    def append_event(self, event: DecisionLifecycleEvent) -> EventRecord:
        return self.stream.append(
            event.event_type,
            dict(event.to_dict()),
            stream="decision_lifecycle",
            correlation_id=event.trace_id,
            trace_id=event.trace_id,
            source=self.source,
            version=1,
        )


def replay_decision_events(
    stream: EventStream,
    *,
    trace_id: str = "",
    after_sequence: int = 0,
    limit: int = 0,
) -> tuple[EventRecord, ...]:
    records = stream.replay(
        StreamReplayCursor(stream="decision_lifecycle", after_sequence=after_sequence),
        limit=limit,
    )
    if not trace_id:
        return tuple(records)
    return tuple(record for record in records if record.trace_id == trace_id)


def lifecycle_events_from_records(records: Iterable[EventRecord]) -> tuple[Mapping[str, object], ...]:
    out: list[Mapping[str, object]] = []
    for record in records:
        payload = record.payload
        out.append(
            {
                "sequence": int(payload.get("sequence") or record.sequence),
                "event_type": record.event_type,
                "trace_id": record.trace_id,
                "step": str(payload.get("step") or ""),
                "status": str(payload.get("status") or ""),
                "reasons": tuple(payload.get("reasons") or ()),
            }
        )
    return tuple(out)
