import hashlib
import hmac
import json
import logging
import os
import threading
import time
from uuid import uuid4
from typing import Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from config.settings import Config
from core.events import ALLOWED_TRADE_COMMANDS, build_trade_command_message, trade_command_queue_key
from utils.json_logging import configure_root_logging_json, emit_audit_log
from utils.redis_helper import get_redis

log = logging.getLogger("elara.webhook.fastapi")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
configure_root_logging_json(bool(getattr(Config, "LOG_JSON", False)))

app = FastAPI(title="Elara Webhook Gateway", version="1.0.0")


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if bool(getattr(Config, "WEB_FORCE_HTTPS", False)):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

if bool(getattr(Config, "OTEL_ENABLED", False)):
    try:
        from utils.opentelemetry_tracing import install_fastapi_tracing

        install_fastapi_tracing(
            app,
            service_name=str(getattr(Config, "OTEL_SERVICE_NAME", "elara-webhook-fastapi") or "elara-webhook-fastapi"),
        )
    except Exception as e:
        log.warning(f"Failed to install FastAPI OpenTelemetry tracing: {e}")

_idemp_cache: Dict[str, float] = {}
_idemp_lock = threading.Lock()
_rate_cache: Dict[str, Dict[str, float]] = {}
_rate_lock = threading.Lock()


class TradingWebhookPayload(BaseModel):
    cmd: str = Field(..., description="stop|flatten|block|resume")
    reason: str = Field("", max_length=500)
    user_id: str = Field("webhook", max_length=64)
    source: str = Field("webhook", max_length=64)


def _cleanup_memory_idempotency(now: float):
    stale = [k for k, expires in _idemp_cache.items() if expires <= now]
    for key in stale:
        _idemp_cache.pop(key, None)
    memory_cap = max(256, int(getattr(Config, "WEBHOOK_IDEMPOTENCY_MEMORY_MAX", 5000)))
    if len(_idemp_cache) > memory_cap:
        # Emergency path: clear fast to avoid CPU-heavy sort under lock during traffic spikes.
        _idemp_cache.clear()


def _is_duplicate_request(idempotency_key: str) -> bool:
    key = str(idempotency_key or "").strip()
    if not key:
        return False
    max_len = max(24, int(getattr(Config, "WEBHOOK_IDEMPOTENCY_KEY_MAX_LEN", 128)))
    if len(key) > max_len:
        raise ValueError(f"idempotency_key too long (>{max_len})")

    ttl = max(10, int(getattr(Config, "WEBHOOK_IDEMPOTENCY_TTL_SEC", 120)))
    redis_client = get_redis()
    if redis_client:
        try:
            redis_key = f"{getattr(Config, 'REDIS_PREFIX', 'elara:')}webhook:idempotency:{key}"
            created = redis_client.set(redis_key, "1", nx=True, ex=ttl)
            return not bool(created)
        except Exception as e:
            log.warning(f"Webhook idempotency Redis error, fallback memory cache: {e}")

    now = time.time()
    with _idemp_lock:
        _cleanup_memory_idempotency(now)
        expiry = _idemp_cache.get(key)
        if expiry and expiry > now:
            return True
        _idemp_cache[key] = now + ttl
    return False


