import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict


def _trace_context() -> Dict[str, str]:
    app_context: Dict[str, str] = {}
    try:
        from utils.observability import current_observability_context

        app_context.update(current_observability_context())
    except Exception:
        pass
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if not span:
            return app_context
        span_context = span.get_span_context()
        if not span_context or not getattr(span_context, "is_valid", False):
            return app_context
        otel_context = {
            "trace_id": format(span_context.trace_id, "032x"),
            "span_id": format(span_context.span_id, "016x"),
        }
        return {**app_context, **otel_context}
    except Exception:
        return app_context


class JsonLogFormatter(logging.Formatter):
    """JSON formatter for structured application logs."""

    def format(self, record: logging.LogRecord) -> str:
        message = str(record.getMessage() or "").replace("\r", " ").replace("\n", " ").strip()
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "module": record.module,
            "line": record.lineno,
        }
        payload.update(_trace_context())

        audit = getattr(record, "audit", None)
        if isinstance(audit, dict):
            payload["audit"] = audit

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_root_logging_json(enabled: bool):
    if not enabled:
        return

    root = logging.getLogger()
    formatter = JsonLogFormatter()

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
        return

    for handler in root.handlers:
        handler.setFormatter(formatter)


def emit_audit_log(logger: logging.Logger, event: str, **fields):
    sensitive_markers = {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
    }

    def _redact_value(key: str, value: Any):
        key_norm = str(key or "").strip().lower()
        if any(marker in key_norm for marker in sensitive_markers):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): _redact_value(str(k), v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact_value(key_norm, item) for item in value]
        if isinstance(value, tuple):
            return tuple(_redact_value(key_norm, item) for item in value)
        return value

    payload = {"event": str(event or "").strip() or "unknown"}
    for key, value in fields.items():
        payload[str(key)] = _redact_value(str(key), value)
    logger.info("audit", extra={"audit": payload})

