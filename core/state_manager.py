from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from config.settings import Config
from core.exceptions import StateBackendUnavailableError, StateEncryptionError
from utils import redis_helper


def _normalize_user_id(user_id: str | None) -> str:
    if user_id is None:
        return "default"
    return str(user_id)


def _safe_json_load(path: str) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_json_dump(path: str, payload: dict[str, Any]) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _xor_crypt(raw: str, secret: str) -> str:
    key = hashlib.sha256(str(secret or "").encode("utf-8")).digest()
    data = raw.encode("utf-8")
    out = bytes(data[idx] ^ key[idx % len(key)] for idx in range(len(data)))
    return base64.b64encode(out).decode("ascii")


def _xor_decrypt(raw: str, secret: str) -> str:
    key = hashlib.sha256(str(secret or "").encode("utf-8")).digest()
    data = base64.b64decode(raw.encode("ascii"))
    out = bytes(data[idx] ^ key[idx % len(key)] for idx in range(len(data)))
    return out.decode("utf-8")


class StateManager:
    def __init__(self, db_path: str | None = None, save_delay: float = 0.0):
        self.db_path = str(db_path or getattr(Config, "STATE_DB_PATH", "") or "").strip()
        if not self.db_path:
            self.db_path = str(
                (Path(getattr(Config, "root_path", os.getcwd())) / "runtime" / "state_memory.json").resolve()
            )
        self.save_delay = float(save_delay or 0.0)
        self._lock = threading.RLock()
        self._memory: dict[str, dict[str, dict[str, Any]]] = {}
        self._backend = str(getattr(Config, "STATE_BACKEND", "file") or "file").strip().lower()
        self.redis_client = None
        self._fallback_mode = str(getattr(Config, "STATE_FALLBACK_MODE", "memory") or "memory").strip().lower()
        if self._fallback_mode not in {"memory", "file", "none"}:
            self._fallback_mode = "memory"
        self._encryption_enabled = bool(getattr(Config, "STATE_ENCRYPTION_ENABLED", False))
        self._encryption_key = str(getattr(Config, "STATE_ENCRYPTION_KEY", "") or "")
        self._encryption_required = bool(getattr(Config, "STATE_ENCRYPTION_REQUIRED", False))
        if self._encryption_enabled and not self._encryption_key and self._encryption_required:
            raise StateEncryptionError("STATE_ENCRYPTION_KEY is required when encryption is enabled.")
        if self._fallback_mode == "file":
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            if not os.path.exists(self.db_path):
                _safe_json_dump(self.db_path, {})
        self._initialize_backend()

    def _initialize_backend(self) -> None:
        backend = self._backend
        if backend == "memory":
            return
        if backend == "file":
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            if not os.path.exists(self.db_path):
                _safe_json_dump(self.db_path, {})
            return
        if backend == "redis":
            client = redis_helper.get_redis()
            if client is not None:
                self.redis_client = client
                return
            fail_fast = bool(getattr(Config, "STATE_BACKEND_FAIL_FAST", False))
            fallback = self._fallback_mode
            if fail_fast or fallback == "none":
                raise StateBackendUnavailableError("Redis state backend unavailable.")
            self._backend = fallback if fallback in {"memory", "file"} else "memory"
            self._initialize_backend()
            return
        self._backend = "memory"

    def _encode_value(self, value: Any, ttl: float | None = None) -> dict[str, Any]:
        expires_at = time.time() + float(ttl) if ttl else None
        if self._encryption_enabled:
            raw_json = json.dumps(value, ensure_ascii=True)
            return {
                "encrypted": True,
                "payload": _xor_crypt(raw_json, self._encryption_key),
                "expires_at": expires_at,
            }
        return {
            "encrypted": False,
            "payload": value,
            "expires_at": expires_at,
        }

    def _decode_value(self, record: dict[str, Any]) -> Any:
        if not isinstance(record, dict):
            return None
        expires_at = record.get("expires_at")
        if expires_at is not None and float(expires_at) <= time.time():
            return None
        if not record.get("encrypted"):
            return record.get("payload")
        try:
            raw_json = _xor_decrypt(str(record.get("payload") or ""), self._encryption_key)
            return json.loads(raw_json)
        except Exception as exc:
            raise StateEncryptionError(f"Failed to decrypt state payload: {exc}") from exc

    def _file_store(self) -> dict[str, Any]:
        return _safe_json_load(self.db_path)

    def _write_file_store(self, data: dict[str, Any]) -> None:
        _safe_json_dump(self.db_path, data)

    def _ensure_redis(self):
        if self._backend != "redis":
            return None
        if self.redis_client is None:
            self.redis_client = redis_helper.get_redis()
        return self.redis_client

    def _fallback_backend(self) -> str | None:
        if self._fallback_mode in {"memory", "file"}:
            return self._fallback_mode
        return None

    def _fallback_set(self, user_text: str, key_text: str, record: dict[str, Any]) -> bool:
        fallback = self._fallback_backend()
        if fallback == "memory":
            self._memory.setdefault(user_text, {})[key_text] = record
            return True
        if fallback == "file":
            data = self._file_store()
            data.setdefault(user_text, {})[key_text] = record
            self._write_file_store(data)
            return True
        return False

    def _fallback_get(self, user_text: str, key_text: str):
        fallback = self._fallback_backend()
        if fallback == "memory":
            record = self._memory.get(user_text, {}).get(key_text)
            value = self._decode_value(record) if record else None
            if value is None and record:
                self._memory.get(user_text, {}).pop(key_text, None)
            return value
        if fallback == "file":
            data = self._file_store()
            record = data.get(user_text, {}).get(key_text)
            value = self._decode_value(record) if record else None
            if value is None and record:
                data.get(user_text, {}).pop(key_text, None)
                self._write_file_store(data)
            return value
        return None

    def _fallback_delete(self, user_text: str, key_text: str) -> bool:
        fallback = self._fallback_backend()
        if fallback == "memory":
            self._memory.get(user_text, {}).pop(key_text, None)
            return True
        if fallback == "file":
            data = self._file_store()
            data.get(user_text, {}).pop(key_text, None)
            self._write_file_store(data)
            return True
        return False

    def _fallback_items(self, user_text: str) -> dict[str, Any]:
        fallback = self._fallback_backend()
        if fallback == "memory":
            return dict(self._memory.get(user_text, {}))
        if fallback == "file":
            return dict(self._file_store().get(user_text, {}))
        return {}

    def _redis_key(self, user_id: str, key: str) -> str:
        prefix = str(getattr(Config, "STATE_REDIS_PREFIX", "elara:") or "elara:")
        return f"{prefix}state:{user_id}:{key}"

    def set_state(self, key, value, user_id="default", ttl=None):
        key_text = str(key or "").strip()
        if not key_text:
            return False
        user_text = _normalize_user_id(user_id)
        record = self._encode_value(value, ttl)
        with self._lock:
            if self._backend == "memory":
                self._memory.setdefault(user_text, {})[key_text] = record
                return True
            if self._backend == "file":
                data = self._file_store()
                data.setdefault(user_text, {})[key_text] = record
                self._write_file_store(data)
                return True
            if self._backend == "redis":
                redis_client = self._ensure_redis()
                ok = redis_helper.redis_safe_call(
                    redis_client,
                    "set",
                    self._redis_key(user_text, key_text),
                    json.dumps(record),
                    default=False,
                    purpose="state_set",
                )
                if ok:
                    return True
                return self._fallback_set(user_text, key_text, record)
        return False

    def get_state(self, key, user_id="default"):
        key_text = str(key or "").strip()
        if not key_text:
            return None
        user_text = _normalize_user_id(user_id)
        with self._lock:
            if self._backend == "memory":
                record = self._memory.get(user_text, {}).get(key_text)
                value = self._decode_value(record) if record else None
                if value is None and record:
                    self._memory.get(user_text, {}).pop(key_text, None)
                return value
            if self._backend == "file":
                data = self._file_store()
                record = data.get(user_text, {}).get(key_text)
                value = self._decode_value(record) if record else None
                if value is None and record:
                    data.get(user_text, {}).pop(key_text, None)
                    self._write_file_store(data)
                return value
            if self._backend == "redis":
                redis_client = self._ensure_redis()
                if not redis_client:
                    return self._fallback_get(user_text, key_text)
                missing = object()
                raw = redis_helper.redis_safe_call(
                    redis_client,
                    "get",
                    self._redis_key(user_text, key_text),
                    default=missing,
                    purpose="state_get",
                )
                if raw is missing:
                    return self._fallback_get(user_text, key_text)
                if raw is None:
                    return None
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                record = json.loads(raw)
                value = self._decode_value(record)
                if value is None:
                    redis_helper.redis_safe_call(
                        redis_client,
                        "delete",
                        self._redis_key(user_text, key_text),
                        default=0,
                        purpose="state_get_cleanup",
                    )
                return value
        return None

    def delete_state(self, key, user_id="default"):
        key_text = str(key or "").strip()
        user_text = _normalize_user_id(user_id)
        with self._lock:
            if self._backend == "memory":
                self._memory.get(user_text, {}).pop(key_text, None)
                return True
            if self._backend == "file":
                data = self._file_store()
                data.get(user_text, {}).pop(key_text, None)
                self._write_file_store(data)
                return True
            if self._backend == "redis":
                redis_client = self._ensure_redis()
                ok = redis_helper.redis_safe_call(
                    redis_client,
                    "delete",
                    self._redis_key(user_text, key_text),
                    default=0,
                    purpose="state_delete",
                )
                if ok:
                    return True
                return self._fallback_delete(user_text, key_text)
        return False

    def dump(self, user_id="default") -> dict[str, Any]:
        user_text = _normalize_user_id(user_id)
        out: dict[str, Any] = {}
        with self._lock:
            if self._backend == "memory":
                items = dict(self._memory.get(user_text, {}))
            elif self._backend == "file":
                items = dict(self._file_store().get(user_text, {}))
            elif self._backend == "redis":
                items = {}
                redis_client = self._ensure_redis()
                prefix = self._redis_key(user_text, "")
                if redis_client:
                    for redis_key in redis_client.scan_iter(match=f"{prefix}*"):
                        raw = redis_helper.redis_safe_call(
                            redis_client,
                            "get",
                            redis_key,
                            default=None,
                            purpose="state_dump",
                        )
                        if raw is None:
                            continue
                        if isinstance(raw, (bytes, bytearray)):
                            raw = raw.decode("utf-8", errors="replace")
                        try:
                            items[str(redis_key).replace(prefix, "", 1)] = json.loads(raw)
                        except Exception:
                            continue
                else:
                    items = self._fallback_items(user_text)
            else:
                items = {}

        for key_text, record in items.items():
            value = self._decode_value(record) if isinstance(record, dict) else None
            if value is not None:
                out[str(key_text)] = value
        return out

    def shutdown(self):
        with self._lock:
            if self.redis_client is not None:
                try:
                    self.redis_client.close()
                except Exception:
                    pass

