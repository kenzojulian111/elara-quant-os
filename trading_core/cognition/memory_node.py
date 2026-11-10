from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True)
class MemoryNodeSnapshot:
    node_id: str
    item_count: int
    latest_trace_id: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "node_id": self.node_id,
            "item_count": int(self.item_count),
            "latest_trace_id": self.latest_trace_id,
            "labels": tuple(self.labels),
        }


class MemoryNode:
    def __init__(self, node_id: str, *, max_items: int = 1000):
        self.node_id = str(node_id or "memory")
        self.max_items = max(1, int(max_items))
        self._items: list[Mapping[str, object]] = []

    def append(self, item: Mapping[str, object]) -> None:
        self._items.append(dict(item))
        if len(self._items) > self.max_items:
            del self._items[0 : len(self._items) - self.max_items]

    def extend(self, items: Iterable[Mapping[str, object]]) -> None:
        for item in items:
            self.append(item)

    def recent(self, limit: int = 10) -> tuple[Mapping[str, object], ...]:
        count = max(1, int(limit))
        return tuple(self._items[-count:])

    def snapshot(self) -> MemoryNodeSnapshot:
        latest = self._items[-1] if self._items else {}
        labels = tuple(str(item.get("label") or item.get("event_type") or "") for item in self._items if item)
        return MemoryNodeSnapshot(
            node_id=self.node_id,
            item_count=len(self._items),
            latest_trace_id=str(latest.get("trace_id") or ""),
            labels=tuple(label for label in labels if label)[-8:],
        )
