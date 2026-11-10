import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

TRADE_SIGNAL_EVENT = "trade.signal.v1"
TRADE_COMMAND_EVENT = "trade.command.v1"
TRADER_STATUS_EVENT = "trade.status.v1"
CORE_HEARTBEAT_EVENT = "core.heartbeat.v1"

ALLOWED_TRADE_COMMANDS = {"stop", "flatten", "block", "resume"}
ALLOWED_INTERNAL_TRADE_COMMANDS = ALLOWED_TRADE_COMMANDS | {"limit_order"}


def _default_ts() -> float:
    return time.time()


def trade_signal_channel(redis_prefix: str) -> str:
    return f"{redis_prefix}events:trade_signal"


def trade_signal_stream_key(redis_prefix: str) -> str:
    return f"{redis_prefix}streams:trade_signal"


def trade_command_queue_key(redis_prefix: str) -> str:
    return f"{redis_prefix}events:trade_command_queue"


def trader_status_key(redis_prefix: str) -> str:
    return f"{redis_prefix}events:trader_status"


def core_heartbeat_key(redis_prefix: str) -> str:
    return f"{redis_prefix}events:core_heartbeat"


@dataclass(frozen=True)
class EventEnvelope:
    event: str
    source: str
    payload: Dict[str, Any]
    ts: float = field(default_factory=_default_ts)
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "source": self.source,
            "ts": float(self.ts),
            "version": int(self.version),
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def decode_envelope(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
    elif isinstance(raw, dict):
        data = raw
    else:
        return None
    if not isinstance(data, dict):
        return None
    return data


def build_trade_signal_event(cmd: str, source: str, reason: str = "", user_id: str = "default") -> EventEnvelope:
    normalized_cmd = str(cmd or "").lower().strip()
    if normalized_cmd not in ALLOWED_TRADE_COMMANDS:
        raise ValueError(f"Unsupported trade signal cmd: {cmd}")
    return EventEnvelope(
        event=TRADE_SIGNAL_EVENT,
        source=str(source or "unknown"),
        payload={
            "cmd": normalized_cmd,
            "reason": str(reason or ""),
            "user_id": str(user_id or "default"),
        },
    )


def parse_trade_signal_event(raw: Any) -> Optional[Dict[str, Any]]:
    data = decode_envelope(raw)
    if not data:
        return None

    if data.get("event") == TRADE_SIGNAL_EVENT and isinstance(data.get("payload"), dict):
        payload = data["payload"]
        cmd = str(payload.get("cmd") or "").lower().strip()
        if cmd not in ALLOWED_TRADE_COMMANDS:
            return None
        return {
            "cmd": cmd,
            "source": str(data.get("source") or "unknown"),
            "reason": str(payload.get("reason") or ""),
            "user_id": str(payload.get("user_id") or "default"),
            "ts": float(data.get("ts") or time.time()),
        }

    # Backward compatibility with legacy payload.
    cmd = str(data.get("cmd") or "").lower().strip()
    if cmd in ALLOWED_TRADE_COMMANDS:
        return {
            "cmd": cmd,
            "source": str(data.get("source") or "unknown"),
            "reason": str(data.get("reason") or ""),
            "user_id": str(data.get("user_id") or "default"),
            "ts": float(data.get("ts") or time.time()),
        }
    return None


def build_trade_command_message(
    cmd: str,
    source: str,
    reason: str = "",
    *,
    priority: int = 1,
    user_id: str = "default",
    command_id: str = "",
    decision_id: str = "",
    execution_id: str = "",
) -> Dict[str, Any]:
    normalized_cmd = str(cmd or "").lower().strip()
    if normalized_cmd not in ALLOWED_TRADE_COMMANDS:
        raise ValueError(f"Unsupported trade command cmd: {cmd}")
    resolved_command_id = str(command_id or "").strip() or uuid.uuid4().hex
    resolved_execution_id = str(execution_id or "").strip() or resolved_command_id
    payload = {
        "cmd": normalized_cmd,
        "reason": str(reason or ""),
        "priority": int(priority),
        "user_id": str(user_id or "default"),
        "command_id": resolved_command_id,
        "execution_id": resolved_execution_id,
    }
    resolved_decision_id = str(decision_id or "").strip()
    if resolved_decision_id:
        payload["decision_id"] = resolved_decision_id
    env = EventEnvelope(
        event=TRADE_COMMAND_EVENT,
        source=str(source or "unknown"),
        payload=payload,
    )
    return env.to_dict()


def parse_trade_command_message(raw: Any) -> Optional[Dict[str, Any]]:
    def _safe_int(value: Any, default: int = 99) -> int:
        try:
            return int(value)
        except Exception:
            try:
                return int(float(value))
            except Exception:
                return int(default)

    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _with_optional_order_fields(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        for field in ("symbol", "side", "amount", "price", "command_id", "decision_id", "execution_id"):
            if field in source:
                target[field] = source.get(field)
        return target

    data = decode_envelope(raw)
    if not data:
        return None

    if data.get("event") == TRADE_COMMAND_EVENT and isinstance(data.get("payload"), dict):
        payload = data["payload"]
        cmd = str(payload.get("cmd") or "").lower().strip()
        if cmd not in ALLOWED_INTERNAL_TRADE_COMMANDS:
            return None
        out = {
            "cmd": cmd,
            "source": str(data.get("source") or "unknown"),
            "reason": str(payload.get("reason") or ""),
            "priority": _safe_int(payload.get("priority"), 99),
            "user_id": str(payload.get("user_id") or "default"),
            "ts": _safe_float(data.get("ts"), time.time()),
        }
        return _with_optional_order_fields(out, payload)

    # Backward compatibility with legacy command dict.
    cmd = str(data.get("cmd") or "").lower().strip()
    if cmd in ALLOWED_INTERNAL_TRADE_COMMANDS:
        out = {
            "cmd": cmd,
            "source": str(data.get("source") or "unknown"),
            "reason": str(data.get("reason") or ""),
            "priority": _safe_int(data.get("priority"), 99),
            "user_id": str(data.get("user_id") or "default"),
            "ts": _safe_float(data.get("ts"), time.time()),
        }
        return _with_optional_order_fields(out, data)
    return None


def build_core_heartbeat_event(pid: int) -> Dict[str, Any]:
    return EventEnvelope(
        event=CORE_HEARTBEAT_EVENT,
        source="elara-core",
        payload={"pid": int(pid)},
    ).to_dict()

