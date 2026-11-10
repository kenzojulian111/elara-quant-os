from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from utils.fast_json import dumps_text, loads


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    scope: str
    kind: str
    content: str
    confidence: float = 1.0
    ts: float = 0.0
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def score(self, *, now: float | None = None, half_life_sec: float = 86400.0) -> float:
        now = float(now if now is not None else time.time())
        age = max(0.0, now - float(self.ts if self.ts is not None else now))
        decay = 0.5 ** (age / max(1.0, float(half_life_sec)))
        return max(0.0, min(1.0, float(self.confidence))) * decay

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope,
            "kind": self.kind,
            "content": self.content,
            "confidence": float(self.confidence),
            "ts": float(self.ts),
            "tags": tuple(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MemorySummary:
    scope: str
    count: int
    top_tags: tuple[str, ...]
    summary: str
    confidence: float


class OperationalMemoryStore:
    def __init__(self, path: str = "", *, half_life_sec: float = 86400.0):
        self.path = str(path or "").strip()
        self.half_life_sec = max(1.0, float(half_life_sec or 86400.0))
        self._records: list[MemoryRecord] = list(self._load())

    def append(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        confidence: float = 1.0,
        tags: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            memory_id=uuid.uuid4().hex,
            scope=str(scope or "global"),
            kind=str(kind or "observation"),
            content=str(content or ""),
            confidence=max(0.0, min(1.0, float(confidence))),
            ts=float(ts if ts is not None else time.time()),
            tags=tuple(sorted({str(tag).strip() for tag in tags if str(tag).strip()})),
            metadata=dict(metadata or {}),
        )
        self._records.append(record)
        self._persist(record)
        return record

    def search(
        self,
        *,
        scope: str = "",
        kind: str = "",
        tags: Iterable[str] = (),
        query: str = "",
        limit: int = 10,
        now: float | None = None,
    ) -> list[MemoryRecord]:
        wanted_tags = {str(tag).strip() for tag in tags if str(tag).strip()}
        query_text = str(query or "").strip().lower()
        scored: list[tuple[float, MemoryRecord]] = []
        for record in self._records:
            if scope and record.scope != scope:
                continue
            if kind and record.kind != kind:
                continue
            if wanted_tags and not wanted_tags.intersection(record.tags):
                continue
            if query_text and query_text not in record.content.lower():
                continue
            scored.append((record.score(now=now, half_life_sec=self.half_life_sec), record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _score, record in scored[: max(1, int(limit or 10))]]

    def summarize(self, *, scope: str = "", limit: int = 25, now: float | None = None) -> MemorySummary:
        records = self.search(scope=scope, limit=limit, now=now)
        tag_counts: dict[str, int] = {}
        weighted_confidence = 0.0
        for record in records:
            weighted_confidence += record.score(now=now, half_life_sec=self.half_life_sec)
            for tag in record.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = tuple(tag for tag, _count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:8])
        fragments = [record.content.strip() for record in records[:5] if record.content.strip()]
        return MemorySummary(
            scope=scope or "global",
            count=len(records),
            top_tags=top_tags,
            summary=" | ".join(fragments),
            confidence=(weighted_confidence / len(records)) if records else 0.0,
        )

    def compress(self, *, scope: str = "", keep: int = 100, now: float | None = None) -> MemorySummary:
        summary = self.summarize(scope=scope, limit=keep, now=now)
        if summary.count <= keep:
            return summary
        filtered = self.search(scope=scope, limit=keep, now=now)
        keep_ids = {record.memory_id for record in filtered}
        self._records = [record for record in self._records if record.memory_id in keep_ids or (scope and record.scope != scope)]
        self._rewrite()
        return summary

    def all(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records)

    def _load(self) -> Iterable[MemoryRecord]:
        if not self.path or not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = loads(line.strip())
                    if isinstance(data, dict):
                        out.append(self._record_from_dict(data))
                except Exception:
                    continue
        return out

    def _persist(self, record: MemoryRecord) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(dumps_text(record.to_dict()) + "\n")

    def _rewrite(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            for record in self._records:
                handle.write(dumps_text(record.to_dict()) + "\n")

    @staticmethod
    def _record_from_dict(data: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_id=str(data.get("memory_id") or uuid.uuid4().hex),
            scope=str(data.get("scope") or "global"),
            kind=str(data.get("kind") or "observation"),
            content=str(data.get("content") or ""),
            confidence=float(data.get("confidence") or 0.0),
            ts=float(data.get("ts") or time.time()),
            tags=tuple(str(tag) for tag in (data.get("tags") or ())),
            metadata=dict(data.get("metadata") or {}),
        )
