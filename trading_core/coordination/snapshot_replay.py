from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from trading_core.coordination.event_stream import EventRecord
from utils.fast_json import dumps_text


@dataclass(frozen=True)
class RuntimeSnapshot:
    sequence: int
    state: Mapping[str, object] = field(default_factory=dict)
    state_hash: str = ""


@dataclass(frozen=True)
class SnapshotRestoreResult:
    snapshot: RuntimeSnapshot
    applied_events: int
    state: Mapping[str, object] = field(default_factory=dict)


class SnapshotReplayEngine:
    def capture(self, *, sequence: int, state: Mapping[str, object]) -> RuntimeSnapshot:
        normalized = dict(state)
        return RuntimeSnapshot(sequence=int(sequence), state=normalized, state_hash=_state_hash(normalized))

    def restore(
        self,
        snapshot: RuntimeSnapshot,
        records: Iterable[EventRecord],
    ) -> SnapshotRestoreResult:
        if snapshot.state_hash != _state_hash(snapshot.state):
            raise ValueError("snapshot_hash_mismatch")
        state = dict(snapshot.state)
        last_sequence = int(snapshot.sequence)
        applied = 0
        for record in records:
            if record.sequence <= last_sequence:
                continue
            if record.sequence != last_sequence + 1:
                raise ValueError(f"snapshot_replay_sequence_gap:{last_sequence}:{record.sequence}")
            ev_counts = state.get("event_counts")
            counts = dict(ev_counts) if isinstance(ev_counts, dict) else {}
            key = str(record.event_type)
            counts[key] = int(counts.get(key) or 0) + 1
            state["event_counts"] = counts
            state["last_event"] = str(record.event_type)
            state["last_sequence"] = record.sequence
            last_sequence = record.sequence
            applied += 1
        restored = self.capture(sequence=last_sequence, state=state)
        return SnapshotRestoreResult(snapshot=restored, applied_events=applied, state=state)


def _state_hash(state: Mapping[str, object]) -> str:
    return hashlib.sha256(dumps_text(dict(state)).encode("utf-8")).hexdigest()
