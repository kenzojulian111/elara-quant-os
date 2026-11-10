import json
import logging
import re
from typing import Any


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(password|pass|api[_-]?key|token|secret|authorization|bearer|private[_-]?key)\b"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|pass|api[_-]?key|token|secret|authorization|bearer|private[_-]?key)\b"
    r"\s*([:=])\s*([\"']?)([^,;\s\"'}]+)([\"']?)"
)
_JSON_KV_PATTERN = re.compile(
    r'(?i)("?(password|pass|api[_-]?key|token|secret|authorization|bearer|private[_-]?key)"?\s*:\s*)'
    r'(".*?"|\'.*?\'|[^,}\s]+)'
)
_API_KEY_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9\-_]{10,}|gsk_[A-Za-z0-9\-_]{10,}|AIza[A-Za-z0-9\-_]{10,})"
)


def _sanitize_crlf(value: str) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")


def _redact_text(value: str) -> str:
    text = _sanitize_crlf(value)
    text = _API_KEY_PATTERN.sub("[REDACTED_KEY]", text)
    text = _ASSIGNMENT_PATTERN.sub(r"\1\2*****", text)
    text = _JSON_KV_PATTERN.sub(r"\1\"*****\"", text)
    return text


def _redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key or "")
            if _SENSITIVE_KEY_PATTERN.search(key_text):
                out[key] = "*****"
            else:
                out[key] = _redact_obj(item)
        return out
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_obj(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


class SensitiveDataFilter(logging.Filter):
    """
    Redacts obvious secrets and sanitizes CRLF in log records.
    Applied globally to reduce accidental key/token leakage.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _redact_text(record.msg)
            else:
                record.msg = _redact_obj(record.msg)

            if record.args:
                if isinstance(record.args, dict):
                    record.args = _redact_obj(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(_redact_obj(item) for item in record.args)
                else:
                    record.args = _redact_obj(record.args)

            if hasattr(record, "exc_text") and record.exc_text:
                record.exc_text = _redact_text(record.exc_text)
        except Exception:
            return True
        return True


def attach_redaction_filter(logger: logging.Logger) -> None:
    existing = [f for f in getattr(logger, "filters", []) if isinstance(f, SensitiveDataFilter)]
    if existing:
        return
    logger.addFilter(SensitiveDataFilter())


def scrub_json_text(text: str) -> str:
    """
    Best-effort redaction helper for explicit JSON payload logging.
    """
    raw = str(text or "")
    try:
        parsed = json.loads(raw)
        return json.dumps(_redact_obj(parsed), ensure_ascii=False)
    except Exception:
        return _redact_text(raw)

