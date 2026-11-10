"""Coordination utilities for events and idempotency."""

from trading_core.coordination.action_context import ActionContext, DecisionRecord
from trading_core.coordination.decision_lifecycle import DecisionLifecycleEvent, decision_lifecycle_events
from trading_core.coordination.cqrs import DecisionProjector, DecisionReadModel
from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.event_sourcing import (
    DecisionEventSourcingWriter,
    EventSourcingWriteResult,
    lifecycle_events_from_records,
    replay_decision_events,
)
from trading_core.coordination.event_stream import EventRecord, EventStream, StreamReplayCursor
from trading_core.coordination.external_bus import (
    DurableBusUnavailableError,
    ExternalBusMessage,
    InMemoryExternalBusAdapter,
    RedisStreamsExternalBusAdapter,
)
from trading_core.coordination.event_types import (
    DECISION_EVENT,
    EXECUTION_EVENT,
    META_EVENT,
    PORTFOLIO_EVENT,
    REFLECTION_EVENT,
    RISK_EVENT,
)
from trading_core.coordination.idempotency import IdempotencyCache
from trading_core.coordination.snapshot_replay import RuntimeSnapshot, SnapshotReplayEngine, SnapshotRestoreResult
from trading_core.coordination.paper_audit import PaperAuditRepository, PaperDecisionReplay

__all__ = [
    "ActionContext",
    "DecisionLifecycleEvent",
    "DecisionProjector",
    "DecisionReadModel",
    "DecisionEventSourcingWriter",
    "DecisionRecord",
    "EventLedger",
    "EventRecord",
    "EventSourcingWriteResult",
    "EventStream",
    "ExternalBusMessage",
    "DurableBusUnavailableError",
    "InMemoryExternalBusAdapter",
    "RedisStreamsExternalBusAdapter",
    "IdempotencyCache",
    "StreamReplayCursor",
    "RuntimeSnapshot",
    "SnapshotReplayEngine",
    "SnapshotRestoreResult",
    "PaperAuditRepository",
    "PaperDecisionReplay",
    "decision_lifecycle_events",
    "lifecycle_events_from_records",
    "replay_decision_events",
    "DECISION_EVENT",
    "EXECUTION_EVENT",
    "PORTFOLIO_EVENT",
    "META_EVENT",
    "REFLECTION_EVENT",
    "RISK_EVENT",
]