def _is_rate_limited(client_ip: str) -> bool:
    ip = str(client_ip or "").strip() or "unknown"
    limit = max(1, int(getattr(Config, "WEBHOOK_RATE_LIMIT_PER_MIN", 120)))
    window_sec = max(10, int(getattr(Config, "WEBHOOK_RATE_LIMIT_WINDOW_SEC", 60)))
    redis_client = get_redis()
    if redis_client:
        try:
            bucket = int(time.time() // window_sec)
            key = f"{getattr(Config, 'REDIS_PREFIX', 'elara:')}webhook:rl:{ip}:{bucket}"
            total = int(redis_client.incr(key))
            if total == 1:
                redis_client.expire(key, window_sec + 2)
            return total > limit
        except Exception as e:
            log.warning(f"Webhook rate-limit Redis error, fallback memory cache: {e}")

    now = time.time()
    with _rate_lock:
        memory_cap = max(1000, int(getattr(Config, "WEBHOOK_RATE_LIMIT_MEMORY_MAX", 10000)))
        if len(_rate_cache) > memory_cap:
            # Fail-safe under attack: trade precision for latency and keep lock hold-time tiny.
            _rate_cache.clear()
        else:
            stale = [
                k for k, payload in _rate_cache.items()
                if float((payload or {}).get("exp") or 0.0) <= now
            ]
            for key in stale:
                _rate_cache.pop(key, None)

        bucket = int(now // window_sec)
        key = f"{ip}:{bucket}"
        payload = _rate_cache.get(key) or {"count": 0.0, "exp": now + window_sec + 1}
        payload["count"] = float(payload.get("count", 0.0)) + 1.0
        payload["exp"] = now + window_sec + 1
        _rate_cache[key] = payload
        return int(payload["count"]) > limit


def _validate_signature(raw_body: bytes, provided_signature: str) -> bool:
    secret = str(getattr(Config, "WEBHOOK_SHARED_SECRET", "") or "").strip()
    allow_unsigned = bool(getattr(Config, "WEBHOOK_ALLOW_UNSIGNED", False))
    if not secret:
        return allow_unsigned
    if not provided_signature:
        return False

    sig = str(provided_signature or "").strip().lower()
    if sig.startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest().lower()
    return hmac.compare_digest(expected, sig)


def _enqueue_trade_command(cmd: str, source: str, reason: str, user_id: str) -> Optional[str]:
    payload = build_trade_command_message(
        cmd=cmd,
        source=source,
        reason=reason,
        user_id=user_id,
        priority=1,
    )

    redis_client = get_redis()
    redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
    queue_key = str(
        getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
        or trade_command_queue_key(redis_prefix)
    ).strip()

    if redis_client:
        try:
            redis_client.lpush(queue_key, json.dumps(payload))
            return "redis"
        except Exception as e:
            log.warning(f"Webhook enqueue Redis failed: {e}")

    if not bool(getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True)):
        return None

    command_path = str(
        getattr(Config, "TRADING_COMMAND_PATH", os.path.join(getattr(Config, "root_path", "."), "runtime", "trading_command.json"))
        or ""
    ).strip()
    if not command_path:
        return None

    tmp_path = f"{command_path}.{uuid4().hex}.tmp"
    try:
        parent_dir = os.path.dirname(command_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, command_path)
        return "file"
    except Exception as e:
        log.error(f"Webhook enqueue file fallback failed: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return None


def _process_webhook(payload: TradingWebhookPayload):
    cmd = str(payload.cmd or "").strip().lower()
    if cmd not in ALLOWED_TRADE_COMMANDS:
        raise ValueError(f"unsupported webhook cmd: {cmd}")
    transport = _enqueue_trade_command(
        cmd=cmd,
        source=str(payload.source or "webhook"),
        reason=str(payload.reason or ""),
        user_id=str(payload.user_id or "webhook"),
    )
    if transport:
        log.info(f"Webhook command queued via {transport}: cmd={cmd}")
        return
    raise RuntimeError(f"webhook command failed to queue: cmd={cmd}")


def _payload_to_dict(payload: TradingWebhookPayload) -> Dict[str, str]:
    dump_fn = getattr(payload, "model_dump", None)
    if callable(dump_fn):
        data = dump_fn()
    else:
        data = payload.dict()
    return dict(data or {})


def _process_webhook_safe(
    payload_data: Dict[str, str],
    request_id: str,
    client_ip: str,
    idempotency_key: str,
):
    cmd = str((payload_data or {}).get("cmd") or "").strip().lower()
    try:
        payload = TradingWebhookPayload(**(payload_data or {}))
    except Exception as e:
        log.error(f"Webhook background invalid payload (request_id={request_id}): {e}")
        emit_audit_log(
            log,
            "webhook_background_failed",
            status=400,
            reason="invalid_payload",
            cmd=cmd,
            key=idempotency_key,
            ip=client_ip,
            request_id=request_id,
        )
        return

    try:
        _process_webhook(payload)
        emit_audit_log(
            log,
            "webhook_processed",
            status=200,
            cmd=cmd,
            key=idempotency_key,
            user_id=payload.user_id,
            ip=client_ip,
            request_id=request_id,
        )
    except Exception as e:
        log.error(f"Webhook background processing failed (request_id={request_id}, cmd={cmd}): {e}")
        emit_audit_log(
            log,
            "webhook_background_failed",
            status=500,
            reason=str(e),
            cmd=cmd,
            key=idempotency_key,
            user_id=payload.user_id,
            ip=client_ip,
            request_id=request_id,
        )


def _extract_client_ip(request: Request) -> str:
    max_len = max(32, int(getattr(Config, "WEBHOOK_CLIENT_IP_MAX_LEN", 128)))
    trust_forwarded_headers = bool(getattr(Config, "WEB_REVERSE_PROXY_ENABLED", False))
    if trust_forwarded_headers:
        forwarded_for = str(request.headers.get("X-Forwarded-For", "") or "")
        if forwarded_for:
            first = forwarded_for.split(",", 1)[0].strip()
            if first:
                return first[:max_len]
        real_ip = str(request.headers.get("X-Real-IP", "") or "").strip()
        if real_ip:
            return real_ip[:max_len]
    client_host = str(request.client.host if request.client else "" or "").strip()
    if client_host:
        return client_host[:max_len]
    return "unknown"


@app.get("/healthz")
def health():
    """Liveness probe for webhook gateway with deep exchange check."""
    status = {"status": "ok", "service": "elara-webhook-fastapi"}
    try:
        redis = get_redis()
        if not redis:
            status["redis"] = "disconnected"
            status["exchange"] = "unknown"
            return status
            
        raw_status = redis.get(Config.TRADING_STATUS_KEY)
        if raw_status:
            data = json.loads(raw_status)
            last_conn = data.get("exchange_connected", False)
            try:
                last_ts = int(float(data.get("timestamp_ms", 0) or 0))
            except Exception:
                last_ts = 0
            now = int(time.time() * 1000)
            if now - last_ts > 180000:
                last_conn = False
            status["exchange"] = "connected" if last_conn else "disconnected"
        else:
            status["exchange"] = "unknown"
    except Exception as e:
        log.debug(f"Webhook health probe failed: {e}")
        status["exchange"] = "error"
    return status


@app.post("/api/webhook/trading")
async def trading_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """
    Receive signed trading command webhook.

    Security controls:
    - HMAC signature validation (`X-Signature`) (Offloaded to GIL thread pool)
    - idempotency key with TTL (`X-Idempotency-Key`)
    - per-client IP rate limiting
    - async background processing for queue I/O
    """
    import asyncio
    client_ip = _extract_client_ip(request)
    if _is_rate_limited(client_ip):
        emit_audit_log(
            log,
            "webhook_rejected",
            status=429,
            reason="rate_limited",
            ip=client_ip,
        )
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    request_id = uuid4().hex
    raw_body = await request.body()

    def _sync_validate_and_parse():
        is_valid = _validate_signature(raw_body, x_signature or "")
        if not is_valid:
            return False, None, "invalid_signature"
        try:
            data = json.loads(raw_body)
            p = TradingWebhookPayload(**data)
            return True, p, None
        except Exception:
            return True, None, "invalid_payload"

    is_valid, payload, err = await asyncio.to_thread(_sync_validate_and_parse)

    if not is_valid:
        emit_audit_log(
            log,
            "webhook_rejected",
            status=401,
            reason="invalid_signature",
            cmd="unknown",
            ip=client_ip,
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    if payload is None:
        emit_audit_log(
            log,
            "webhook_rejected",
            status=400,
            reason="invalid_payload",
            cmd="unknown",
            ip=client_ip,
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail="Invalid payload")

    cmd = str(payload.cmd or "").strip().lower()
    if cmd not in ALLOWED_TRADE_COMMANDS:
        emit_audit_log(
            log,
            "webhook_rejected",
            status=400,
            reason="invalid_cmd",
            cmd=cmd,
            ip=client_ip,
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail="Invalid cmd")

    idempotency_key = str(x_idempotency_key or "").strip()
    if not idempotency_key:
        idempotency_key = hashlib.sha256(raw_body).hexdigest()

    try:
        if _is_duplicate_request(idempotency_key):
            emit_audit_log(
                log,
                "webhook_duplicate",
                status=200,
                cmd=cmd,
                key=idempotency_key,
                ip=client_ip,
                request_id=request_id,
            )
            return {"status": "duplicate_ignored", "cmd": cmd, "request_id": request_id}
    except ValueError as e:
        emit_audit_log(
            log,
            "webhook_rejected",
            status=400,
            reason=str(e),
            cmd=cmd,
            ip=client_ip,
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail="Invalid idempotency key")

    payload_data = _payload_to_dict(payload)
    background_tasks.add_task(
        _process_webhook_safe,
        payload_data,
        request_id,
        client_ip,
        idempotency_key,
    )
    emit_audit_log(
        log,
        "webhook_accepted",
        status=202,
        cmd=cmd,
        user_id=payload.user_id,
        key=idempotency_key,
        ip=client_ip,
        request_id=request_id,
    )
    return {"status": "accepted", "cmd": cmd, "request_id": request_id}

