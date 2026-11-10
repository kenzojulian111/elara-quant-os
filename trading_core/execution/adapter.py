from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.fast_json import dumps_text, loads

from trading_core.coordination.action_context import ActionContext
from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.event_types import EXECUTION_EVENT
from trading_core.data_contracts import ExecutionPlan


@dataclass(frozen=True)
class ExecutionReceipt:
    execution_id: str
    action_id: str
    trace_id: str
    strategy_id: str
    symbol: str
    direction: str
    entry_price: float
    size_pct: float
    stop_loss: float
    take_profit: Optional[float]
    status: str
    mode: str
    detail: str
    timestamp: str


class ExecutionAdapter:
    def execute(self, plan: ExecutionPlan, context: ActionContext) -> ExecutionReceipt:
        raise NotImplementedError


class ShadowExecutionAdapter(ExecutionAdapter):
    def __init__(self, ledger: EventLedger | None = None):
        self.ledger = ledger

    def execute(self, plan: ExecutionPlan, context: ActionContext) -> ExecutionReceipt:
        receipt = ExecutionReceipt(
            execution_id=_execution_id(plan, context),
            action_id=context.action_id,
            trace_id=plan.trace_id,
            strategy_id=plan.strategy_id,
            symbol=plan.symbol,
            direction=plan.direction,
            entry_price=plan.entry_price,
            size_pct=plan.size_pct,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            status="shadowed",
            mode="shadow",
            detail="order logged (shadow mode)",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if self.ledger:
            self.ledger.append_dataclass(EXECUTION_EVENT, receipt)
        return receipt


class LiveExecutionAdapter(ExecutionAdapter):
    def __init__(self, *, allow_live: bool = False, ledger: EventLedger | None = None):
        self.allow_live = bool(allow_live)
        self.ledger = ledger

    def execute(self, plan: ExecutionPlan, context: ActionContext) -> ExecutionReceipt:
        if not self.allow_live:
            receipt = ExecutionReceipt(
                execution_id=_execution_id(plan, context),
                action_id=context.action_id,
                trace_id=plan.trace_id,
                strategy_id=plan.strategy_id,
                symbol=plan.symbol,
                direction=plan.direction,
                entry_price=plan.entry_price,
                size_pct=plan.size_pct,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                status="blocked",
                mode="live",
                detail="live execution disabled",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            if self.ledger:
                self.ledger.append_dataclass(EXECUTION_EVENT, receipt)
            return receipt
        raise NotImplementedError("Live execution adapter not configured.")


class PaperExecutionAdapter(ExecutionAdapter):
    """Deterministic, durable paper fills with restart-safe deduplication.

    This adapter never sends an exchange order. It records a fill at the plan's
    entry price and returns the already-recorded receipt if the same
    ``execution_id`` is delivered again after a restart.
    """

    SCHEMA = "elara.paper_execution.v1"

    def __init__(self, state_path: str, *, ledger: EventLedger | None = None):
        self.state_path = Path(state_path)
        self.ledger = ledger

    def execute(self, plan: ExecutionPlan, context: ActionContext) -> ExecutionReceipt:
        execution_id = _execution_id(plan, context)
        receipts = self._load_receipts()
        existing = receipts.get(execution_id)
        if existing:
            return ExecutionReceipt(**existing)

        receipt = ExecutionReceipt(
            execution_id=execution_id,
            action_id=context.action_id,
            trace_id=plan.trace_id,
            strategy_id=plan.strategy_id,
            symbol=plan.symbol,
            direction=plan.direction,
            entry_price=plan.entry_price,
            size_pct=plan.size_pct,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            status="paper_filled",
            mode="paper",
            detail="paper order filled at planned entry price",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        receipts[execution_id] = {
            "execution_id": receipt.execution_id,
            "action_id": receipt.action_id,
            "trace_id": receipt.trace_id,
            "strategy_id": receipt.strategy_id,
            "symbol": receipt.symbol,
            "direction": receipt.direction,
            "entry_price": receipt.entry_price,
            "size_pct": receipt.size_pct,
            "stop_loss": receipt.stop_loss,
            "take_profit": receipt.take_profit,
            "status": receipt.status,
            "mode": receipt.mode,
            "detail": receipt.detail,
            "timestamp": receipt.timestamp,
        }
        self._save_receipts(receipts)
        if self.ledger:
            self.ledger.append_dataclass(EXECUTION_EVENT, receipt)
        return receipt

    def _load_receipts(self) -> dict[str, dict]:
        if not self.state_path.exists():
            return {}
        try:
            payload = loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict) or payload.get("schema") != self.SCHEMA:
            return {}
        receipts = payload.get("receipts")
        return dict(receipts) if isinstance(receipts, dict) else {}

    def _save_receipts(self, receipts: dict[str, dict]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": self.SCHEMA, "receipts": receipts}
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(dumps_text(payload), encoding="utf-8")
        temporary.replace(self.state_path)


def _execution_id(plan: ExecutionPlan, context: ActionContext) -> str:
    return str(plan.execution_id or plan.trace_id or context.action_id)

