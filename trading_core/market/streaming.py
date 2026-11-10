from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from trading_core.data_contracts import MarketState
from utils.observability import record_feed_freshness


@dataclass(frozen=True)
class MarketDataEvent:
    stream: str
    symbol: str
    event_type: str
    ts: float
    payload: dict[str, Any]
    sequence: int = 0


@dataclass(frozen=True)
class BackpressurePolicy:
    max_queue_depth: int = 1000
    drop_policy: str = "drop_oldest"
    critical_event_types: tuple[str, ...] = ("trade", "order_book", "halt")


@dataclass(frozen=True)
class BackpressureSnapshot:
    accepted: bool
    reason: str
    queue_depth: int
    dropped: int = 0


@dataclass
class MarketFanout:
    policy: BackpressurePolicy = field(default_factory=BackpressurePolicy)
    _queues: dict[str, deque[MarketDataEvent]] = field(default_factory=dict)
    _dropped: dict[str, int] = field(default_factory=dict)

    def subscribe(self, name: str) -> None:
        self._queues.setdefault(str(name or "subscriber"), deque())
        self._dropped.setdefault(str(name or "subscriber"), 0)

    def publish(self, event: MarketDataEvent) -> dict[str, BackpressureSnapshot]:
        if not self._queues:
            self.subscribe("default")
        snapshots: dict[str, BackpressureSnapshot] = {}
        for name, queue in self._queues.items():
            snapshots[name] = self._append_with_policy(name, queue, event)
        record_feed_freshness(
            event.stream,
            event.ts,
            symbol=event.symbol,
            stale_after_sec=30.0,
        )
        return snapshots

    def drain(self, name: str, limit: int = 0) -> list[MarketDataEvent]:
        queue = self._queues.setdefault(str(name or "subscriber"), deque())
        if limit <= 0 or limit >= len(queue):
            out = list(queue)
            queue.clear()
            return out
        out = [queue.popleft() for _ in range(limit)]
        return out

    def depth(self, name: str) -> int:
        return len(self._queues.get(str(name or "subscriber"), ()))

    def dropped(self, name: str) -> int:
        return int(self._dropped.get(str(name or "subscriber"), 0))

    def _append_with_policy(self, name: str, queue: deque[MarketDataEvent], event: MarketDataEvent) -> BackpressureSnapshot:
        max_depth = max(1, int(self.policy.max_queue_depth))
        if len(queue) < max_depth:
            queue.append(event)
            return BackpressureSnapshot(True, "accepted", len(queue), self.dropped(name))
        if event.event_type in set(self.policy.critical_event_types):
            queue.popleft()
            queue.append(event)
            self._dropped[name] = self.dropped(name) + 1
            return BackpressureSnapshot(True, "critical_bypass_dropped_oldest", len(queue), self.dropped(name))
        if self.policy.drop_policy == "reject_new":
            self._dropped[name] = self.dropped(name) + 1
            return BackpressureSnapshot(False, "queue_full_reject_new", len(queue), self.dropped(name))
        queue.popleft()
        queue.append(event)
        self._dropped[name] = self.dropped(name) + 1
        return BackpressureSnapshot(True, "dropped_oldest", len(queue), self.dropped(name))


class MarketStreamNormalizer:
    def __init__(self):
        self._sequence_by_stream: dict[str, int] = {}

    def normalize(self, raw: dict[str, Any], *, stream: str, symbol: str, event_type: str = "tick") -> MarketDataEvent:
        key = f"{stream}:{symbol}:{event_type}"
        sequence = self._sequence_by_stream.get(key, 0) + 1
        self._sequence_by_stream[key] = sequence
        ts = _to_epoch(raw.get("ts") or raw.get("timestamp") or time.time())
        return MarketDataEvent(
            stream=str(stream or "market"),
            symbol=str(symbol or raw.get("symbol") or "unknown"),
            event_type=str(event_type or raw.get("event_type") or "tick"),
            ts=ts,
            payload=dict(raw or {}),
            sequence=sequence,
        )

    def to_market_state(self, event: MarketDataEvent) -> MarketState:
        payload = event.payload
        ts = datetime.fromtimestamp(float(event.ts), tz=timezone.utc)
        return MarketState(
            ts=ts,
            symbol=event.symbol,
            open=_to_float(payload.get("open") or payload.get("o") or payload.get("price")),
            high=_to_float(payload.get("high") or payload.get("h") or payload.get("price")),
            low=_to_float(payload.get("low") or payload.get("l") or payload.get("price")),
            close=_to_float(payload.get("close") or payload.get("c") or payload.get("price")),
            volume=_to_float(payload.get("volume") or payload.get("v")),
            order_book_imbalance=_optional_float(payload.get("order_book_imbalance")),
            funding_rate=_optional_float(payload.get("funding_rate")),
            macro_signal=str(payload.get("macro_signal") or "").strip() or None,
            trend=str(payload.get("trend") or "").strip() or None,
            volatility=str(payload.get("volatility") or "").strip() or None,
            liquidity=str(payload.get("liquidity") or "").strip() or None,
        )


def replay_market_events(events: Iterable[MarketDataEvent]) -> Iterable[MarketState]:
    normalizer = MarketStreamNormalizer()
    for event in events:
        yield normalizer.to_market_state(event)


def _to_epoch(value: Any) -> float:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return time.time()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None
