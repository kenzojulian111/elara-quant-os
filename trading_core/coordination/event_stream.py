from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from utils.fast_json import dumps_text, loads


@dataclass(frozen=True)
class EventRecord:
    event_type: str
    payload: dict[str, Any]
    stream: str = "default"
    event_id: str = ""
    ts: float = 0.0
    correlation_id: str = ""
    trace_id: str = ""
    source: str = ""
    version: int = 1
    sequence: int = 0
    previous_hash: str = ""
    event_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "stream": self.stream,
            "ts": float(self.ts),
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "source": self.source,
            "version": int(self.version),
            "sequence": int(self.sequence),
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class StreamReplayCursor:
    stream: str = "default"
    after_sequence: int = 0
    after_ts: float = 0.0


@dataclass(frozen=True)
class BackpressureDecision:
    accepted: bool
    reason: str
    queue_depth: int = 0
    dropped_event_id: str = ""


@dataclass
class InMemorySubscriber:
    name: str
    max_pending: int = 1000
    drop_policy: str = "drop_oldest"
    items: list[EventRecord] = field(default_factory=list)

    def publish(self, record: EventRecord) -> BackpressureDecision:
        depth = len(self.items)
        if depth < max(1, int(self.max_pending)):
            self.items.append(record)
            return BackpressureDecision(True, "accepted", queue_depth=len(self.items))
        if self.drop_policy == "reject_new":
            return BackpressureDecision(False, "subscriber_queue_full", queue_depth=depth)
        dropped = self.items.pop(0)
        self.items.append(record)
        return BackpressureDecision(True, "dropped_oldest", queue_depth=len(self.items), dropped_event_id=dropped.event_id)

    def drain(self, limit: int | None = None) -> list[EventRecord]:
        if limit is None or limit >= len(self.items):
            out = list(self.items)
            self.items.clear()
            return out
        out = self.items[:limit]
        del self.items[:limit]
        return out


class EventStream:
    def __init__(self, path: str, *, stream: str = "default", source: str = "elara"):
        self.path = str(path or "").strip()
        self.stream = str(stream or "default").strip() or "default"
        self.source = str(source or "elara").strip() or "elara"
        self._lock = threading.RLock()
        self._subscribers: dict[str, InMemorySubscriber] = {}
        self._sequence = 0
        self._last_hash = ""
        self._load_tail_state()

    def subscribe(self, name: str, *, max_pending: int = 1000, drop_policy: str = "drop_oldest") -> InMemorySubscriber:
        subscriber = InMemorySubscriber(str(name or "subscriber"), max_pending=max_pending, drop_policy=drop_policy)
        with self._lock:
            self._subscribers[subscriber.name] = subscriber
        return subscriber

    def append(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        stream: str = "",
        correlation_id: str = "",
        trace_id: str = "",
        source: str = "",
        version: int = 1,
    ) -> EventRecord:
        with self._lock:
            sequence = self._sequence + 1
            previous_hash = self._last_hash
            record = EventRecord(
                event_id=uuid.uuid4().hex,
                event_type=str(event_type or "").strip(),
                stream=str(stream or self.stream or "default").strip() or "default",
                ts=time.time(),
                correlation_id=str(correlation_id or "").strip(),
                trace_id=str(trace_id or "").strip(),
                source=str(source or self.source or "elara").strip(),
                version=int(version or 1),
                sequence=sequence,
                previous_hash=previous_hash,
                payload=dict(payload or {}),
            )
            event_hash = self._hash_record(record)
            record = EventRecord(**{**record.to_dict(), "event_hash": event_hash})
            self._write(record)
            self._sequence = sequence
            self._last_hash = event_hash
            for subscriber in self._subscribers.values():
                subscriber.publish(record)
            return record

    def replay(
        self,
        cursor: StreamReplayCursor | None = None,
        *,
        event_types: tuple[str, ...] = (),
        limit: int = 0,
    ) -> Iterable[EventRecord]:
        cursor = cursor or StreamReplayCursor(stream=self.stream)
        wanted_types = {str(item) for item in event_types if str(item or "").strip()}
        count = 0
        for record in self.read_all():
            if cursor.stream and record.stream != cursor.stream:
                continue
            if cursor.after_sequence and record.sequence <= cursor.after_sequence:
                continue
            if cursor.after_ts and record.ts <= cursor.after_ts:
                continue
            if wanted_types and record.event_type not in wanted_types:
                continue
            yield record
            count += 1
            if limit and count >= limit:
                break

    def read_all(self) -> Iterable[EventRecord]:
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    data = loads(text)
                    if isinstance(data, dict):
                        yield self._record_from_dict(data)
                except Exception:
                    continue

    def verify_hash_chain(self) -> tuple[bool, str]:
        previous = ""
        for record in self.read_all():
            if record.previous_hash != previous:
                return False, f"previous_hash_mismatch:{record.sequence}"
            expected = self._hash_record(EventRecord(**{**record.to_dict(), "event_hash": ""}))
            if record.event_hash != expected:
                return False, f"event_hash_mismatch:{record.sequence}"
            previous = record.event_hash
        return True, "ok"

    def _load_tail_state(self) -> None:
        last = None
        try:
            for record in self.read_all():
                if record.stream == self.stream:
                    last = record
        except Exception:
            last = None
        if last is not None:
            self._sequence = int(last.sequence)
            self._last_hash = str(last.event_hash or "")

    def _write(self, record: EventRecord) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(dumps_text(record.to_dict()) + "\n")

    @staticmethod
    def _record_from_dict(data: dict[str, Any]) -> EventRecord:
        return EventRecord(
            event_id=str(data.get("event_id") or ""),
            event_type=str(data.get("event_type") or ""),
            stream=str(data.get("stream") or "default"),
            ts=float(data.get("ts") or 0.0),
            correlation_id=str(data.get("correlation_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
            source=str(data.get("source") or ""),
            version=int(data.get("version") or 1),
            sequence=int(data.get("sequence") or 0),
            previous_hash=str(data.get("previous_hash") or ""),
            event_hash=str(data.get("event_hash") or ""),
            payload=dict(data.get("payload") or {}),
        )

    @staticmethod
    def _hash_record(record: EventRecord) -> str:
        data = record.to_dict()
        data["event_hash"] = ""
        raw = dumps_text(data)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
