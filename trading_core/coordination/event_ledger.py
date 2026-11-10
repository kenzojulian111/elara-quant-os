from __future__ import annotations

import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from core.runtime_events import stable_hash
from utils.fast_json import dumps_text, loads


LEDGER_SCHEMA_VERSION = 1


class EventLedger:
    def __init__(self, path: str):
        self.path = str(path or "").strip()
        self._lock = threading.Lock()
        self._sequence = 0
        self._last_hash = ""
        self._load_tail_state()

    def append(
        self,
        event_type: str,
        payload: dict,
        *,
        trace_id: str = "",
        correlation_id: str = "",
        source: str = "trading_core",
    ) -> None:
        if not self.path:
            return
        with self._lock:
            sequence = self._sequence + 1
            record = {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "sequence": sequence,
                "event_id": self._event_id(sequence),
                "event_type": str(event_type or "").strip(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": str(source or "trading_core"),
                "trace_id": str(trace_id or _payload_value(payload, "trace_id")),
                "correlation_id": str(correlation_id or _payload_value(payload, "action_id")),
                "previous_hash": self._last_hash,
                "event_hash": "",
                "payload": payload,
            }
            record["event_hash"] = _hash_record(record)
            line = dumps_text(record)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._sequence = sequence
            self._last_hash = str(record["event_hash"])

    def append_dataclass(self, event_type: str, payload) -> None:
        data = asdict(payload)
        self.append(
            event_type,
            data,
            trace_id=str(data.get("trace_id") or ""),
            correlation_id=str(data.get("action_id") or data.get("trace_id") or ""),
        )

    def read(self) -> Iterable[dict]:
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    yield loads(text)
                except Exception:
                    continue

    def verify_hash_chain(self) -> tuple[bool, str]:
        previous = ""
        count = 0
        for record in self.read():
            count += 1
            if int(record.get("schema_version") or 0) != LEDGER_SCHEMA_VERSION:
                return False, f"schema_version_mismatch:{count}"
            if str(record.get("previous_hash") or "") != previous:
                return False, f"previous_hash_mismatch:{count}"
            expected = _hash_record(record)
            if str(record.get("event_hash") or "") != expected:
                return False, f"event_hash_mismatch:{count}"
            previous = str(record.get("event_hash") or "")
        return True, "ok"

    def _load_tail_state(self) -> None:
        last_sequence = 0
        last_hash = ""
        try:
            for record in self.read():
                last_sequence = max(last_sequence, int(record.get("sequence") or 0))
                if record.get("event_hash"):
                    last_hash = str(record.get("event_hash") or "")
        except Exception:
            last_sequence = 0
            last_hash = ""
        self._sequence = last_sequence
        self._last_hash = last_hash

    def _event_id(self, sequence: int) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{stamp}-{int(sequence):06d}"


def _hash_record(record: dict) -> str:
    normalized = dict(record)
    normalized["event_hash"] = ""
    return stable_hash(normalized)


def _payload_value(payload: dict, key: str) -> str:
    try:
        return str((payload or {}).get(key) or "")
    except Exception:
        return ""

