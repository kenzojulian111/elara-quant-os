from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ExternalBusMessage:
    event_id: str
    event_type: str
    payload: Mapping[str, object] = field(default_factory=dict)
    source: str = "elara"
    created_at: float = 0.0
    stream_id: str = ""


class InMemoryExternalBusAdapter:
    """Test adapter matching the external durable bus contract."""

    def __init__(self) -> None:
        self._messages: list[ExternalBusMessage] = []

    def publish(
        self,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        source: str = "elara",
    ) -> ExternalBusMessage:
        message = ExternalBusMessage(
            event_id=uuid.uuid4().hex,
            event_type=str(event_type or "").strip(),
            payload=dict(payload or {}),
            source=str(source or "elara"),
            created_at=time.time(),
        )
        self._messages.append(message)
        return message

    def replay(self, *, after_offset: int = 0, limit: int = 0) -> tuple[ExternalBusMessage, ...]:
        messages = tuple(self._messages[max(0, int(after_offset)) :])
        return messages[:limit] if limit > 0 else messages

    @property
    def depth(self) -> int:
        return len(self._messages)


class DurableBusUnavailableError(RuntimeError):
    pass


class RedisStreamsExternalBusAdapter:
    """Durable Redis Streams adapter for replayable inter-service events."""

    def __init__(self, client, *, stream_key: str, maxlen: int = 100_000) -> None:
        if client is None:
            raise DurableBusUnavailableError("redis_client_required")
        self.client = client
        self.stream_key = str(stream_key or "").strip()
        if not self.stream_key:
            raise ValueError("stream_key_required")
        self.maxlen = max(100, int(maxlen))

    def publish(
        self,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        source: str = "elara",
    ) -> ExternalBusMessage:
        event_id = uuid.uuid4().hex
        created_at = time.time()
        fields = {
            "event_id": event_id,
            "event_type": str(event_type or "").strip(),
            "payload": json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), default=str),
            "source": str(source or "elara"),
            "created_at": f"{created_at:.6f}",
        }
        try:
            stream_id = self.client.xadd(self.stream_key, fields, maxlen=self.maxlen, approximate=True)
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_publish_failed:{exc}") from exc
        return ExternalBusMessage(
            event_id=event_id,
            event_type=fields["event_type"],
            payload=dict(payload or {}),
            source=fields["source"],
            created_at=created_at,
            stream_id=_text(stream_id),
        )

    def replay(self, *, after_offset: str = "", limit: int = 0) -> tuple[ExternalBusMessage, ...]:
        minimum = f"({_text(after_offset)}" if _text(after_offset) else "-"
        count = max(1, int(limit)) if int(limit) > 0 else None
        try:
            rows = self.client.xrange(self.stream_key, min=minimum, max="+", count=count)
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_replay_failed:{exc}") from exc
        return tuple(self._decode(stream_id, fields) for stream_id, fields in rows)

    def ensure_group(self, group: str, *, start_id: str = "0-0") -> None:
        try:
            self.client.xgroup_create(self.stream_key, str(group), id=str(start_id), mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                raise DurableBusUnavailableError(f"redis_stream_group_failed:{exc}") from exc

    def consume(
        self,
        *,
        group: str,
        consumer: str,
        count: int = 100,
        block_ms: int = 1000,
        include_pending: bool = False,
    ) -> tuple[ExternalBusMessage, ...]:
        self.ensure_group(group)
        try:
            streams = self.client.xreadgroup(
                str(group),
                str(consumer),
                {self.stream_key: "0" if include_pending else ">"},
                count=max(1, int(count)),
                block=max(0, int(block_ms)),
            )
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_consume_failed:{exc}") from exc
        return tuple(
            self._decode(stream_id, fields)
            for _, rows in streams
            for stream_id, fields in rows
        )

    def acknowledge(self, *, group: str, stream_ids: tuple[str, ...]) -> int:
        if not stream_ids:
            return 0
        try:
            return int(self.client.xack(self.stream_key, str(group), *stream_ids))
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_ack_failed:{exc}") from exc

    def claim_stale(
        self,
        *,
        group: str,
        consumer: str,
        min_idle_ms: int = 30_000,
        count: int = 100,
    ) -> tuple[ExternalBusMessage, ...]:
        self.ensure_group(group)
        try:
            response = self.client.xautoclaim(
                self.stream_key,
                str(group),
                str(consumer),
                min_idle_time=max(0, int(min_idle_ms)),
                start_id="0-0",
                count=max(1, int(count)),
            )
            rows = response[1] if len(response) > 1 else ()
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_claim_failed:{exc}") from exc
        return tuple(self._decode(stream_id, fields) for stream_id, fields in rows)

    def delete(self, *stream_ids: str) -> int:
        if not stream_ids:
            return 0
        try:
            return int(self.client.xdel(self.stream_key, *stream_ids))
        except Exception as exc:
            raise DurableBusUnavailableError(f"redis_stream_delete_failed:{exc}") from exc

    @staticmethod
    def _decode(stream_id, fields) -> ExternalBusMessage:
        normalized = {_text(key): _text(value) for key, value in dict(fields).items()}
        try:
            payload = json.loads(normalized.get("payload") or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return ExternalBusMessage(
            event_id=normalized.get("event_id", ""),
            event_type=normalized.get("event_type", ""),
            payload=payload,
            source=normalized.get("source", "elara"),
            created_at=float(normalized.get("created_at") or 0.0),
            stream_id=_text(stream_id),
        )


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")
