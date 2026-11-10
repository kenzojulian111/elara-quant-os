from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    event_type: str
    source: str
    occurred_at: float
    payload: dict[str, Any]
    correlation_id: str = ""
    trace_id: str = ""
    decision_id: str = ""
    execution_id: str = ""
    config_hash: str = ""
    previous_hash: str = ""
    event_hash: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "occurred_at": float(self.occurred_at),
            "schema_version": int(self.schema_version),
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "decision_id": self.decision_id,
            "execution_id": self.execution_id,
            "config_hash": self.config_hash,
            "previous_hash": self.previous_hash,
            "payload": _json_safe(self.payload),
            "event_hash": self.event_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvent":
        return cls(
            event_id=str(data.get("event_id") or ""),
            event_type=str(data.get("event_type") or ""),
            source=str(data.get("source") or ""),
            occurred_at=float(data.get("occurred_at") or 0.0),
            schema_version=int(data.get("schema_version") or 1),
            correlation_id=str(data.get("correlation_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
            decision_id=str(data.get("decision_id") or ""),
            execution_id=str(data.get("execution_id") or ""),
            config_hash=str(data.get("config_hash") or ""),
            previous_hash=str(data.get("previous_hash") or ""),
            payload=dict(data.get("payload") or {}),
            event_hash=str(data.get("event_hash") or ""),
        )

    def hash_payload(self) -> str:
        data = self.to_dict()
        data.pop("event_hash", None)
        return stable_hash(data)


class RuntimeEventStore:
    def __init__(self, path: str | os.PathLike[str], *, time_fn=None):
        self.path = Path(path)
        self._time_fn = time_fn or time.time

    def append(
        self,
        event_type: str,
        *,
        source: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str = "",
        trace_id: str = "",
        decision_id: str = "",
        execution_id: str = "",
        config_hash: str = "",
    ) -> RuntimeEvent:
        normalized_type = str(event_type or "").strip().upper()
        if not normalized_type:
            raise ValueError("event_type is required")
        normalized_source = str(source or "").strip()
        if not normalized_source:
            raise ValueError("source is required")

        with _LOCK:
            previous_hash = self.last_event_hash()
            event = RuntimeEvent(
                event_id=uuid.uuid4().hex,
                event_type=normalized_type,
                source=normalized_source,
                occurred_at=float(self._time_fn()),
                payload=dict(payload or {}),
                correlation_id=str(correlation_id or ""),
                trace_id=str(trace_id or ""),
                decision_id=str(decision_id or ""),
                execution_id=str(execution_id or ""),
                config_hash=str(config_hash or ""),
                previous_hash=previous_hash,
            )
            event = RuntimeEvent.from_dict({**event.to_dict(), "event_hash": event.hash_payload()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(canonical_json(event.to_dict()) + "\n")
            return event

    def iter_events(self):
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                yield RuntimeEvent.from_dict(json.loads(line))

    def read_tail(self, limit: int = 100) -> list[RuntimeEvent]:
        limit = max(1, int(limit))
        events = list(self.iter_events() or [])
        return events[-limit:]

    def last_event_hash(self) -> str:
        last = ""
        for event in self.iter_events() or []:
            last = event.event_hash
        return last

    def verify_chain(self) -> dict[str, Any]:
        previous = ""
        count = 0
        errors: list[str] = []
        for event in self.iter_events() or []:
            count += 1
            if event.previous_hash != previous:
                errors.append(f"previous_hash_mismatch:{event.event_id}")
            if event.hash_payload() != event.event_hash:
                errors.append(f"event_hash_mismatch:{event.event_id}")
            previous = event.event_hash
        return {"ok": not errors, "count": count, "errors": tuple(errors)}


def default_runtime_event_store() -> RuntimeEventStore:
    from config.settings import Config

    path = str(getattr(Config, "RUNTIME_EVENT_LOG_PATH", "") or "").strip()
    if not path:
        path = os.path.join(str(getattr(Config, "root_path", os.getcwd())), "runtime", "runtime_events.jsonl")
    return RuntimeEventStore(path)


def append_runtime_event(event_type: str, *, source: str, payload: dict[str, Any] | None = None, **kwargs) -> RuntimeEvent | None:
    from config.settings import Config

    if not bool(getattr(Config, "RUNTIME_EVENT_STORE_ENABLED", True)):
        return None
    try:
        return default_runtime_event_store().append(event_type, source=source, payload=payload or {}, **kwargs)
    except Exception:
        return None
