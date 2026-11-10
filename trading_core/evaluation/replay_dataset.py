from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from trading_core.coordination.external_bus import ExternalBusMessage


@dataclass(frozen=True)
class ReplayDatasetManifest:
    schema: str
    dataset_id: str
    event_count: int
    first_stream_id: str
    last_stream_id: str
    events_sha256: str
    created_at: float


@dataclass(frozen=True)
class ReplayDataset:
    manifest: ReplayDatasetManifest
    events: tuple[ExternalBusMessage, ...]


class ReplayDatasetStore:
    SCHEMA = "elara.replay.dataset.v1"

    def write(
        self,
        path: str | Path,
        events: Iterable[ExternalBusMessage],
        *,
        dataset_id: str = "",
    ) -> ReplayDatasetManifest:
        rows = tuple(events)
        event_lines = tuple(_event_line(event) for event in rows)
        manifest = ReplayDatasetManifest(
            schema=self.SCHEMA,
            dataset_id=str(dataset_id or uuid.uuid4().hex),
            event_count=len(rows),
            first_stream_id=rows[0].stream_id if rows else "",
            last_stream_id=rows[-1].stream_id if rows else "",
            events_sha256=_lines_hash(event_lines),
            created_at=time.time(),
        )
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical({"manifest": asdict(manifest)}) + "\n")
            for line in event_lines:
                handle.write(line + "\n")
        return manifest

    def load_verified(self, path: str | Path) -> ReplayDataset:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        if not lines:
            raise ValueError("replay_dataset_empty")
        header = json.loads(lines[0])
        manifest_data = header.get("manifest") if isinstance(header, dict) else None
        if not isinstance(manifest_data, dict):
            raise ValueError("replay_dataset_manifest_missing")
        manifest = ReplayDatasetManifest(**manifest_data)
        if manifest.schema != self.SCHEMA:
            raise ValueError("replay_dataset_schema_mismatch")
        event_lines = tuple(lines[1:])
        if _lines_hash(event_lines) != manifest.events_sha256:
            raise ValueError("replay_dataset_hash_mismatch")
        events = tuple(_event_from_line(line) for line in event_lines)
        if len(events) != manifest.event_count:
            raise ValueError("replay_dataset_count_mismatch")
        if events and (events[0].stream_id != manifest.first_stream_id or events[-1].stream_id != manifest.last_stream_id):
            raise ValueError("replay_dataset_boundary_mismatch")
        return ReplayDataset(manifest=manifest, events=events)


def _event_line(event: ExternalBusMessage) -> str:
    return _canonical(
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "payload": dict(event.payload),
            "source": event.source,
            "created_at": float(event.created_at),
            "stream_id": event.stream_id,
        }
    )


def _event_from_line(line: str) -> ExternalBusMessage:
    data = json.loads(line)
    return ExternalBusMessage(
        event_id=str(data.get("event_id") or ""),
        event_type=str(data.get("event_type") or ""),
        payload=dict(data.get("payload") or {}),
        source=str(data.get("source") or "elara"),
        created_at=float(data.get("created_at") or 0.0),
        stream_id=str(data.get("stream_id") or ""),
    )


def _lines_hash(lines: tuple[str, ...]) -> str:
    raw = "".join(f"{line}\n" for line in lines)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
