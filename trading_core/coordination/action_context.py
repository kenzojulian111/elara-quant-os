from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from trading_core.data_contracts import DecisionTraceStep, ReasonedDecision


@dataclass(frozen=True)
class ActionContext:
    action_id: str
    created_at: str
    source: str

    @classmethod
    def create(cls, source: str) -> "ActionContext":
        return cls(
            action_id=uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(),
            source=str(source or "system"),
        )


@dataclass(frozen=True)
class DecisionRecord:
    action_id: str
    strategy_id: str
    symbol: str
    decision: str
    confidence: float
    created_at: str
    trace_id: str = ""
    status: str = "observed"
    reasons: tuple[str, ...] = ()
    pipeline_trace: tuple[DecisionTraceStep, ...] = ()
    risk_approved: bool = False
    risk_reason: str = ""
    governance_approved: bool = False
    governance_mode: str = ""
    governance_reason: str = ""
    runtime_mode: str = ""
    allocation_weight: float = 0.0
    current_drawdown_pct: float = 0.0
    reasoned_decision: ReasonedDecision | None = None

