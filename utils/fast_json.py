from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


try:
    import orjson as _orjson
except Exception:
    _orjson = None

try:
    import msgspec as _msgspec
except Exception:
    _msgspec = None


def backend_name() -> str:
    if _orjson is not None:
        return "orjson"
    if _msgspec is not None:
        return "msgspec"
    return "json"


def _default(value: Any):
    if is_dataclass(value):
        # asdict expects a dataclass instance, not the dataclass type
        if isinstance(value, type):
            raise TypeError("dataclass type is not JSON serializable; provide an instance")
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps_bytes(value: Any) -> bytes:
    if _orjson is not None:
        return _orjson.dumps(value, default=_default)
    if _msgspec is not None:
        return _msgspec.json.encode(_json_safe(value))
    return json.dumps(_json_safe(value), ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def dumps_text(value: Any) -> str:
    return dumps_bytes(value).decode("utf-8")


def loads(value: bytes | str | bytearray | memoryview) -> Any:
    if isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = bytes(value)
    if _orjson is not None:
        return _orjson.loads(raw)
    if _msgspec is not None:
        return _msgspec.json.decode(raw)
    return json.loads(raw.decode("utf-8"))


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        # asdict expects a dataclass instance, not the dataclass type
        if isinstance(value, type):
            raise TypeError("dataclass type is not JSON serializable; provide an instance")
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
