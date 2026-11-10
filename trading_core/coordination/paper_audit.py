from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from trading_core.coordination.event_ledger import EventLedger


@dataclass(frozen=True)
class PaperDecisionReplay:
    trace_id: str
    action_id: str
    integrity_ok: bool
    integrity_reason: str
    market_data: Mapping[str, object] | None
    decision: Mapping[str, object] | None
    risk: Mapping[str, object] | None
    governance: Mapping[str, object] | None
    execution: Mapping[str, object] | None

    @property
    def found(self) -> bool:
        return self.decision is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "action_id": self.action_id,
            "found": self.found,
            "integrity_ok": self.integrity_ok,
            "integrity_reason": self.integrity_reason,
            "market_data": self.market_data,
            "decision": self.decision,
            "risk": self.risk,
            "governance": self.governance,
            "execution": self.execution,
        }


class PaperAuditRepository:
    """Reconstructs one paper decision from the append-only audit ledger."""

    def __init__(self, ledger: EventLedger):
        self.ledger = ledger

    def replay(self, trace_id: str) -> PaperDecisionReplay:
        trace_id = str(trace_id or "").strip()
        integrity_ok, integrity_reason = self.ledger.verify_hash_chain()
        records = tuple(self.ledger.read())
        decision_record = next(
            (
                record
                for record in records
                if record.get("event_type") == "decision" and str(record.get("trace_id") or "") == trace_id
            ),
            None,
        )
        decision = _payload(decision_record)
        action_id = str(decision.get("action_id") or "")

        def related(event_type: str) -> Mapping[str, object] | None:
            record = next(
                (
                    candidate
                    for candidate in records
                    if candidate.get("event_type") == event_type
                    and (
                        str(candidate.get("trace_id") or "") == trace_id
                        or (action_id and str(candidate.get("correlation_id") or "") == action_id)
                    )
                ),
                None,
            )
            return _payload(record) or None

        return PaperDecisionReplay(
            trace_id=trace_id,
            action_id=action_id,
            integrity_ok=integrity_ok,
            integrity_reason=integrity_reason,
            market_data=related("market_data"),
            decision=decision or None,
            risk=related("risk"),
            governance=related("governance"),
            execution=related("execution"),
        )

    def recent_rows(self, limit: int = 20) -> list[dict[str, object]]:
        """Return the compact shape consumed by the primary decision table."""
        limit = max(1, int(limit))
        records = tuple(self.ledger.read())
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        for record in reversed(records):
            if record.get("event_type") != "decision":
                continue
            payload = _payload(record)
            trace_id = str(record.get("trace_id") or payload.get("trace_id") or "")
            if not trace_id or trace_id in seen:
                continue
            seen.add(trace_id)
            replay = self.replay(trace_id)
            reasons = tuple(payload.get("reasons") or ())
            receipt_status = str((replay.execution or {}).get("status") or "")
            reason_text = "; ".join(str(reason) for reason in reasons[:3]) or "-"
            execution_id = str((replay.execution or {}).get("execution_id") or "")
            audit_ids = f"trace={trace_id}" + (f" execution={execution_id}" if execution_id else "")
            rows.append(
                {
                    "entry_time": payload.get("created_at") or record.get("timestamp") or "",
                    "symbol": payload.get("symbol") or "",
                    "direction": payload.get("decision") or "",
                    "status": receipt_status or payload.get("status") or "observed",
                    "entry_reason": f"{reason_text} | {audit_ids}",
                    "trace_id": trace_id,
                    "execution_id": execution_id,
                    "mode": (replay.execution or {}).get("mode") or "paper",
                }
            )
            if len(rows) >= limit:
                break
        return rows


def _payload(record: Mapping[str, object] | None) -> Mapping[str, object]:
    if not record:
        return {}
    payload = record.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}
