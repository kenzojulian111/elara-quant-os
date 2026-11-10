import logging
import asyncio
import hmac
import hashlib
import secrets
import base64
import re
import math
import csv
import io
import urllib.request
from datetime import timedelta
import socket
import os
import time
import threading
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeoutError, ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from flask import Flask, Response, jsonify, request, render_template, g, redirect
from flask.json.provider import DefaultJSONProvider
from flask_socketio import SocketIO
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    verify_jwt_in_request,
    decode_token,
    get_jwt_identity,
    get_jwt,
)

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except ImportError:
    Limiter = None
    def get_remote_address():
        return "unknown"
from flask_cors import CORS

try:
    from flask_wtf import CSRFProtect
    from flask_wtf.csrf import CSRFError, generate_csrf
except ImportError:
    CSRFProtect = None
    CSRFError = None
    generate_csrf = None

try:
    from flask_talisman import Talisman
except ImportError:
    Talisman = None

from config.settings import Config
from core.events import (
    build_trade_command_message,
    trade_command_queue_key,
    trader_status_key,
)
from database.models import db
from utils.common import async_runner
from utils.health import assess_runtime_mode, observe_runtime_state, snapshot as health_snapshot
from utils.runtime_sanity import evaluate_runtime_sanity
from runtime.interfaces.mqtt_service import system_state, state_lock
from core.base import NLPUnderstanding, Entity
from utils.redis_helper import get_redis, redis_get_json, redis_readiness_probe
from utils.service_contracts import TraderStatusContract
from utils.json_logging import configure_root_logging_json, emit_audit_log
from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.paper_audit import PaperAuditRepository
from utils.observability import (
    REGISTRY as OBSERVABILITY_REGISTRY,
    new_trace_id,
    profile_block,
    record_http_request,
    record_runtime_health,
    reset_observability_context,
    set_observability_context,
)
import json
try:
    from pydantic import BaseModel, Field, ValidationError
except Exception:
    BaseModel = None
    Field = None
    ValidationError = Exception

log = logging.getLogger("elara.web")


if BaseModel and Field:
    class DeviceControlPayload(BaseModel):
        type: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
        room: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
        action: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z]+$")
        value: float | None = None
        async_mode: bool | None = Field(default=None, alias="async")

    class TgControlPayload(BaseModel):
        command: str = Field(..., min_length=1, max_length=1000)
        async_mode: bool | None = Field(default=None, alias="async")

    class TradingCommandPayload(BaseModel):
        cmd: str = Field(..., min_length=1, max_length=24, pattern=r"^[A-Za-z_]+$")
        reason: str | None = Field(default="", max_length=500)
        user_id: str | None = Field(default="web_user", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:@.]+$")

    class AdminCreateUserPayload(BaseModel):
        email: str = Field(..., min_length=3, max_length=254)
        password: str = Field(..., min_length=1, max_length=256)
        name: str | None = Field(default="", max_length=80)
        role: str | None = Field(default="user", max_length=20)

    class AdminUpdateUserPayload(BaseModel):
        role: str | None = Field(default=None, max_length=20)
        name: str | None = Field(default=None, max_length=80)
        password: str | None = Field(default=None, min_length=1, max_length=256)

    class SelfOpsHealPayload(BaseModel):
        sync: bool | None = None

    class SelfOpsDeployPayload(BaseModel):
        pull: bool | None = None
        install: bool | None = None
        restart: bool | None = None

    class PasswordForgotPayload(BaseModel):
        email: str | None = Field(default=None, min_length=3, max_length=254)

    class PasswordResetPayload(BaseModel):
        token: str = Field(..., min_length=8, max_length=512)
        new_password: str = Field(..., min_length=8, max_length=256)

    class EmailVerifyRequestPayload(BaseModel):
        email: str | None = Field(default=None, min_length=3, max_length=254)

    class EmailVerifyConfirmPayload(BaseModel):
        token: str = Field(..., min_length=8, max_length=512)

    class AccountProfilePatchPayload(BaseModel):
        name: str = Field(..., min_length=1, max_length=80)

    class AccountPasswordPatchPayload(BaseModel):
        current_password: str = Field(..., min_length=1, max_length=256)
        new_password: str = Field(..., min_length=8, max_length=256)

    class MfaOtpPayload(BaseModel):
        otp: str = Field(..., min_length=6, max_length=10)
else:
    DeviceControlPayload = None
    TgControlPayload = None
    TradingCommandPayload = None
    AdminCreateUserPayload = None
    AdminUpdateUserPayload = None
    SelfOpsHealPayload = None
    SelfOpsDeployPayload = None
    PasswordForgotPayload = None
    PasswordResetPayload = None
    EmailVerifyRequestPayload = None
    EmailVerifyConfirmPayload = None
    AccountProfilePatchPayload = None
    AccountPasswordPatchPayload = None
    MfaOtpPayload = None


class ElaraJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return format(obj, "f")
        return super().default(obj)


def _is_production_like_env() -> bool:
    env_candidates = [
        getattr(Config, "ENV", ""),
        os.getenv("APP_ENV", ""),
        os.getenv("FLASK_ENV", ""),
    ]
    normalized = {
        str(value or "").strip().lower()
        for value in env_candidates
        if str(value or "").strip()
    }
    return any(value in {"production", "prod"} for value in normalized)


def create_web_app(elara_skill, db_manager, mqtt_service=None, state_manager=None, self_heal_service=None):
    base_dir = Config.root_path
    templates_dir = os.path.join(base_dir, "templates")
    static_dir = os.path.join(base_dir, "static")

    app = Flask(__name__, static_folder=static_dir, template_folder=templates_dir)
    if db_manager is not None:
        db_manager.app = app
    app.json_provider_class = ElaraJSONProvider
    app.json = app.json_provider_class(app)

    app.config.from_object(Config)
    configure_root_logging_json(bool(getattr(Config, "LOG_JSON", False)))
    app.config["DEBUG"] = bool(getattr(Config, "DEBUG", False))
    app.config["MAX_CONTENT_LENGTH"] = max(
        1024,
        int(getattr(Config, "WEB_MAX_CONTENT_LENGTH_BYTES", 1024 * 1024)),
    )
    app.config["JWT_TOKEN_LOCATION"] = list(
        getattr(Config, "JWT_TOKEN_LOCATION", ["headers", "cookies"]) or ["headers", "cookies"]
    )
    app.config["JWT_ACCESS_COOKIE_NAME"] = str(
        getattr(Config, "JWT_ACCESS_COOKIE_NAME", "elara_access_token") or "elara_access_token"
    )
    app.config["JWT_COOKIE_CSRF_PROTECT"] = bool(getattr(Config, "JWT_COOKIE_CSRF_PROTECT", False))
    app.config["JWT_COOKIE_SECURE"] = bool(getattr(Config, "WEB_FORCE_HTTPS", False))
    app.config["JWT_COOKIE_SAMESITE"] = str(getattr(Config, "JWT_COOKIE_SAMESITE", "Lax") or "Lax")

    app.config["JWT_SECRET_KEY"] = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    if not app.config.get("JWT_SECRET_KEY"):
        raise RuntimeError("JWT_SECRET_KEY/SECRET_KEY kosong. Set env SECRET_KEY atau JWT_SECRET_KEY.")

    if bool(getattr(Config, "WEB_REVERSE_PROXY_ENABLED", False)):
        proxy_hops = max(1, int(getattr(Config, "WEB_REVERSE_PROXY_HOPS", 1)))
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_port=proxy_hops,
        )
        log.info(f"Reverse proxy trust enabled (hops={proxy_hops}).")
    try:
        app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=Config.JWT_ACCESS_MINUTES)
    except Exception as e:
        log.warning(f"Failed applying JWT access token expiry config: {e}")

    cors_origins = Config.CORS_ORIGINS
    if isinstance(cors_origins, str) and cors_origins != "*":
        cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

    CORS(app, origins=cors_origins)

    allowed_hosts_raw = str(getattr(Config, "WEB_ALLOWED_HOSTS", "") or "").strip()
    allowed_hosts = {
        h.strip().lower()
        for h in allowed_hosts_raw.split(",")
        if h.strip()
    }
    if allowed_hosts:
        log.info(f"Trusted hosts enabled ({len(allowed_hosts)} host entries).")

    max_command_chars = max(32, int(getattr(Config, "WEB_MAX_COMMAND_CHARS", 1000)))
    max_reason_chars = max(16, int(getattr(Config, "WEB_MAX_REASON_CHARS", 500)))
    max_email_chars = max(32, int(getattr(Config, "WEB_MAX_EMAIL_CHARS", 254)))
    max_password_chars = max(32, int(getattr(Config, "WEB_MAX_PASSWORD_CHARS", 256)))
    max_json_keys = max(20, int(getattr(Config, "WEB_MAX_JSON_KEYS", 400)))
    max_json_depth = max(1, int(getattr(Config, "WEB_MAX_JSON_DEPTH", 8)))
    jwt_cookie_max_bytes = max(512, int(getattr(Config, "JWT_COOKIE_MAX_BYTES", 4096)))
    command_sync_timeout_sec = max(0.2, float(getattr(Config, "WEB_COMMAND_SYNC_TIMEOUT_SEC", 2.5)))
    command_background_default = bool(getattr(Config, "WEB_COMMAND_BACKGROUND_DEFAULT", False))
    redact_command_logs = bool(getattr(Config, "WEB_LOG_REDACT_COMMANDS", True))
    device_set_min = float(getattr(Config, "WEB_DEVICE_SET_MIN", -50.0))
    device_set_max = float(getattr(Config, "WEB_DEVICE_SET_MAX", 100.0))
    observability_enabled = bool(getattr(Config, "OBSERVABILITY_ENABLED", True))
    prometheus_metrics_enabled = bool(getattr(Config, "PROMETHEUS_METRICS_ENABLED", True))
    prometheus_metrics_public = bool(getattr(Config, "PROMETHEUS_METRICS_PUBLIC", False))
    prometheus_metrics_token = str(getattr(Config, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
    slow_request_ms = max(1.0, float(getattr(Config, "OBSERVABILITY_SLOW_REQUEST_MS", 1000.0) or 1000.0))
    health_probe_timeout_sec = max(0.1, float(getattr(Config, "WEB_HEALTH_PROBE_TIMEOUT_SEC", 0.8)))
    health_cache_ttl_sec = max(0.0, float(getattr(Config, "WEB_HEALTH_CACHE_TTL_SEC", 2.0)))
    activity_alert_tg_enabled = bool(getattr(Config, "WEB_ACTIVITY_ALERT_TG_ENABLED", False))
    activity_alert_cooldown_sec = max(10, int(getattr(Config, "WEB_ACTIVITY_ALERT_COOLDOWN_SEC", 90)))
    activity_alert_max_memory = max(50, int(getattr(Config, "WEB_ACTIVITY_ALERT_MAX_MEMORY", 500)))
    activity_alert_digest_sec = max(60, int(getattr(Config, "WEB_ACTIVITY_ALERT_DIGEST_SEC", 900)))
    activity_alert_digest_min_events = max(1, int(getattr(Config, "WEB_ACTIVITY_ALERT_DIGEST_MIN_EVENTS", 3)))
    activity_alert_user_action_cooldown_sec = max(
        20,
        int(getattr(Config, "WEB_ACTIVITY_ALERT_USER_ACTION_COOLDOWN_SEC", 300)),
    )
    market_chart_fetch_interval_sec = max(2.0, float(getattr(Config, "WEB_MARKET_CHART_FETCH_INTERVAL_SEC", 8.0)))
    market_chart_points_max = max(20, int(getattr(Config, "WEB_MARKET_CHART_POINTS_MAX", 120)))
    market_symbols_max = max(4, int(getattr(Config, "WEB_MARKET_SYMBOLS_MAX", 64)))
    market_allow_blocking_fallback = bool(getattr(Config, "WEB_MARKET_ALLOW_BLOCKING_FALLBACK", False))
    market_default_timeframe = str(getattr(Config, "WEB_MARKET_DEFAULT_TIMEFRAME", "1h") or "1h").strip().lower()
    market_alert_eval_interval_sec = max(2.0, float(getattr(Config, "WEB_MARKET_ALERT_EVAL_INTERVAL_SEC", 8.0)))
    market_alert_default_cooldown_sec = max(30, int(getattr(Config, "WEB_MARKET_ALERT_DEFAULT_COOLDOWN_SEC", 300)))
    market_alert_max_rules_per_user = max(20, int(getattr(Config, "WEB_MARKET_ALERT_MAX_RULES_PER_USER", 200)))
    market_alert_telegram_ready = bool(
        str(getattr(Config, "BOT_TOKEN", "") or "").strip()
        and int(getattr(Config, "TG_ALLOWED_USER", 0) or 0) != 0
    )
    html_auth_guard_enabled = bool(getattr(Config, "WEB_HTML_AUTH_GUARD_ENABLED", True))
    require_email_verification = bool(getattr(Config, "WEB_REQUIRE_EMAIL_VERIFICATION", False))
    expose_dev_auth_tokens = bool(getattr(Config, "WEB_EXPOSE_DEV_AUTH_TOKENS", False))
    password_reset_ttl_minutes = max(5, int(getattr(Config, "WEB_PASSWORD_RESET_TTL_MINUTES", 30)))
    email_verify_ttl_minutes = max(10, int(getattr(Config, "WEB_EMAIL_VERIFY_TTL_MINUTES", 1440)))
    mfa_totp_period_sec = max(15, int(getattr(Config, "WEB_MFA_TOTP_PERIOD_SEC", 30)))
    mfa_totp_window_steps = max(0, int(getattr(Config, "WEB_MFA_TOTP_WINDOW_STEPS", 1)))
    admin_mfa_required = bool(getattr(Config, "WEB_ADMIN_MFA_REQUIRED", True))
    session_touch_min_interval_sec = max(3, int(getattr(Config, "WEB_SESSION_TOUCH_MIN_INTERVAL_SEC", 30)))
    activity_alert_last_sent = {}
    activity_alert_user_action_last_sent = {}
    activity_alert_digest_buffer = []
    activity_alert_digest_last_sent = 0.0
    session_touch_cache = {}
    market_chart_cache = {
        "stock": {"active_symbol": "", "symbols": {}, "symbol_order": [], "updated_at": 0, "error": ""},
        "crypto": {"active_symbol": "", "symbols": {}, "symbol_order": [], "updated_at": 0, "error": ""},
    }
    market_chart_last_sync_ts = 0.0
    market_alert_last_eval_ts = 0.0
    market_socket_subscriptions = {}
    market_socket_sub_lock = threading.Lock()
    market_stream_stop_event = threading.Event()
    market_stream_started = False
    health_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-health")
    health_cache = {
        "ready": {"ts": 0.0, "payload": None, "status": 200},
        "deep": {"ts": 0.0, "payload": None, "status": 200},
    }
    shutdown_hooks = []

    def _register_shutdown_hook(fn):
        if callable(fn):
            shutdown_hooks.append(fn)

    def _run_shutdown_hooks():
        for fn in shutdown_hooks:
            try:
                fn()
            except Exception as e:
                log.warning(f"Web shutdown hook error: {e}")

    _register_shutdown_hook(lambda: health_executor.shutdown(wait=False, cancel_futures=True))
    _register_shutdown_hook(lambda: market_stream_stop_event.set())
    app.extensions["elara_shutdown_hooks"] = shutdown_hooks
    app.extensions["elara_run_shutdown_hooks"] = _run_shutdown_hooks

    jwt_revoke_enabled = bool(getattr(Config, "JWT_REVOKE_ENABLED", True))
    jwt_block_prefix = str(getattr(Config, "JWT_BLOCKLIST_PREFIX", "elara:jwt:block:") or "elara:jwt:block:")
    jwt_block_fallback_ttl = max(60, int(getattr(Config, "JWT_BLOCKLIST_FALLBACK_TTL_SEC", 86400)))
    jwt_block_memory = {}

    replay_enabled = bool(getattr(Config, "API_REPLAY_PROTECT_ENABLED", False))
    replay_secret = str(getattr(Config, "API_REPLAY_SECRET", "") or "").strip()
    replay_secret_rotation = str(getattr(Config, "API_REPLAY_SECRET_ROTATION", "") or "").strip()
    replay_secrets = []
    for raw in [replay_secret, replay_secret_rotation]:
        for part in str(raw or "").split(","):
            item = str(part or "").strip()
            if item and item not in replay_secrets:
                replay_secrets.append(item)
    replay_max_skew_sec = max(1, int(getattr(Config, "API_REPLAY_MAX_SKEW_SEC", 10)))
    replay_nonce_ttl_sec = max(1, int(getattr(Config, "API_REPLAY_NONCE_TTL_SEC", 30)))
    replay_nonce_memory = {}

    def _cookie_size_estimate(name: str, value: str) -> int:
        # Rough HTTP header size estimate for one Set-Cookie line.
        return len(str(name or "")) + len(str(value or "")) + 180

    def _set_access_cookie(response, token: str) -> bool:
        cookie_name = str(app.config.get("JWT_ACCESS_COOKIE_NAME") or "elara_access_token")
        if _cookie_size_estimate(cookie_name, token) > jwt_cookie_max_bytes:
            log.warning(
                "JWT cookie skipped: estimated size exceeds configured limit (%s bytes).",
                jwt_cookie_max_bytes,
            )
            return False
        response.set_cookie(
            cookie_name,
            token,
            max_age=int(getattr(Config, "JWT_ACCESS_MINUTES", 720)) * 60,
            httponly=True,
            samesite=str(app.config.get("JWT_COOKIE_SAMESITE") or "Lax"),
            secure=bool(app.config.get("JWT_COOKIE_SECURE", False)),
        )
        return True

    if bool(getattr(Config, "WEB_SECURITY_HEADERS", True)):
        if Talisman:
            web_force_https = bool(getattr(Config, "WEB_FORCE_HTTPS", False))
            # Keep CSP disabled for now because dashboard uses inline scripts/styles.
            Talisman(
                app,
                force_https=web_force_https,
                content_security_policy=None,
                frame_options="DENY",
                # Avoid forcing Secure cookies on local HTTP when HTTPS is not enabled.
                session_cookie_secure=web_force_https,
            )
        else:
            log.warning("WEB_SECURITY_HEADERS aktif tapi Flask-Talisman belum terpasang.")

    csrf_enabled = bool(getattr(Config, "WEB_CSRF_PROTECT", True))
    csrf = None
    if csrf_enabled and not CSRFProtect:
        msg = "WEB_CSRF_PROTECT aktif tapi Flask-WTF belum terpasang."
        if _is_production_like_env():
            raise RuntimeError(msg)
        log.warning(msg)
    if csrf_enabled and CSRFProtect:
        app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
        app.config.setdefault("WTF_CSRF_CHECK_DEFAULT", False)
        csrf = CSRFProtect()
        csrf.init_app(app)

    db.init_app(app)
    with app.app_context():
        try:
            if bool(getattr(Config, "DB_AUTO_CREATE_TABLES", True)):
                db.create_all()
        except Exception as e:
            log.error(f"DB init error: {e}")
            
    # Phase 6: API Documentation (Swagger)
    try:
        from utils.swagger_ui import install_swagger_ui
        install_swagger_ui(app)
    except Exception as e:
        log.warning(f"Failed to install Swagger UI: {e}")

    # Phase 6: Distributed Tracing (OpenTelemetry)
    if bool(getattr(Config, "OTEL_ENABLED", False)):
        try:
            from utils.opentelemetry_tracing import install_flask_tracing

            install_flask_tracing(
                app,
                service_name=str(getattr(Config, "OTEL_SERVICE_NAME", "elara-core") or "elara-core"),
            )
        except Exception as e:
            log.warning(f"Failed to install OpenTelemetry Tracing: {e}")

    jwt = JWTManager(app)

    @jwt.token_in_blocklist_loader
    def _token_in_blocklist(_jwt_header, jwt_payload):
        if not jwt_revoke_enabled:
            return False
        jti = str((jwt_payload or {}).get("jti") or "")
        if _is_jti_revoked(jti):
            return True
        try:
            return bool(db_manager.is_user_session_revoked(jti))
        except Exception:
            return False

    default_limit = str(getattr(Config, "WEB_RATE_LIMIT_DEFAULT", "") or "").strip()
    default_limits = [default_limit] if default_limit else []
    if Limiter:
        limiter_storage_uri = (getattr(Config, "REDIS_URL", "") or "memory://")
        if limiter_storage_uri and limiter_storage_uri != "memory://":
            redis_client = get_redis()
            if not redis_client:
                log.warning("Redis unavailable for rate limiter; falling back to memory storage.")
                limiter_storage_uri = "memory://"
        limiter = Limiter(
            get_remote_address,
            app=app,
            default_limits=default_limits,
            storage_uri=limiter_storage_uri,
        )
    else:
        class _DummyLimiter:
            def limit(self, *_args, **_kwargs):
                def deco(fn):
                    return fn
                return deco
        limiter = _DummyLimiter()

    def _compact_text(value, *, max_len: int) -> str:
        text_value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text_value) > max_len:
            return ""
        return text_value

    def _is_safe_device_token(value: str) -> bool:
        token = str(value or "").strip().lower()
        if not token:
            return False
        return bool(re.fullmatch(r"[a-z0-9_\-]{1,64}", token))

    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        text_value = str(value or "").strip().lower()
        if text_value in {"1", "true", "yes", "on"}:
            return True
        if text_value in {"0", "false", "no", "off"}:
            return False
        return None

    def _validate_payload(model_cls, payload, *, by_alias: bool = False, exclude_none: bool = False):
        if not model_cls:
            return payload
        validator = getattr(model_cls, "model_validate", None)
        if callable(validator):
            parsed = validator(payload)
        else:
            parsed = model_cls.parse_obj(payload)
        dump_fn = getattr(parsed, "model_dump", None)
        if callable(dump_fn):
            return dump_fn(by_alias=by_alias, exclude_none=exclude_none)
        return parsed.dict(by_alias=by_alias, exclude_none=exclude_none)

    def _prefer_background_execution(payload) -> bool:
        maybe_flag = None
        if isinstance(payload, dict):
            maybe_flag = payload.get("async")
        if maybe_flag is None:
            maybe_flag = request.args.get("async")
        parsed = _coerce_bool(maybe_flag)
        if parsed is None:
            return command_background_default
        return parsed

    def _redact_command_for_log(command_text: str) -> str:
        raw = str(command_text or "")
        if not redact_command_logs or not raw:
            return raw
        out = raw
        out = re.sub(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*([^\s,;]+)",
            r"\1=[REDACTED]",
            out,
        )
        out = re.sub(
            r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*",
            r"\1 [REDACTED]",
            out,
        )
        return out

    def _normalize_command_message(message, source: str = "") -> str:
        text = str(message or "").strip()
        if text:
            return text
        src = str(source or "").strip()
        if src:
            return f"Command selesai ({src}), tapi belum ada output yang bisa ditampilkan."
        return "Command selesai, tapi belum ada output yang bisa ditampilkan."

    def _attach_command_future_handler(future, request_id: str, source: str):
        def _on_done(fut):
            message = None
            try:
                message = _normalize_command_message(fut.result(), source=source)
            except Exception as err:
                log.error(f"Async command failed ({source}, request_id={request_id}): {err}")
                message = "Command execution failed."
            try:
                socketio.emit(
                    "command_response",
                    {"request_id": request_id, "message": message, "source": source},
                )
            except Exception as emit_err:
                log.warning(
                    f"Failed emitting command_response websocket event ({source}, request_id={request_id}): {emit_err}"
                )

        future.add_done_callback(_on_done)

    def _submit_command(coro, request_id: str, source: str, *, background_only: bool = False):
        future = async_runner.submit_coroutine(coro)
        _attach_command_future_handler(future, request_id=request_id, source=source)
        if background_only:
            return None, True, None
        try:
            result = future.result(timeout=command_sync_timeout_sec)
            return _normalize_command_message(result, source=source), False, None
        except FutureTimeoutError:
            return None, True, None
        except Exception as err:
            return None, False, err

    def _run_health_probe(check_fn):
        future = health_executor.submit(check_fn)
        try:
            with profile_block("web_health_probe", check=getattr(check_fn, "__name__", "unknown")):
                future.result(timeout=health_probe_timeout_sec)
            return {"status": "ok"}, False
        except FutureTimeoutError:
            future.cancel()
            return {"status": "timeout", "timeout_sec": health_probe_timeout_sec}, True
        except Exception as e:
            return {"status": "down", "error": str(e)}, True

    def _load_trading_status_snapshot():
        redis_client = get_redis()
        redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        status_key = str(
            getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
            or trader_status_key(redis_prefix)
        ).strip()
        status_payload = None
        try:
            if redis_client:
                status_payload = redis_get_json(redis_client, status_key)
            if not status_payload and bool(getattr(Config, "TRADING_STATUS_FILE_FALLBACK_ENABLED", True)):
                status_path = str(getattr(Config, "TRADING_STATUS_PATH", "") or "").strip()
                if status_path and os.path.exists(status_path):
                    with open(status_path, "r", encoding="utf-8") as f:
                        status_payload = json.load(f)
        except Exception:
            return None
        return status_payload if isinstance(status_payload, dict) else None

    def _trader_status_health(required: bool = False):
        meta = {}

        def _probe():
            nonlocal meta
            payload = _load_trading_status_snapshot()
            if not payload:
                if required:
                    raise RuntimeError("trading status unavailable")
                meta = {"status": "unavailable", "required": False}
                return
            status_contract = TraderStatusContract.from_payload(payload)
            ts_text = str(payload.get("ts") or "").strip()
            if not ts_text:
                raise RuntimeError("trading status timestamp missing")
            try:
                ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_sec = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
            except Exception as e:
                raise RuntimeError(f"invalid trading status timestamp: {e}") from e

            max_age = max(10, int(getattr(Config, "TRADING_STATUS_MAX_AGE_SEC", 120)))
            if age_sec > max_age:
                raise RuntimeError(f"trading status stale ({age_sec:.1f}s)")

            setup_error = str(payload.get("setup_error") or "").strip()
            is_running = status_contract.is_running
            simulation_fallback = status_contract.simulation_fallback
            control_state = status_contract.control_state
            drift_high = bool(payload.get("exchange_time_drift_high", False))
            meta = {
                "status_age_sec": round(age_sec, 2),
                "is_running": is_running,
                "simulation_fallback": simulation_fallback,
                "control_state": control_state,
                "exchange_time_drift_high": drift_high,
            }
            if setup_error:
                raise RuntimeError(f"trader setup_error: {setup_error[:120]}")
            if not is_running and not simulation_fallback:
                raise RuntimeError("trader daemon not running")
            if drift_high:
                raise RuntimeError("exchange_time_drift_high")

        check, bad = _run_health_probe(_probe)
        check.update(meta)
        return check, bad

    def _db_health():
        try:
            engine = db.engine

            def _db_ping():
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))

            return _run_health_probe(_db_ping)
        except Exception as e:
            return {"status": "down", "error": str(e)}, True

    def _redis_health(*, deep: bool = False):
        redis_client = get_redis()
        redis_required = bool(getattr(Config, "REDIS_EVENT_BUS_REQUIRED", False))
        if not redis_client:
            return {"status": "unavailable", "required": redis_required}, redis_required
        if deep or redis_required:
            probe_payload = {}

            def _probe():
                nonlocal probe_payload
                redis_prefix = str(getattr(Config, "REDIS_PREFIX", "elara:") or "elara:")
                probe_payload = redis_readiness_probe(redis_client, redis_prefix=redis_prefix)
                if not bool(probe_payload.get("ok")):
                    raise RuntimeError("redis readiness failed")

            check, bad = _run_health_probe(_probe)
            check["readiness"] = probe_payload
            return check, bad
        return _run_health_probe(redis_client.ping)

    def _dependency_states_from_checks(checks: dict) -> dict[str, str]:
        states = {}
        for name, check in (checks or {}).items():
            if not isinstance(check, dict):
                continue
            status = str(check.get("status") or "").strip().lower()
            if status in {"disabled"}:
                continue
            dependency = "trader_status" if name == "trader" else str(name or "").strip().lower()
            if status == "ok":
                states[dependency] = "up"
            elif status == "timeout":
                states[dependency] = "slow"
            elif status == "unavailable":
                states[dependency] = "down" if bool(check.get("required", False)) else "up"
            else:
                states[dependency] = "down"
        return states

    def _runtime_payload_from_checks(checks: dict) -> dict:
        metrics = health_snapshot()
        assessment = assess_runtime_mode(
            dependency_states=_dependency_states_from_checks(checks),
            metrics=metrics,
        )
        state = observe_runtime_state(
            assessment,
            source="web_health",
            metadata={"checks": sorted(str(key) for key in (checks or {}).keys())},
        )
        sanity = evaluate_runtime_sanity(
            dependency_states=assessment.dependency_states,
            runtime_state=str(state.get("state") or ""),
        )
        metrics["runtime_state"] = state
        metrics["runtime_mode_assessment"] = {
            "mode": assessment.mode,
            "reasons": assessment.reasons,
            "allow_new_entries": assessment.allow_new_entries,
            "allow_background_tasks": assessment.allow_background_tasks,
            "dependency_states": dict(assessment.dependency_states),
        }
        metrics["runtime_sanity"] = sanity.to_dict()
        record_runtime_health(metrics)
        return metrics

    def _health_cached(kind: str, builder):
        bucket = health_cache.setdefault(kind, {"ts": 0.0, "payload": None, "status": 200})
        now = time.time()
        cached_payload = bucket.get("payload")
        cached_ts = float(bucket.get("ts") or 0.0)
        if health_cache_ttl_sec > 0 and cached_payload and (now - cached_ts) <= health_cache_ttl_sec:
            return cached_payload, int(bucket.get("status") or 200)
        payload, status_code = builder()
        bucket["ts"] = now
        bucket["payload"] = payload
        bucket["status"] = status_code
        return payload, status_code

    def _build_ready_health_payload():
        checks = {}
        degraded = False

        db_check, db_bad = _db_health()
        checks["db"] = db_check
        degraded = degraded or db_bad

        redis_check, redis_bad = _redis_health(deep=False)
        checks["redis"] = redis_check
        degraded = degraded or redis_bad

        trader_required = bool(getattr(Config, "HEALTH_TRADER_REQUIRED", False))
        if bool(getattr(Config, "ENABLE_TRADING_SKILL", True)):
            trader_check, trader_bad = _trader_status_health(required=trader_required)
            checks["trader"] = trader_check
            degraded = degraded or (trader_bad and trader_required)
        else:
            checks["trader"] = {"status": "disabled"}

        payload = {
            "status": "degraded" if degraded else "ok",
            "kind": "ready",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "runtime": _runtime_payload_from_checks(checks),
        }
        return payload, 503 if degraded else 200

    def _build_deep_health_payload():
        ready_payload, ready_status = _build_ready_health_payload()
        checks = dict(ready_payload.get("checks") or {})
        degraded = ready_status >= 400

        if bool(getattr(Config, "ENABLE_TRADING_SKILL", True)):
            exchange_required = bool(getattr(Config, "HEALTH_EXCHANGE_REQUIRED", False))
            exchange_check, exchange_bad = _trader_status_health(required=exchange_required)
            checks["exchange"] = exchange_check
            degraded = degraded or (exchange_bad and exchange_required)
        else:
            checks["exchange"] = {"status": "disabled"}

        payload = {
            "status": "degraded" if degraded else "ok",
            "kind": "deep",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "runtime": _runtime_payload_from_checks(checks),
        }
        return payload, 503 if degraded else 200

    def _device_catalog(device_config):
        out = {}
        raw_devices = {}
        if isinstance(device_config, dict):
            raw_devices = device_config.get("devices", {}) or {}
        if not isinstance(raw_devices, dict):
            return out
        for dev_type, rooms in raw_devices.items():
            type_key = str(dev_type or "").strip().lower()
            if not type_key:
                continue
            room_items = []
            if isinstance(rooms, dict):
                room_items = list(rooms.keys())
            elif isinstance(rooms, list):
                room_items = rooms
            elif rooms is not None:
                room_items = [rooms]
            room_set = {str(room or "").strip().lower() for room in room_items if str(room or "").strip()}
            if room_set:
                out[type_key] = room_set
        return out

    def _count_json_keys(value, depth: int = 0) -> int:
        if depth > max_json_depth:
            return max_json_keys + 1
        if isinstance(value, dict):
            count = len(value)
            for item in value.values():
                count += _count_json_keys(item, depth + 1)
                if count > max_json_keys:
                    return count
            return count
        if isinstance(value, list):
            count = 0
            for item in value:
                count += _count_json_keys(item, depth + 1)
                if count > max_json_keys:
                    return count
            return count
        return 0

    def _host_valid() -> bool:
        if not allowed_hosts:
            return True
        host = (request.host or "").split(":", 1)[0].strip().lower()
        return bool(host and host in allowed_hosts)

    def _cleanup_expired_cache(cache: dict):
        now = time.time()
        stale_keys = [k for k, expires_at in cache.items() if float(expires_at or 0) <= now]
        for key in stale_keys:
            cache.pop(key, None)

    def _activity_client_ip() -> str:
        xff = str(request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return str(xff.split(",")[0].strip())[:64]
        return str(request.remote_addr or "")[:64]

    def _activity_user_agent() -> str:
        return str(request.headers.get("User-Agent") or "").strip()[:255]

    def _jwt_block_key(jti: str) -> str:
        return f"{jwt_block_prefix}{jti}"

    def _revoke_jti(jti: str, exp_ts: int):
        ttl = max(1, int(exp_ts - time.time()))
        redis_client = get_redis()
        if redis_client:
            try:
                redis_client.set(_jwt_block_key(jti), "1", ex=ttl)
                return
            except Exception as e:
                log.warning(f"JWT blocklist Redis write failed, fallback memory: {e}")
        _cleanup_expired_cache(jwt_block_memory)
        jwt_block_memory[jti] = time.time() + min(ttl, jwt_block_fallback_ttl)

    def _is_jti_revoked(jti: str) -> bool:
        if not jti:
            return False
        redis_client = get_redis()
        if redis_client:
            try:
                return bool(redis_client.get(_jwt_block_key(jti)))
            except Exception as e:
                log.warning(f"JWT blocklist Redis read failed, fallback memory: {e}")
        _cleanup_expired_cache(jwt_block_memory)
        return jti in jwt_block_memory

    def _register_user_session(username: str, token: str):
        try:
            payload = decode_token(token) or {}
            jti = str(payload.get("jti") or "").strip()
            exp_ts = int(payload.get("exp") or 0)
            if not jti:
                return
            db_manager.register_user_session(
                username=str(username or "").strip() or "anonymous",
                jti=jti,
                exp_ts=exp_ts,
                ip_address=_activity_client_ip(),
                user_agent=_activity_user_agent(),
            )
        except Exception as e:
            log.debug(f"register user session skipped: {e}")

    def _touch_current_user_session():
        try:
            claims = get_jwt() or {}
            jti = str(claims.get("jti") or "").strip()
            if not jti:
                return
            now = time.time()
            if len(session_touch_cache) > (activity_alert_max_memory * 4):
                stale_before = now - max(session_touch_min_interval_sec * 4, 120)
                stale_keys = [k for k, ts in session_touch_cache.items() if float(ts) < stale_before]
                for old_key in stale_keys:
                    session_touch_cache.pop(old_key, None)
            last_touch = float(session_touch_cache.get(jti, 0.0) or 0.0)
            if (now - last_touch) < session_touch_min_interval_sec:
                return
            session_touch_cache[jti] = now
            db_manager.touch_user_session(
                jti=jti,
                ip_address=_activity_client_ip(),
                user_agent=_activity_user_agent(),
            )
        except Exception:
            return

    def _nonce_cache_key(nonce: str) -> str:
        return f"replay:{nonce}"

    def _replay_nonce_seen(nonce: str) -> bool:
        _cleanup_expired_cache(replay_nonce_memory)
        redis_client = get_redis()
        if redis_client:
            try:
                ok = redis_client.set(
                    _nonce_cache_key(nonce),
                    "1",
                    nx=True,
                    ex=replay_nonce_ttl_sec,
                )
                return not bool(ok)
            except Exception as e:
                log.warning(f"Replay nonce Redis set failed, fallback memory: {e}")
        now = time.time()
        if nonce in replay_nonce_memory and replay_nonce_memory[nonce] > now:
            return True
        replay_nonce_memory[nonce] = now + replay_nonce_ttl_sec
        return False

    def _verify_replay_guard() -> bool:
        if not replay_enabled:
            return True
        if not replay_secrets:
            log.warning("Replay guard enabled but API_REPLAY_SECRET kosong; bypass replay guard.")
            return True
        ts_header = str(request.headers.get("X-Request-Timestamp") or "").strip()
        nonce = str(request.headers.get("X-Request-Nonce") or "").strip()
        signature = str(request.headers.get("X-Request-Signature") or "").strip().lower()
        if not ts_header or not nonce or not signature:
            return False
        try:
            ts_int = int(ts_header)
        except Exception:
            return False
        if abs(int(time.time()) - ts_int) > replay_max_skew_sec:
            return False
        if len(nonce) > 128 or _replay_nonce_seen(nonce):
            return False
        body = request.get_data(cache=True, as_text=False) or b""
        base = f"{ts_int}.{nonce}.".encode("utf-8") + body
        for secret in replay_secrets:
            expected = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(expected, signature):
                return True
        return False

    def _normalize_email(value) -> str:
        return _compact_text(value, max_len=max_email_chars).strip().lower()

    def _is_valid_email(email_text: str) -> bool:
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(email_text or "")))

    def _resolve_identity_optional() -> str:
        try:
            verify_jwt_in_request(optional=True)
            identity = str(get_jwt_identity() or "").strip().lower()
            if identity:
                _touch_current_user_session()
            return identity
        except Exception:
            return ""

    def _resolve_identity_required() -> str:
        verify_jwt_in_request()
        identity = str(get_jwt_identity() or "").strip().lower()
        if identity:
            _touch_current_user_session()
        return identity

    def _redirect_login_response():
        return redirect("/login")

    def _require_html_session(*, admin_only: bool = False):
        if AUTH_DISABLED or not html_auth_guard_enabled:
            return None
        try:
            identity = _resolve_identity_required()
        except Exception:
            return _redirect_login_response()
        if not identity:
            return _redirect_login_response()
        if admin_only:
            role = str(db_manager.get_user_role(identity) or "").strip().lower()
            if role not in ("admin", "superuser"):
                return redirect("/portal")
        return None

    def _generate_mfa_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    def _decode_base32_secret(secret: str) -> bytes:
        raw = str(secret or "").strip().replace(" ", "").upper()
        if not raw:
            return b""
        padding = "=" * ((8 - (len(raw) % 8)) % 8)
        try:
            return base64.b32decode(raw + padding, casefold=True)
        except Exception:
            return b""

    def _totp_code(secret: str, ts: int | None = None) -> str:
        key = _decode_base32_secret(secret)
        if not key:
            return ""
        epoch = int(ts if ts is not None else time.time())
        counter = int(epoch // mfa_totp_period_sec)
        msg = counter.to_bytes(8, "big")
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code_int = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
        return f"{code_int:06d}"

    def _normalize_otp(value) -> str:
        return re.sub(r"[^0-9]", "", str(value or "").strip())[:6]

    def _verify_totp(secret: str, otp: str) -> bool:
        code = _normalize_otp(otp)
        if len(code) != 6:
            return False
        now = int(time.time())
        for step in range(-mfa_totp_window_steps, mfa_totp_window_steps + 1):
            candidate = _totp_code(secret, ts=now + (step * mfa_totp_period_sec))
            if candidate and hmac.compare_digest(candidate, code):
                return True
        return False

    def _build_otpauth_uri(email: str, secret: str) -> str:
        label = str(email or "").strip()
        issuer = "Elara"
        return (
            f"otpauth://totp/{issuer}:{label}"
            f"?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period={mfa_totp_period_sec}"
        )

    def _as_float(value):
        try:
            return float(value)
        except Exception:
            return None

    def _extract_market_price(row: dict) -> float | None:
        if not isinstance(row, dict):
            return None
        for key in ("close", "entry", "price", "last", "mark_price"):
            price = _as_float(row.get(key))
            if price is not None and price > 0:
                return float(price)
        return None

    def _normalize_market_symbol(value) -> str:
        return re.sub(r"[^A-Z0-9_:/.\-]", "", str(value or "").strip().upper())[:32]

    def _pick_market_rows(rows, *, max_rows: int = 60):
        if not isinstance(rows, list):
            return []
        picked = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_market_symbol(row.get("symbol"))
            price = _extract_market_price(row)
            if not symbol or price is None:
                continue
            picked.append({"symbol": symbol, "price": float(price)})
            if len(picked) >= max_rows:
                break
        return picked

    def _frame_last_close(frame) -> float | None:
        if frame is None:
            return None
        try:
            close_series = frame["close"]
        except Exception:
            return None
        try:
            if hasattr(close_series, "dropna"):
                close_series = close_series.dropna()
            if len(close_series) == 0:
                return None
        except Exception:
            return None
        try:
            last_val = close_series.iloc[-1] if hasattr(close_series, "iloc") else close_series[-1]
        except Exception:
            return None
        out = _as_float(last_val)
        if out is None or out <= 0:
            return None
        return float(out)

    def _fallback_market_watchlist(kind: str, *, max_rows: int = 12) -> list[str]:
        out = []
        limit = max(1, int(max_rows))
        if kind == "stock":
            universe_path = str(getattr(Config, "STOCK_UNIVERSE_PATH", "") or "").strip()
            if universe_path and not os.path.isabs(universe_path):
                root_path = str(getattr(Config, "root_path", "") or "").strip()
                if root_path:
                    candidate = os.path.abspath(os.path.join(root_path, universe_path))
                    if os.path.exists(candidate):
                        universe_path = candidate
            if universe_path and os.path.exists(universe_path):
                try:
                    with open(universe_path, "r", encoding="utf-8") as fh:
                        for line in fh:
                            raw = str(line or "").strip()
                            if not raw or raw.startswith("#"):
                                continue
                            sym = _normalize_market_symbol(raw)
                            if sym and sym not in out:
                                out.append(sym)
                            if len(out) >= limit:
                                break
                except Exception:
                    pass
        else:
            raw_universe = str(getattr(Config, "CRYPTO_UNIVERSE", "") or "")
            for token in raw_universe.split(","):
                sym = _normalize_market_symbol(str(token or "").replace(" ", ""))
                if not sym:
                    continue
                if "/" not in sym:
                    sym = _normalize_market_symbol(f"{sym}/USDT")
                if sym and sym not in out:
                    out.append(sym)
                if len(out) >= limit:
                    break
            benchmark = _normalize_market_symbol(getattr(Config, "CRYPTO_BENCHMARK_SYMBOL", ""))
            if benchmark and benchmark not in out:
                out.insert(0, benchmark)
                out = out[:limit]
        return out[:limit]

    def _fallback_market_rows_from_skill(kind: str, skill, *, max_rows: int = 12) -> list[dict]:
        if not skill:
            return []
        symbols = _fallback_market_watchlist(kind, max_rows=max_rows)
        if not symbols:
            return []

        rows = []
        if kind == "stock":
            bulk_map = {}
            if hasattr(skill, "_fetch_bulk_ohlcv"):
                try:
                    bulk_map = skill._fetch_bulk_ohlcv(symbols) or {}
                except Exception:
                    bulk_map = {}
            for sym in symbols:
                frame = bulk_map.get(sym) if isinstance(bulk_map, dict) else None
                if frame is None and hasattr(skill, "_fetch_ohlcv"):
                    try:
                        frame = skill._fetch_ohlcv(sym)
                    except Exception:
                        frame = None
                price = _frame_last_close(frame)
                if price is None:
                    continue
                rows.append({"symbol": sym, "price": float(price)})
                if len(rows) >= max_rows:
                    break
            return rows

        for sym in symbols:
            frame = None
            if hasattr(skill, "_fetch_ohlcv_openbb"):
                try:
                    frame = skill._fetch_ohlcv_openbb(sym)
                except Exception:
                    frame = None
            if hasattr(skill, "_fetch_ohlcv_yfinance"):
                try:
                    if frame is None:
                        frame = skill._fetch_ohlcv_yfinance(sym)
                except Exception:
                    frame = None
            price = _frame_last_close(frame)
            if price is None:
                continue
            rows.append({"symbol": sym, "price": float(price)})
            if len(rows) >= max_rows:
                break
        return rows

    def _get_market_symbol_entry(item: dict, symbol: str) -> dict:
        symbols = item.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
            item["symbols"] = symbols
        entry = symbols.get(symbol)
        if not isinstance(entry, dict):
            entry = {"price": None, "points": [], "updated_at": 0.0}
            symbols[symbol] = entry
        return entry

    def _market_cache_append(kind: str, *, symbol: str, price: float, updated_at: float = 0.0, now_ts: float | None = None):
        item = market_chart_cache.get(kind)
        if not item:
            return
        symbol_text = _normalize_market_symbol(symbol)
        if not symbol_text or price <= 0:
            return
        now_val = float(now_ts if now_ts is not None else time.time())
        entry = _get_market_symbol_entry(item, symbol_text)
        points = list(entry.get("points") or [])
        if points:
            last = points[-1]
            last_price = _as_float((last or {}).get("price"))
            last_ts = _as_float((last or {}).get("ts")) or 0.0
            if last_price is not None and abs(last_price - price) < 1e-12 and (now_val - last_ts) < 1.0:
                entry["price"] = float(price)
                entry["updated_at"] = float(updated_at or now_val)
                return
        points.append({"ts": now_val, "price": float(price)})
        if len(points) > market_chart_points_max:
            points = points[-market_chart_points_max:]
        entry["points"] = points
        entry["price"] = float(price)
        entry["updated_at"] = float(updated_at or now_val)

    def _parse_market_timeframe_seconds(value) -> tuple[str, int]:
        raw = str(value or "").strip().lower()
        alias = {
            "15m": 15 * 60,
            "30m": 30 * 60,
            "1h": 60 * 60,
            "4h": 4 * 60 * 60,
            "12h": 12 * 60 * 60,
            "1d": 24 * 60 * 60,
            "24h": 24 * 60 * 60,
            "all": 0,
        }
        if raw in alias:
            return raw, int(alias[raw])
        if re.fullmatch(r"\d+[mhd]", raw):
            value_num = int(raw[:-1])
            unit = raw[-1]
            mul = 60 if unit == "m" else (60 * 60 if unit == "h" else 24 * 60 * 60)
            return raw, max(0, value_num * mul)
        if raw == "":
            fallback = market_default_timeframe if market_default_timeframe else "1h"
            if fallback in alias:
                return fallback, int(alias[fallback])
            return "1h", 60 * 60
        return "1h", 60 * 60

    def _select_market_symbol(kind: str, requested_symbol: str = "") -> str:
        item = market_chart_cache.get(kind) or {}
        order = list(item.get("symbol_order") or [])
        requested = _normalize_market_symbol(requested_symbol)
        symbols = item.get("symbols")
        if requested and isinstance(symbols, dict) and requested in symbols:
            return requested
        active = _normalize_market_symbol(item.get("active_symbol"))
        if active and isinstance(symbols, dict) and active in symbols:
            return active
        if order:
            return _normalize_market_symbol(order[0])
        if isinstance(symbols, dict) and symbols:
            return _normalize_market_symbol(next(iter(symbols.keys())))
        return ""

    def _build_market_kind_payload(kind: str, *, limit: int, timeframe: str, symbol: str):
        item = market_chart_cache.get(kind) or {}
        symbols = item.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
        selected_symbol = _select_market_symbol(kind, requested_symbol=symbol)
        selected_entry = symbols.get(selected_symbol) if selected_symbol else None
        if not isinstance(selected_entry, dict):
            selected_entry = {}

        timeframe_label, timeframe_seconds = _parse_market_timeframe_seconds(timeframe)
        now_ts = time.time()
        points_raw = list(selected_entry.get("points") or [])
        if timeframe_seconds > 0:
            cutoff = now_ts - timeframe_seconds
            points_raw = [p for p in points_raw if (_as_float((p or {}).get("ts")) or 0.0) >= cutoff]
        points_raw = points_raw[-limit:]

        points = []
        for p in points_raw:
            ts = _as_float((p or {}).get("ts")) or 0.0
            price = _as_float((p or {}).get("price"))
            if price is None:
                continue
            points.append(
                {
                    "ts": datetime.fromtimestamp(ts or now_ts, tz=timezone.utc).isoformat(),
                    "price": float(price),
                }
            )

        first = points[0]["price"] if points else None
        last = points[-1]["price"] if points else None
        change_pct = 0.0
        if first is not None and last is not None and first > 0:
            change_pct = ((last - first) / first) * 100.0

        available = []
        for sym in list(item.get("symbol_order") or []):
            sym_text = _normalize_market_symbol(sym)
            entry = symbols.get(sym_text)
            if not isinstance(entry, dict):
                continue
            price_val = _as_float(entry.get("price"))
            available.append(
                {
                    "symbol": sym_text,
                    "price": float(price_val) if price_val is not None else None,
                    "updated_at": float(entry.get("updated_at") or 0.0),
                }
            )
        if len(available) > market_symbols_max:
            available = available[:market_symbols_max]

        return {
            "symbol": selected_symbol,
            "price": float(last) if last is not None else _as_float(selected_entry.get("price")),
            "change_pct": round(float(change_pct), 4),
            "updated_at": float(selected_entry.get("updated_at") or item.get("updated_at") or 0.0),
            "points": points,
            "available_symbols": available,
            "timeframe": timeframe_label,
            "error": str(item.get("error") or ""),
        }

    def _sync_market_chart_cache(force: bool = False):
        nonlocal market_chart_last_sync_ts
        now_ts = time.time()
        if not force and (now_ts - float(market_chart_last_sync_ts or 0.0)) < market_chart_fetch_interval_sec:
            return
        market_chart_last_sync_ts = now_ts

        jobs = (
            ("stock", _get_stock_screener_skill, float(getattr(Config, "STOCK_SCREEN_API_WAIT_SEC", 1.5))),
            ("crypto", _get_crypto_screener_skill, float(getattr(Config, "CRYPTO_SCREEN_API_WAIT_SEC", 1.2))),
        )

        for kind, getter, wait_sec in jobs:
            cache_item = market_chart_cache.get(kind) or {}
            try:
                skill = getter()
                if not skill or not hasattr(skill, "get_screen_snapshot"):
                    fallback_watchlist = _fallback_market_watchlist(kind, max_rows=min(market_symbols_max, 12))
                    if fallback_watchlist:
                        cache_item["symbol_order"] = fallback_watchlist
                        cache_item["active_symbol"] = fallback_watchlist[0]
                        for sym in fallback_watchlist:
                            entry = _get_market_symbol_entry(cache_item, sym)
                            if "points" not in entry or not isinstance(entry.get("points"), list):
                                entry["points"] = []
                            if "price" not in entry:
                                entry["price"] = None
                            if not _as_float(entry.get("updated_at")):
                                entry["updated_at"] = float(cache_item.get("updated_at") or now_ts)
                        cache_item["updated_at"] = float(cache_item.get("updated_at") or now_ts)
                    cache_item["error"] = "skill_unavailable"
                    continue
                snap = skill.get_screen_snapshot(
                    force_refresh=bool(force),
                    wait_timeout_sec=max(0.2, min(wait_sec, 2.5)),
                ) or {}
                rows = _pick_market_rows(list(snap.get("results") or []), max_rows=market_symbols_max)
                if not rows and (force or market_allow_blocking_fallback):
                    rows = _fallback_market_rows_from_skill(
                        kind,
                        skill,
                        max_rows=min(market_symbols_max, 12),
                    )
                if not rows:
                    fallback_watchlist = _fallback_market_watchlist(kind, max_rows=min(market_symbols_max, 12))
                    if fallback_watchlist:
                        cache_item["symbol_order"] = fallback_watchlist
                        cache_item["active_symbol"] = fallback_watchlist[0]
                        for sym in fallback_watchlist:
                            entry = _get_market_symbol_entry(cache_item, sym)
                            if "points" not in entry or not isinstance(entry.get("points"), list):
                                entry["points"] = []
                            if "price" not in entry:
                                entry["price"] = None
                            if not _as_float(entry.get("updated_at")):
                                entry["updated_at"] = float(cache_item.get("updated_at") or now_ts)
                        cache_item["updated_at"] = float(cache_item.get("updated_at") or now_ts)
                        cache_item["error"] = str(snap.get("error") or "watchlist_only")
                        continue
                    cache_item["error"] = str(snap.get("error") or "no_data")
                    continue
                updated_raw = _as_float(snap.get("updated_at")) or 0.0
                if updated_raw > 1_000_000_000_000:
                    updated_raw = updated_raw / 1000.0

                seen = []
                for row in rows:
                    symbol = str(row.get("symbol") or "")
                    price = _as_float(row.get("price"))
                    if not symbol or price is None:
                        continue
                    seen.append(_normalize_market_symbol(symbol))
                    _market_cache_append(
                        kind,
                        symbol=symbol,
                        price=float(price),
                        updated_at=updated_raw,
                        now_ts=now_ts,
                    )

                cache_item["symbol_order"] = [s for s in seen if s]
                if not _normalize_market_symbol(cache_item.get("active_symbol")) and cache_item["symbol_order"]:
                    cache_item["active_symbol"] = cache_item["symbol_order"][0]
                if cache_item["symbol_order"]:
                    first_symbol = cache_item["symbol_order"][0]
                    cache_item["active_symbol"] = first_symbol
                cache_item["updated_at"] = float(updated_raw or now_ts)
                cache_item["error"] = str(snap.get("error") or "")

                symbols_map = cache_item.get("symbols")
                if isinstance(symbols_map, dict) and len(symbols_map) > market_symbols_max:
                    keep = set(cache_item["symbol_order"][:market_symbols_max])
                    for sym_key in list(symbols_map.keys()):
                        if sym_key not in keep:
                            symbols_map.pop(sym_key, None)
            except Exception as e:
                cache_item["error"] = str(e)

    def _build_market_chart_payload(
        limit: int = 90,
        *,
        stock_symbol: str = "",
        crypto_symbol: str = "",
        stock_timeframe: str = "",
        crypto_timeframe: str = "",
    ):
        lim = max(10, min(int(limit), market_chart_points_max))
        return {
            "stock": _build_market_kind_payload(
                "stock",
                limit=lim,
                timeframe=(stock_timeframe or market_default_timeframe),
                symbol=stock_symbol,
            ),
            "crypto": _build_market_kind_payload(
                "crypto",
                limit=lim,
                timeframe=(crypto_timeframe or market_default_timeframe),
                symbol=crypto_symbol,
            ),
        }

    def _parse_utc_datetime(value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except Exception:
                return None
        return None

    def _normalize_market_kind(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw == "stock":
            return "stock"
        return "crypto"

    def _normalize_alert_symbol(value: str, market_kind: str = "crypto") -> str:
        kind = _normalize_market_kind(market_kind)
        symbol = _normalize_market_symbol(value)
        if kind == "stock":
            return symbol
        if not symbol or "/" in symbol:
            return symbol
        compact = re.sub(r"[\s\-_]", "", symbol)
        quote_candidates = ("USDT", "USDC", "BUSD", "USD", "IDR", "TRY", "EUR", "GBP", "BTC", "ETH", "BNB")
        for quote in quote_candidates:
            if len(compact) > len(quote) and compact.endswith(quote):
                base = compact[: len(compact) - len(quote)]
                if len(base) >= 2:
                    return f"{base}/{quote}"
        return compact

    def _normalize_alert_condition(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"cross_above", "above", "up"}:
            return "cross_above"
        if raw in {"cross_below", "below", "down"}:
            return "cross_below"
        return "cross_above"

    def _normalize_alert_channel(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"telegram", "both", "portal"}:
            return raw
        return "portal"

    def _normalize_alert_scope(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"global", "user"}:
            return raw
        return "user"

    def _get_market_prev_curr_price(kind: str, symbol: str) -> tuple[float | None, float | None]:
        item = market_chart_cache.get(kind) or {}
        symbols = item.get("symbols")
        if not isinstance(symbols, dict):
            return None, None
        symbol_text = _normalize_market_symbol(symbol)
        entry = symbols.get(symbol_text)
        if not isinstance(entry, dict):
            return None, None
        curr = _as_float(entry.get("price"))
        points = list(entry.get("points") or [])
        prev = None
        if len(points) >= 2:
            prev = _as_float((points[-2] or {}).get("price"))
        elif len(points) == 1:
            prev = _as_float((points[-1] or {}).get("price"))
        if curr is None and points:
            curr = _as_float((points[-1] or {}).get("price"))
        return prev, curr

    def _is_alert_crossed(condition: str, target: float, prev_price: float | None, curr_price: float | None) -> bool:
        if curr_price is None or target <= 0:
            return False
        cond = _normalize_alert_condition(condition)
        if prev_price is None:
            if cond == "cross_above":
                return curr_price >= target
            return curr_price <= target
        if cond == "cross_above":
            return prev_price < target <= curr_price
        return prev_price > target >= curr_price

    def _build_market_alert_text(
        *,
        name: str,
        symbol: str,
        market_kind: str,
        condition: str,
        target_price: float,
        trigger_price: float,
    ) -> str:
        kind_text = "SAHAM" if market_kind == "stock" else "CRYPTO"
        cond_text = "tembus atas" if condition == "cross_above" else "tembus bawah"
        name_text = str(name or "").strip()[:60]
        if not name_text:
            name_text = f"{symbol} {cond_text}"
        return (
            f"{name_text} | {kind_text} {symbol} {cond_text} "
            f"target={target_price:.6g} now={trigger_price:.6g}"
        )

    def _evaluate_market_alert_rules(*, force: bool = False) -> dict:
        nonlocal market_alert_last_eval_ts
        now_ts = time.time()
        if not force and (now_ts - float(market_alert_last_eval_ts or 0.0)) < market_alert_eval_interval_sec:
            return {"checked": 0, "triggered": 0, "skipped": True, "ts": now_ts}
        market_alert_last_eval_ts = now_ts

        _sync_market_chart_cache(force=force)
        rules = list(db_manager.get_active_market_alert_rules(limit=2000) or [])
        if not rules:
            return {"checked": 0, "triggered": 0, "skipped": False, "ts": now_ts}

        triggered = 0
        now_dt = datetime.now(timezone.utc)
        for rule in rules:
            try:
                rule_id = int(rule.get("id") or 0)
            except Exception:
                rule_id = 0
            if rule_id <= 0:
                continue

            kind = _normalize_market_kind(rule.get("market_kind"))
            symbol = _normalize_market_symbol(rule.get("symbol"))
            condition = _normalize_alert_condition(rule.get("condition"))
            scope = _normalize_alert_scope(rule.get("scope"))
            owner = str(rule.get("owner_username") or "").strip().lower()[:120] or "anonymous"
            event_username = "global" if scope == "global" else owner
            channel = _normalize_alert_channel(rule.get("notify_channel"))

            try:
                target_price = float(rule.get("target_price") or 0.0)
            except Exception:
                target_price = 0.0
            if not symbol or target_price <= 0:
                continue
            try:
                cooldown_sec = int(rule.get("cooldown_sec") or market_alert_default_cooldown_sec)
            except Exception:
                cooldown_sec = market_alert_default_cooldown_sec
            cooldown_sec = max(30, min(cooldown_sec, 7 * 24 * 60 * 60))

            last_triggered_dt = _parse_utc_datetime(rule.get("last_triggered_at"))
            if last_triggered_dt is not None:
                age = (now_dt - last_triggered_dt).total_seconds()
                if age < cooldown_sec:
                    continue

            prev_price, curr_price = _get_market_prev_curr_price(kind, symbol)
            if not _is_alert_crossed(condition, target_price, prev_price, curr_price):
                continue
            if curr_price is None:
                continue

            alert_text = _build_market_alert_text(
                name=str(rule.get("name") or ""),
                symbol=symbol,
                market_kind=kind,
                condition=condition,
                target_price=target_price,
                trigger_price=float(curr_price),
            )
            delivered = False
            if channel in {"telegram", "both"} and market_alert_telegram_ready:
                try:
                    _send_telegram_text(f"[MARKET ALERT]\n{alert_text}")
                    delivered = True
                except Exception:
                    delivered = False
            if channel == "portal":
                delivered = True

            event_row = db_manager.record_market_alert_event(
                rule_id=rule_id,
                username=event_username,
                market_kind=kind,
                symbol=symbol,
                condition=condition,
                target_price=target_price,
                trigger_price=float(curr_price),
                notify_channel=channel,
                delivered=delivered,
                message=alert_text,
            )
            db_manager.touch_market_alert_rule_trigger(
                rule_id=rule_id,
                trigger_price=float(curr_price),
                triggered_at=now_dt,
            )
            payload = dict(event_row or {})
            payload["rule_id"] = rule_id
            payload["scope"] = scope
            payload["owner_username"] = owner
            payload["ts"] = now_ts
            try:
                socketio.emit("market_alert_event", payload)
            except Exception as e:
                log.debug(f"market alert socket emit skipped: {e}")
            triggered += 1

        return {"checked": len(rules), "triggered": int(triggered), "skipped": False, "ts": now_ts}

    def _sanitize_market_subscription(payload=None) -> dict:
        src = payload if isinstance(payload, dict) else {}
        stock_symbol = _normalize_market_symbol(src.get("stock_symbol"))
        crypto_symbol = _normalize_market_symbol(src.get("crypto_symbol"))
        stock_timeframe, _ = _parse_market_timeframe_seconds(src.get("stock_timeframe"))
        crypto_timeframe, _ = _parse_market_timeframe_seconds(src.get("crypto_timeframe"))
        try:
            limit = int(src.get("limit", 120))
        except Exception:
            limit = 120
        limit = max(10, min(limit, market_chart_points_max))
        refresh = bool(_coerce_bool(src.get("refresh")))
        return {
            "stock_symbol": stock_symbol,
            "crypto_symbol": crypto_symbol,
            "stock_timeframe": stock_timeframe or market_default_timeframe,
            "crypto_timeframe": crypto_timeframe or market_default_timeframe,
            "limit": limit,
            "refresh": refresh,
        }

    def _market_stream_payload_from_subscription(subscription: dict):
        sub = _sanitize_market_subscription(subscription)
        payload = _build_market_chart_payload(
            limit=int(sub["limit"]),
            stock_symbol=sub["stock_symbol"],
            crypto_symbol=sub["crypto_symbol"],
            stock_timeframe=sub["stock_timeframe"],
            crypto_timeframe=sub["crypto_timeframe"],
        )
        payload["selection"] = {
            "stock_symbol": sub["stock_symbol"],
            "crypto_symbol": sub["crypto_symbol"],
            "stock_timeframe": sub["stock_timeframe"],
            "crypto_timeframe": sub["crypto_timeframe"],
        }
        payload["ts"] = time.time()
        return payload

    def _upsert_market_subscription(sid: str, payload=None) -> dict | None:
        sid_text = str(sid or "").strip()
        if not sid_text:
            return None
        sub = _sanitize_market_subscription(payload)
        with market_socket_sub_lock:
            market_socket_subscriptions[sid_text] = dict(sub)
        return sub

    def _drop_market_subscription(sid: str):
        sid_text = str(sid or "").strip()
        if not sid_text:
            return
        with market_socket_sub_lock:
            market_socket_subscriptions.pop(sid_text, None)

    def _snapshot_market_subscriptions():
        with market_socket_sub_lock:
            return [(sid, dict(cfg)) for sid, cfg in market_socket_subscriptions.items()]

    def _emit_market_payload_to_sid(sid: str, *, force_refresh: bool = False):
        sid_text = str(sid or "").strip()
        if not sid_text:
            return
        with market_socket_sub_lock:
            sub = dict(market_socket_subscriptions.get(sid_text) or {})
        if not sub:
            return
        _sync_market_chart_cache(force=force_refresh)
        payload = _market_stream_payload_from_subscription(sub)
        try:
            socketio.emit("market_realtime_event", payload, room=sid_text)
        except Exception as e:
            log.debug(f"market realtime emit skipped sid={sid_text}: {e}")

    def _broadcast_market_payload_to_subscribers(*, force_refresh: bool = False):
        subs = _snapshot_market_subscriptions()
        if not subs:
            return 0
        _sync_market_chart_cache(force=force_refresh)
        sent = 0
        for sid, sub in subs:
            payload = _market_stream_payload_from_subscription(sub)
            try:
                socketio.emit("market_realtime_event", payload, room=sid)
                sent += 1
            except Exception as e:
                log.debug(f"market realtime broadcast skipped sid={sid}: {e}")
        return sent

    def _market_stream_loop():
        while not market_stream_stop_event.is_set():
            try:
                _broadcast_market_payload_to_subscribers(force_refresh=False)
                _evaluate_market_alert_rules(force=False)
            except Exception as e:
                log.debug(f"market stream loop tick error: {e}")
            wait_sec = max(2.0, market_chart_fetch_interval_sec)
            market_stream_stop_event.wait(wait_sec)

    def _ensure_market_stream_loop_started():
        nonlocal market_stream_started
        with market_socket_sub_lock:
            if market_stream_started:
                return
            market_stream_started = True
        try:
            socketio.start_background_task(_market_stream_loop)
        except Exception as e:
            with market_socket_sub_lock:
                market_stream_started = False
            log.warning(f"market stream background task failed: {e}")

    def _resolve_activity_actor(default: str = "anonymous") -> str:
        actor = ""
        try:
            actor = str(get_jwt_identity() or "").strip()
        except Exception:
            actor = ""
        if actor:
            return actor
        try:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict):
                for key in ("email", "username", "user_id"):
                    candidate = _compact_text(payload.get(key), max_len=120)
                    if candidate:
                        return candidate
        except Exception:
            pass
        return str(default or "anonymous").strip() or "anonymous"

    def _resolve_command_user_id(default: str = "web_user") -> str:
        actor = ""
        try:
            actor = str(get_jwt_identity() or "").strip().lower()
        except Exception:
            actor = ""
        if actor:
            return actor
        try:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict):
                for key in ("user_id", "username", "email"):
                    candidate = _compact_text(payload.get(key), max_len=120)
                    if candidate:
                        return candidate.strip().lower()
        except Exception:
            pass
        return str(default or "web_user").strip().lower() or "web_user"

    def _activity_alert_fingerprint(payload: dict) -> str:
        action = str(payload.get("action") or "").strip().lower()
        path = str(payload.get("path") or "").strip().lower()
        status = str(payload.get("status_code") if payload.get("status_code") is not None else "")
        return f"{action}|{status}|{path}"

    def _is_critical_activity(payload: dict) -> bool:
        action = str(payload.get("action") or "").strip().lower()
        try:
            status_code = int(payload.get("status_code") or 0)
        except Exception:
            status_code = 0
        if status_code >= 500:
            return True
        if "failed" in action or "rejected" in action:
            return True
        return False

    def _send_telegram_text(text: str):
        token = str(getattr(Config, "BOT_TOKEN", "") or "").strip()
        chat_id = int(getattr(Config, "TG_ALLOWED_USER", 0) or 0)
        if not token or chat_id == 0:
            return

        def _send():
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            body = json.dumps({"chat_id": chat_id, "text": str(text or "")}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()

        try:
            async_runner.run_coroutine_fire_and_forget(asyncio.to_thread(_send))
        except Exception as e:
            log.debug(f"telegram send skipped: {e}")

    def _flush_activity_alert_digest(now_ts: float, *, force: bool = False):
        nonlocal activity_alert_digest_last_sent
        if not activity_alert_tg_enabled:
            return
        pending = list(activity_alert_digest_buffer)
        if not pending:
            return
        if not force:
            if (now_ts - float(activity_alert_digest_last_sent or 0.0)) < activity_alert_digest_sec:
                return
            if len(pending) < activity_alert_digest_min_events:
                return

        by_user = Counter()
        by_action = Counter()
        by_status = Counter()
        for item in pending:
            actor = str(item.get("username") or "anonymous")
            action = str(item.get("action") or "-")
            try:
                code = int(item.get("status_code") or 0)
            except Exception:
                code = 0
            status_bucket = f"{int(code / 100)}xx" if code >= 100 else "unknown"
            by_user[actor] += 1
            by_action[action] += 1
            by_status[status_bucket] += 1

        def _top(counter_obj: Counter, limit: int = 3) -> str:
            if not counter_obj:
                return "-"
            return ", ".join([f"{name}:{count}" for name, count in counter_obj.most_common(limit)])

        text = (
            "[WEB CRITICAL DIGEST]\n"
            f"events={len(pending)}\n"
            f"window_sec={activity_alert_digest_sec}\n"
            f"top_users={_top(by_user)}\n"
            f"top_actions={_top(by_action)}\n"
            f"status_mix={_top(by_status)}"
        )
        _send_telegram_text(text)
        activity_alert_digest_buffer.clear()
        activity_alert_digest_last_sent = now_ts

    def _send_activity_alert_telegram(payload: dict):
        if not activity_alert_tg_enabled:
            return
        if not _is_critical_activity(payload):
            return

        now = time.time()
        key = _activity_alert_fingerprint(payload)
        action_text = str(payload.get("action") or "").strip().lower()
        actor = str(payload.get("username") or "anonymous").strip().lower()
        user_action_key = f"{actor}|{action_text}"

        last_user_action = float(activity_alert_user_action_last_sent.get(user_action_key, 0.0) or 0.0)
        user_action_suppressed = (now - last_user_action) < activity_alert_user_action_cooldown_sec
        if not user_action_suppressed:
            activity_alert_user_action_last_sent[user_action_key] = now

        last_ts = float(activity_alert_last_sent.get(key, 0.0) or 0.0)
        fingerprint_suppressed = (now - last_ts) < activity_alert_cooldown_sec
        if not fingerprint_suppressed:
            activity_alert_last_sent[key] = now

        if len(activity_alert_last_sent) > activity_alert_max_memory:
            threshold = now - max(activity_alert_cooldown_sec, 60)
            stale = [k for k, ts in activity_alert_last_sent.items() if float(ts) < threshold]
            for old_key in stale:
                activity_alert_last_sent.pop(old_key, None)
            if len(activity_alert_last_sent) > activity_alert_max_memory:
                # Keep the newest slice only.
                newest = sorted(activity_alert_last_sent.items(), key=lambda item: float(item[1]), reverse=True)[
                    :activity_alert_max_memory
                ]
                activity_alert_last_sent.clear()
                activity_alert_last_sent.update({k: ts for k, ts in newest})

        if len(activity_alert_user_action_last_sent) > activity_alert_max_memory:
            threshold = now - max(activity_alert_user_action_cooldown_sec, 60)
            stale = [k for k, ts in activity_alert_user_action_last_sent.items() if float(ts) < threshold]
            for old_key in stale:
                activity_alert_user_action_last_sent.pop(old_key, None)

        activity_alert_digest_buffer.append(
            {
                "timestamp": payload.get("timestamp"),
                "username": payload.get("username"),
                "action": payload.get("action"),
                "status_code": payload.get("status_code"),
                "path": payload.get("path"),
                "method": payload.get("method"),
            }
        )
        max_digest_size = activity_alert_max_memory * 2
        if len(activity_alert_digest_buffer) > max_digest_size:
            del activity_alert_digest_buffer[: len(activity_alert_digest_buffer) - max_digest_size]

        if not user_action_suppressed and not fingerprint_suppressed:
            text = (
                "[WEB CRITICAL ACTIVITY]\n"
                f"time={payload.get('timestamp')}\n"
                f"user={payload.get('username')}\n"
                f"action={payload.get('action')}\n"
                f"status={payload.get('status_code')}\n"
                f"endpoint={payload.get('method')} {payload.get('path')}\n"
                f"details={payload.get('details')}"
            )
            _send_telegram_text(text)

        _flush_activity_alert_digest(now_ts=now, force=False)

    def _track_user_activity(action: str, status_code: int, *, details: str = "", username: str = ""):
        action_text = _compact_text(action, max_len=120)
        if not action_text:
            return
        actor = _compact_text(username, max_len=120) or _resolve_activity_actor()
        detail_text = _compact_text(details, max_len=255)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "username": actor or "anonymous",
            "action": action_text,
            "path": request.path,
            "method": request.method,
            "status_code": int(status_code),
            "details": detail_text,
        }
        try:
            db_manager.log_user_activity(
                username=payload["username"],
                action=payload["action"],
                path=payload["path"],
                method=payload["method"],
                status_code=payload["status_code"],
                details=payload["details"],
            )
        except Exception as e:
            log.debug(f"user activity logging skipped: {e}")
        try:
            socketio.emit("admin_activity_event", payload)
        except Exception as e:
            log.debug(f"user activity stream emit skipped: {e}")
        _send_activity_alert_telegram(payload)

    def _audit_action(event: str, status_code: int, **fields):
        actor = ""
        try:
            actor = str(get_jwt_identity() or "")
        except Exception:
            actor = ""
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        emit_audit_log(
            log,
            event=event,
            status=status_code,
            method=request.method,
            path=request.path,
            ip=ip,
            actor=actor,
            **fields,
        )

    def _request_path_label() -> str:
        try:
            rule = getattr(request, "url_rule", None)
            if rule and getattr(rule, "rule", None):
                return str(rule.rule)
        except Exception:
            pass
        return str(getattr(request, "path", "") or "/")

    @app.before_request
    def _start_observability_context():
        if not observability_enabled:
            return None
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or uuid4().hex
        )
        trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
        g.observability_started_at = time.perf_counter()
        g.correlation_id = str(correlation_id)[:128]
        g.trace_id = str(trace_id)[:128]
        g.observability_context_token = set_observability_context(
            correlation_id=g.correlation_id,
            request_id=g.correlation_id,
            trace_id=g.trace_id,
            method=request.method,
            path=request.path,
        )
        return None

    @app.before_request
    def _trusted_host_gate():
        if _host_valid():
            return None
        log.warning(f"Blocked request with invalid Host header: {request.host}")
        if request.path.startswith("/api/"):
            return jsonify({"message": "Invalid Host header"}), 400
        return "Invalid Host header", 400

    @app.before_request
    def _json_payload_guard():
        if request.method not in {"POST", "PUT", "PATCH"}:
            return None
        content_length = int(request.content_length or 0)
        max_content = int(app.config.get("MAX_CONTENT_LENGTH") or 0)
        if max_content and content_length > max_content:
            return jsonify({"message": "Payload too large"}), 413
        if not request.path.startswith("/api/"):
            return None
        if not request.is_json:
            return None
        payload = request.get_json(silent=True)
        if payload is None:
            return None
        total_keys = _count_json_keys(payload, depth=0)
        if total_keys > max_json_keys:
            log.warning(
                "JSON payload key-count blocked: %s keys exceeds max=%s for path=%s",
                total_keys,
                max_json_keys,
                request.path,
            )
            return jsonify({"message": "Payload JSON too complex"}), 400
        return None

    @app.before_request
    def _set_rate_role():
        g.limiter_exempt = False
        g.current_user_role = None
        try:
            verify_jwt_in_request(optional=True)
            ident = get_jwt_identity()
            if ident:
                _touch_current_user_session()
            role = db_manager.get_user_role(ident) if ident else None
            g.current_user_role = role
            if role in ("admin", "superuser"):
                g.limiter_exempt = True
        except Exception as e:
            log.debug(f"Optional JWT verification skipped: {e}")

    @app.before_request
    def _csrf_gate():
        if not csrf_enabled or not csrf:
            return None
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return None
        if request.path.startswith("/socket.io/"):
            return None
        csrf.protect()
        return None

    def _require_admin_role_for_selfops():
        try:
            verify_jwt_in_request()
            role = getattr(g, "current_user_role", None)
            if role in ("admin", "superuser"):
                return None
            # fallback role check if before_request role was not populated yet
            ident = get_jwt_identity()
            role = db_manager.get_user_role(ident) if ident else None
            if role in ("admin", "superuser"):
                g.current_user_role = role
                return None
            return jsonify({"message": "Forbidden"}), 403
        except Exception as e:
            log.warning(f"Self-ops auth failed: {e}")
            return jsonify({"message": "Unauthorized"}), 401
    socketio = SocketIO(
        app,
        cors_allowed_origins=cors_origins,
        async_mode="threading",
        ping_timeout=20,
        ping_interval=10
    )

    # Guest access disabled by default; env flag ALLOW_GUEST_DASHBOARD can enable it explicitly
    AUTH_DISABLED = bool(getattr(Config, "ALLOW_GUEST_DASHBOARD", False))
    LEGACY_UI_ENABLED = bool(getattr(Config, "WEB_LEGACY_DASHBOARD_ENABLED", False))
    TG_DASHBOARD_ENABLED = bool(getattr(Config, "WEB_TG_DASHBOARD_ENABLED", LEGACY_UI_ENABLED))

    def _legacy_redirect(source: str):
        target = f"/portal?legacy={source}"
        response = redirect(target)
        response.headers.setdefault("X-Elara-Deprecated", "true")
        response.headers.setdefault("X-Elara-Deprecated-Source", source)
        return response

    def _csrf_token_value() -> str:
        if not csrf_enabled or not generate_csrf:
            return ""
        try:
            return str(generate_csrf() or "")
        except Exception as e:
            log.warning(f"Failed generating CSRF token: {e}")
            return ""

    def _get_stock_screener_skill():
        skill_manager = getattr(elara_skill, "skill_manager", None)
        if not skill_manager:
            return None
        return skill_manager.intent_map.get("screen_stocks")

    def _get_crypto_screener_skill():
        skill_manager = getattr(elara_skill, "skill_manager", None)
        if not skill_manager:
            return None
        return skill_manager.intent_map.get("screen_crypto")

    def _get_llm_skill():
        skill_manager = getattr(elara_skill, "skill_manager", None)
        if not skill_manager:
            return None
        for intent in ("general_conversation", "unknown"):
            skill = skill_manager.intent_map.get(intent)
            if skill and hasattr(skill, "get_markdown_skill_analytics"):
                return skill
        for skill in list(getattr(skill_manager, "skills", []) or []):
            if hasattr(skill, "get_markdown_skill_analytics"):
                return skill
        return None

    def _markdown_skill_analytics_payload(include_recent: bool = False):
        llm_skill = _get_llm_skill()
        if not llm_skill:
            return {
                "enabled": False,
                "available": False,
                "message": "LLM skill analytics unavailable",
            }
        getter = getattr(llm_skill, "get_markdown_skill_analytics", None)
        if not callable(getter):
            return {
                "enabled": False,
                "available": False,
                "message": "LLM skill analytics not implemented",
            }
        try:
            payload = getter() or {}
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "message": f"analytics_error: {e}",
            }

        payload = dict(payload)
        payload["available"] = True
        if not include_recent:
            payload.pop("recent_events", None)
        return payload

    def _markdown_skill_catalog_payload(user_id: str, query: str = "", limit: int = 30):
        llm_skill = _get_llm_skill()
        if llm_skill and hasattr(llm_skill, "get_markdown_skill_catalog"):
            try:
                return llm_skill.get_markdown_skill_catalog(
                    user_id=user_id,
                    query=query,
                    limit=limit,
                ) or {
                    "enabled": False,
                    "available": False,
                    "count": 0,
                    "items": [],
                }
            except Exception as e:
                return {
                    "enabled": False,
                    "available": False,
                    "count": 0,
                    "items": [],
                    "message": f"catalog_error: {e}",
                }
        return {
            "enabled": False,
            "available": False,
            "count": 0,
            "items": [],
            "message": "LLM skill catalog unavailable",
        }

    def _submit_markdown_feedback_payload(
        user_id: str,
        user_msg: str,
        feedback_value,
        skill_name: str = "",
        match_kind: str = "",
        note: str = "",
    ):
        llm_skill = _get_llm_skill()
        if not llm_skill:
            return {
                "enabled": False,
                "available": False,
                "recorded": False,
                "message": "LLM skill feedback unavailable",
            }
        submitter = getattr(llm_skill, "submit_markdown_route_feedback", None)
        if not callable(submitter):
            return {
                "enabled": False,
                "available": False,
                "recorded": False,
                "message": "feedback_not_implemented",
            }
        try:
            payload = submitter(
                user_id=user_id,
                user_msg=user_msg,
                feedback=feedback_value,
                skill_name=skill_name,
                match_kind=match_kind,
                note=note,
            ) or {}
            return dict(payload)
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "recorded": False,
                "message": f"feedback_error: {e}",
            }

    def _markdown_skill_evaluation_payload(
        user_id: str,
        top_n: int = 12,
        include_recent: bool = False,
        query: str = "",
    ):
        llm_skill = _get_llm_skill()
        if not llm_skill:
            return {
                "enabled": False,
                "available": False,
                "message": "LLM skill evaluation unavailable",
            }

        getter = getattr(llm_skill, "get_markdown_skill_evaluation", None)
        if not callable(getter):
            return {
                "enabled": False,
                "available": False,
                "message": "evaluation_not_implemented",
            }

        try:
            payload = dict(getter(top_n=top_n, include_recent=include_recent) or {})
            payload["available"] = bool(payload.get("available", True))
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "message": f"evaluation_error: {e}",
            }

        if query:
            previewer = getattr(llm_skill, "preview_markdown_route", None)
            if callable(previewer):
                try:
                    payload["route_preview"] = previewer(
                        query,
                        user_id=user_id,
                        top_k=max(3, min(int(top_n or 6), 12)),
                    ) or {}
                except Exception as e:
                    payload["route_preview"] = {"matched": False, "message": f"preview_error: {e}"}
        return payload

    def _markdown_route_preview_payload(user_id: str, query: str, top_k: int = 6):
        llm_skill = _get_llm_skill()
        if not llm_skill:
            return {
                "enabled": False,
                "available": False,
                "message": "LLM skill route preview unavailable",
            }
        previewer = getattr(llm_skill, "preview_markdown_route", None)
        if not callable(previewer):
            return {
                "enabled": False,
                "available": False,
                "message": "route_preview_not_implemented",
            }
        try:
            return dict(previewer(query, user_id=user_id, top_k=max(1, min(int(top_k or 6), 20))) or {})
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "message": f"route_preview_error: {e}",
            }

    def _reset_markdown_feedback_payload():
        llm_skill = _get_llm_skill()
        if not llm_skill:
            return {
                "enabled": False,
                "available": False,
                "reset": False,
                "message": "LLM skill feedback unavailable",
            }
        resetter = getattr(llm_skill, "reset_markdown_route_feedback", None)
        if not callable(resetter):
            return {
                "enabled": False,
                "available": False,
                "reset": False,
                "message": "feedback_reset_not_implemented",
            }
        try:
            return dict(resetter() or {})
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "reset": False,
                "message": f"feedback_reset_error: {e}",
            }

    def _markdown_registry_payload():
        skill_manager = getattr(elara_skill, "skill_manager", None)
        if not skill_manager:
            return {
                "enabled": False,
                "available": False,
                "message": "skill_manager_unavailable",
            }
        getter = getattr(skill_manager, "get_markdown_registry_stats", None)
        if not callable(getter):
            return {
                "enabled": False,
                "available": False,
                "message": "registry_stats_not_implemented",
            }
        try:
            payload = dict(getter() or {})
            payload["available"] = bool(payload.get("available", True))
            return payload
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "message": f"registry_stats_error: {e}",
            }

    def _refresh_markdown_registry_payload(force: bool = True, reason: str = "admin_api"):
        skill_manager = getattr(elara_skill, "skill_manager", None)
        if not skill_manager:
            return {
                "enabled": False,
                "available": False,
                "refreshed": False,
                "message": "skill_manager_unavailable",
            }
        refresher = getattr(skill_manager, "refresh_markdown_registry", None)
        if not callable(refresher):
            return {
                "enabled": False,
                "available": False,
                "refreshed": False,
                "message": "registry_refresh_not_implemented",
            }
        try:
            payload = dict(refresher(force=bool(force), reason=str(reason or "admin_api")) or {})
            payload["available"] = bool(payload.get("available", True))
            return payload
        except Exception as e:
            return {
                "enabled": False,
                "available": False,
                "refreshed": False,
                "message": f"registry_refresh_error: {e}",
            }

    _screener_visual_cache_lock = threading.Lock()
    _screener_visual_cache = {}
    _screener_visual_cache_ttl_sec = max(
        1.0, float(getattr(Config, "SCREENER_VISUAL_CACHE_TTL_SEC", 6.0) or 6.0)
    )

    def _get_screener_visual_cache(kind: str, updated_at: float, limit: int):
        key = (str(kind or "unknown"), int(limit), int(updated_at or 0))
        now = time.time()
        with _screener_visual_cache_lock:
            entry = _screener_visual_cache.get(key)
            if entry and (now - entry["ts"]) <= _screener_visual_cache_ttl_sec:
                return entry["payload"]
        return None

    def _set_screener_visual_cache(kind: str, updated_at: float, limit: int, payload: dict):
        key = (str(kind or "unknown"), int(limit), int(updated_at or 0))
        now = time.time()
        with _screener_visual_cache_lock:
            _screener_visual_cache[key] = {"ts": now, "payload": payload}
            if len(_screener_visual_cache) > 24:
                for stale_key, _ in sorted(
                    _screener_visual_cache.items(), key=lambda item: item[1]["ts"]
                )[:-24]:
                    _screener_visual_cache.pop(stale_key, None)
            for stale_key, entry in list(_screener_visual_cache.items()):
                if (now - entry["ts"]) > _screener_visual_cache_ttl_sec:
                    _screener_visual_cache.pop(stale_key, None)

    def _build_screener_visual_payload(results):
        rows = []
        scatter = []
        bubble = []
        score_hist = {"0-40": 0, "40-50": 0, "50-60": 0, "60-70": 0, "70-80": 0, "80-100": 0}
        tag_counter = Counter()

        total_score = 0.0
        total_rr = 0.0
        total_liq = 0.0

        for idx, r in enumerate(results, 1):
            symbol = str(r.get("symbol") or "")
            score = float(r.get("score") or 0.0)
            rr = float(r.get("rr") or 0.0)
            atr_pct = float(r.get("atr_pct") or 0.0)
            vol_ratio = float(r.get("vol_ratio") or 0.0)
            liquidity = float(r.get("liquidity") or 0.0)
            close = float(r.get("close") or 0.0)
            entry = float(r.get("entry") or 0.0)
            stop = float(r.get("stop") or 0.0)
            tp1 = float(r.get("tp1") or 0.0)
            tp2 = float(r.get("tp2") or 0.0)
            tags = [str(t) for t in (r.get("tags") or [])]

            rows.append({
                "rank": idx,
                "symbol": symbol,
                "score": round(score, 2),
                "setup": r.get("setup"),
                "close": round(close, 2),
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "tp1": round(tp1, 2),
                "tp2": round(tp2, 2),
                "rr": round(rr, 3),
                "atr_pct": round(atr_pct, 3),
                "vol_ratio": round(vol_ratio, 3),
                "rs_edge": round(float(r.get("rs_edge") or 0.0), 3),
                "liquidity": round(liquidity, 2),
                "tags": tags,
            })

            scatter.append({"x": round(score, 2), "y": round(rr, 3), "symbol": symbol})
            bubble.append({
                "x": round(atr_pct, 3),
                "y": round(vol_ratio, 3),
                "r": max(4, min(20, int((liquidity / 1_000_000_000) ** 0.5))),
                "symbol": symbol,
            })

            if score < 40:
                score_hist["0-40"] += 1
            elif score < 50:
                score_hist["40-50"] += 1
            elif score < 60:
                score_hist["50-60"] += 1
            elif score < 70:
                score_hist["60-70"] += 1
            elif score < 80:
                score_hist["70-80"] += 1
            else:
                score_hist["80-100"] += 1

            tag_counter.update(tags)
            total_score += score
            total_rr += rr
            total_liq += liquidity

        count = len(rows)
        top_tags = [{"tag": k, "count": v} for k, v in tag_counter.most_common(12)]
        top_scores = sorted(rows, key=lambda x: x.get("score", 0), reverse=True)[:10]

        columns = [
            {"key": "rank", "label": "#", "type": "number"},
            {"key": "symbol", "label": "Symbol", "type": "string"},
            {"key": "score", "label": "Score", "type": "number"},
            {"key": "setup", "label": "Setup", "type": "string"},
            {"key": "entry", "label": "Entry", "type": "number"},
            {"key": "stop", "label": "Stop", "type": "number"},
            {"key": "tp1", "label": "TP1", "type": "number"},
            {"key": "tp2", "label": "TP2", "type": "number"},
            {"key": "rr", "label": "RR", "type": "number"},
            {"key": "atr_pct", "label": "ATR%", "type": "number"},
            {"key": "vol_ratio", "label": "Vol x", "type": "number"},
            {"key": "liquidity", "label": "Liquidity", "type": "number"},
        ]

        summary = {
            "count": count,
            "avg_score": round(total_score / count, 3) if count else 0.0,
            "avg_rr": round(total_rr / count, 3) if count else 0.0,
            "total_liquidity": round(total_liq, 2),
        }

        return {
            "table": {"columns": columns, "rows": rows},
            "charts": {
                "score_vs_rr": scatter,
                "atr_vs_volume": bubble,
                "score_histogram": [{"range": k, "count": v} for k, v in score_hist.items()],
                "top_scores": top_scores,
                "tag_frequency": top_tags,
            },
            "summary": summary,
        }

    if CSRFError:
        @app.errorhandler(CSRFError)
        def _handle_csrf_error(error):
            message = "CSRF token invalid or missing."
            log.warning(f"CSRF rejected request: {error}")
            if request.path.startswith("/api/"):
                return jsonify({"message": message}), 400
            return message, 400

    @app.errorhandler(HTTPException)
    def _handle_http_error(error):
        if not request.path.startswith("/api/"):
            return error
        code = int(getattr(error, "code", 500) or 500)
        generic = {
            400: "Invalid request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            413: "Payload too large",
            422: "Invalid request",
            429: "Too many requests",
        }
        message = generic.get(code, "Request error")
        if code >= 500:
            message = "Internal Error"
        return jsonify({"message": message}), code

    @app.errorhandler(Exception)
    def _handle_unexpected_error(error):
        log.error(f"Unhandled web error: {error}", exc_info=True)
        if request.path.startswith("/api/"):
            return jsonify({"message": "Internal Error"}), 500
        return "Internal Error", 500

    @app.after_request
    def _inject_security_headers(resp):
        if observability_enabled:
            started_at = getattr(g, "observability_started_at", None)
            duration_sec = 0.0
            if started_at is not None:
                duration_sec = max(0.0, time.perf_counter() - float(started_at))
            path_label = _request_path_label()
            record_http_request(request.method, path_label, int(resp.status_code or 0), duration_sec)
            correlation_id = str(getattr(g, "correlation_id", "") or "")
            trace_id = str(getattr(g, "trace_id", "") or "")
            if correlation_id:
                resp.headers.setdefault("X-Correlation-ID", correlation_id)
            if trace_id:
                resp.headers.setdefault("X-Trace-ID", trace_id)
            if duration_sec * 1000.0 >= slow_request_ms:
                log.warning(
                    "Slow HTTP request method=%s path=%s status=%s duration_ms=%.1f",
                    request.method,
                    path_label,
                    resp.status_code,
                    duration_sec * 1000.0,
                )
            reset_observability_context(getattr(g, "observability_context_token", None))
        if bool(getattr(Config, "WEB_SECURITY_HEADERS", True)):
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("X-Frame-Options", "DENY")
            resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            if bool(getattr(Config, "WEB_FORCE_HTTPS", False)):
                resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.route('/')
    def index():
        if not LEGACY_UI_ENABLED:
            return _legacy_redirect("index")
        if not AUTH_DISABLED:
            auth_redirect = _require_html_session(admin_only=False)
            if auth_redirect:
                return auth_redirect
        return render_template(
            'index.html',
            guest_allowed=AUTH_DISABLED,
            csrf_token_value=_csrf_token_value(),
        )

    @app.route('/dashboard')
    def dashboard():
        if not LEGACY_UI_ENABLED:
            return _legacy_redirect("dashboard")
        if not AUTH_DISABLED:
            auth_redirect = _require_html_session(admin_only=False)
            if auth_redirect:
                return auth_redirect
        return render_template(
            'index.html',
            guest_allowed=AUTH_DISABLED,
            csrf_token_value=_csrf_token_value(),
        )

    @app.route('/portal')
    def portal():
        auth_redirect = _require_html_session(admin_only=False)
        if auth_redirect:
            return auth_redirect
        legacy_source = _compact_text(request.args.get("legacy"), max_len=32)
        return render_template(
            'portal.html',
            csrf_token_value=_csrf_token_value(),
            default_view='auto',
            legacy_enabled=LEGACY_UI_ENABLED,
            terminal_enabled=LEGACY_UI_ENABLED,
            legacy_source=legacy_source,
        )

    @app.route('/terminal')
    @app.route('/dashboard/terminal')
    def terminal_dashboard():
        if not LEGACY_UI_ENABLED:
            return _legacy_redirect("terminal")
        auth_redirect = _require_html_session(admin_only=False)
        if auth_redirect:
            return auth_redirect
        return render_template(
            'terminal.html',
            csrf_token_value=_csrf_token_value(),
        )

    @app.route('/dashboard/user')
    @app.route('/dashboard/admin')
    def role_dashboard():
        admin_only = bool(request.path.endswith('/dashboard/admin'))
        auth_redirect = _require_html_session(admin_only=admin_only)
        if auth_redirect:
            return auth_redirect
        default_view = 'admin' if request.path.endswith('/dashboard/admin') else 'user'
        legacy_source = _compact_text(request.args.get("legacy"), max_len=32)
        return render_template(
            'portal.html',
            csrf_token_value=_csrf_token_value(),
            default_view=default_view,
            legacy_enabled=LEGACY_UI_ENABLED,
            terminal_enabled=LEGACY_UI_ENABLED,
            legacy_source=legacy_source,
        )

    @app.route('/login')
    @app.route('/signin')
    @app.route('/signup')
    @app.route('/forgot-password')
    @app.route('/reset-password')
    @app.route('/verify-email')
    def login_page():
        path = str(request.path or "").strip().lower()
        mode = "signin"
        if path.endswith("/signup"):
            mode = "signup"
        elif path.endswith("/forgot-password"):
            mode = "forgot"
        elif path.endswith("/reset-password"):
            mode = "reset"
        elif path.endswith("/verify-email"):
            mode = "verify"
        if mode in {"signin", "signup"} and _resolve_identity_optional():
            return redirect("/portal")
        return render_template(
            'login.html',
            csrf_token_value=_csrf_token_value(),
            auth_mode=mode,
            prefill_token=_compact_text(request.args.get("token"), max_len=512),
            prefill_email=_compact_text(request.args.get("email"), max_len=max_email_chars),
        )

    @app.route('/account')
    def account_page():
        auth_redirect = _require_html_session(admin_only=False)
        if auth_redirect:
            return auth_redirect
        return render_template(
            'account.html',
            csrf_token_value=_csrf_token_value(),
        )

    @app.route('/tg/dashboard')
    def tg_dashboard():
        if not TG_DASHBOARD_ENABLED:
            return _legacy_redirect("tg")
        token = request.args.get('token', '')
        cookie_token = request.cookies.get("tg_webapp_auth") or ""
        valid_token = str(Config.TG_WEBAPP_TOKEN or "")
        token_ok = bool(
            valid_token
            and (
                (token and secrets.compare_digest(str(token), valid_token))
                or (cookie_token and secrets.compare_digest(str(cookie_token), valid_token))
            )
        )
        if not token_ok:
            return "Unauthorized", 403
        if token:
            response = redirect("/tg/dashboard")
            response.set_cookie(
                "tg_webapp_auth",
                Config.TG_WEBAPP_TOKEN,
                max_age=3600,
                httponly=True,
                samesite="Lax",
                secure=bool(getattr(Config, "WEB_FORCE_HTTPS", False)),
            )
            return response
        resp = render_template("tg_dashboard.html", csrf_token_value=_csrf_token_value())
        response = app.make_response(resp)
        if not cookie_token:
            response.set_cookie(
                "tg_webapp_auth",
                Config.TG_WEBAPP_TOKEN,
                max_age=3600,
                httponly=True,
                samesite="Lax",
                secure=bool(getattr(Config, "WEB_FORCE_HTTPS", False)),
            )
        return response

    @app.route('/api/login', methods=['POST'])
    @app.route('/api/signin', methods=['POST'])
    @limiter.limit("20 per minute")
    def login_api():
        """
        Authenticate a user and return a JWT access token.
        ---
        tags:
          - Authentication
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              required:
                - email
                - password
              properties:
                email:
                  type: string
                  example: bos@elara.ai
                password:
                  type: string
                  example: mYsecRet
        responses:
          200:
            description: Successfully logged in
            schema:
              type: object
              properties:
                status:
                  type: string
                access_token:
                  type: string
          401:
            description: Invalid credentials
        """
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        email = _normalize_email(data.get('email'))
        password = _compact_text(data.get('password'), max_len=max_password_chars)
        otp = _normalize_otp(data.get("otp"))

        user_valid = bool(email and password and db_manager.check_user(email, password))
        if user_valid:
            profile = db_manager.get_user_profile(email) or {}
            role = str(profile.get("role") or "user").strip().lower()

            if require_email_verification and not db_manager.is_email_verified(email):
                _track_user_activity("login_rejected_unverified", 403, username=email, details="email_not_verified")
                return (
                    jsonify(
                        {
                            "message": "Email belum diverifikasi",
                            "email_verification_required": True,
                        }
                    ),
                    403,
                )

            mfa_enabled = bool(db_manager.is_user_mfa_enabled(email))
            requires_admin_mfa = bool(admin_mfa_required and role in {"admin", "superuser"} and mfa_enabled)
            if requires_admin_mfa:
                secret = db_manager.get_user_mfa_secret(email)
                if not otp:
                    _track_user_activity("login_mfa_challenge", 200, username=email, details="otp_required")
                    return jsonify(
                        {
                            "status": "mfa_required",
                            "mfa_required": True,
                            "message": "OTP admin diperlukan",
                        }
                    )
                if not _verify_totp(secret, otp):
                    _track_user_activity("login_mfa_failed", 401, username=email, details="otp_invalid")
                    return (
                        jsonify(
                            {
                                "status": "mfa_required",
                                "mfa_required": True,
                                "message": "OTP tidak valid",
                            }
                        ),
                        401,
                    )

            token = create_access_token(identity=email)
            _register_user_session(email, token)
            response = jsonify(
                access_token=token,
                status="success",
                user={
                    "id": profile.get("id"),
                    "email": profile.get("email", email),
                    "name": profile.get("name", ""),
                    "role": role or "user",
                },
            )
            _set_access_cookie(response, token)
            _track_user_activity("login_success", 200, username=email, details="web_portal")
            return response
        _track_user_activity("login_failed", 401, username=(email or "anonymous"), details="invalid_credentials")
        return jsonify({"message": "Email atau password salah"}), 401

    @app.route('/api/signup', methods=['POST'])
    @app.route('/api/register', methods=['POST'])
    @limiter.limit("10 per minute")
    def signup_api():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}

        email = _normalize_email(data.get("email"))
        password = _compact_text(data.get("password"), max_len=max_password_chars)
        name = _compact_text(data.get("name"), max_len=80)

        if not email or not password:
            _track_user_activity("signup_rejected", 400, username=(email or "anonymous"), details="missing_fields")
            return jsonify({"message": "email/password wajib"}), 400

        if not _is_valid_email(email):
            _track_user_activity("signup_rejected", 400, username=email, details="invalid_email")
            return jsonify({"message": "format email tidak valid"}), 400

        if len(password) < 8:
            _track_user_activity("signup_rejected", 400, username=email, details="weak_password")
            return jsonify({"message": "password minimal 8 karakter"}), 400

        created = db_manager.create_user(
            email=email,
            password=password,
            name=name,
            role="user",
        ) or {}
        if not created:
            _track_user_activity("signup_failed", 409, username=email, details="email_exists_or_create_failed")
            return jsonify({"message": "akun sudah ada / gagal membuat akun"}), 409

        if require_email_verification:
            db_manager.set_email_verified(email, False)
            verify_token = db_manager.issue_email_verification_token(email, ttl_minutes=email_verify_ttl_minutes)
            payload = {
                "status": "pending_verification",
                "email_verification_required": True,
                "message": "Akun dibuat. Verifikasi email diperlukan sebelum login.",
                "user": {
                    "id": created.get("id"),
                    "email": created.get("email", email),
                    "name": created.get("name", ""),
                    "role": created.get("role", "user"),
                },
            }
            if expose_dev_auth_tokens and verify_token:
                payload["verification_token"] = verify_token
            _track_user_activity(
                "signup_success_pending_verification",
                201,
                username=(created.get("email") or email),
                details="verify_required",
            )
            return jsonify(payload), 201

        token = create_access_token(identity=created.get("email") or email)
        _register_user_session(created.get("email") or email, token)
        response = jsonify(
            {
                "status": "created",
                "access_token": token,
                "user": {
                    "id": created.get("id"),
                    "email": created.get("email", email),
                    "name": created.get("name", ""),
                    "role": created.get("role", "user"),
                },
            }
        )
        _set_access_cookie(response, token)
        _track_user_activity("signup_success", 201, username=(created.get("email") or email), details="web_portal")
        return response, 201

    @app.route('/api/password/forgot', methods=['POST'])
    @limiter.limit("20 per minute")
    def password_forgot_api():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if PasswordForgotPayload:
            try:
                data = _validate_payload(PasswordForgotPayload, data, exclude_none=True)
            except ValidationError:
                data = {}
        email = _normalize_email(data.get("email"))
        reset_token = ""
        if email and _is_valid_email(email):
            reset_token = db_manager.issue_password_reset_token(email, ttl_minutes=password_reset_ttl_minutes)
        payload = {
            "status": "ok",
            "message": "Jika email terdaftar, token reset sudah diterbitkan.",
        }
        if expose_dev_auth_tokens and reset_token:
            payload["reset_token"] = reset_token
        _track_user_activity(
            "password_reset_requested",
            200,
            username=(email or "anonymous"),
            details="accepted",
        )
        return jsonify(payload)

    @app.route('/api/password/reset', methods=['POST'])
    @limiter.limit("20 per minute")
    def password_reset_api():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if PasswordResetPayload:
            try:
                data = _validate_payload(PasswordResetPayload, data)
            except ValidationError:
                _track_user_activity("password_reset_rejected", 400, details="invalid_payload")
                return jsonify({"message": "payload tidak valid"}), 400
        token_in = _compact_text(data.get("token"), max_len=512)
        new_password = _compact_text(data.get("new_password"), max_len=max_password_chars)
        if not token_in or not new_password or len(new_password) < 8:
            _track_user_activity("password_reset_rejected", 400, details="invalid_token_or_password")
            return jsonify({"message": "token/new_password tidak valid"}), 400
        email = db_manager.reset_password_with_token_and_get_email(token_in, new_password)
        if not email:
            _track_user_activity("password_reset_failed", 400, details="invalid_or_expired_token")
            return jsonify({"message": "token reset invalid / expired"}), 400

        revoked_rows = db_manager.revoke_user_sessions(username=email) or []
        if jwt_revoke_enabled:
            for item in revoked_rows:
                try:
                    jti = str(item.get("jti") or "")
                    exp_ts = int(item.get("exp_ts") or 0)
                    if jti and exp_ts > 0:
                        _revoke_jti(jti, exp_ts)
                except Exception:
                    continue

        _track_user_activity("password_reset_success", 200, username=email, details="token_flow")
        return jsonify({"status": "ok", "message": "Password berhasil direset"})

    @app.route('/api/email/verify/request', methods=['POST'])
    @limiter.limit("20 per minute")
    def email_verify_request_api():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if EmailVerifyRequestPayload:
            try:
                data = _validate_payload(EmailVerifyRequestPayload, data, exclude_none=True)
            except ValidationError:
                data = {}
        email = _normalize_email(data.get("email"))
        if not email:
            email = _resolve_identity_optional()
        verify_token = ""
        if email and _is_valid_email(email):
            verify_token = db_manager.issue_email_verification_token(email, ttl_minutes=email_verify_ttl_minutes)
        payload = {
            "status": "ok",
            "message": "Jika email terdaftar, token verifikasi sudah diterbitkan.",
        }
        if expose_dev_auth_tokens and verify_token:
            payload["verification_token"] = verify_token
        _track_user_activity("email_verify_requested", 200, username=(email or "anonymous"), details="accepted")
        return jsonify(payload)

    @app.route('/api/email/verify/confirm', methods=['POST'])
    @limiter.limit("20 per minute")
    def email_verify_confirm_api():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if EmailVerifyConfirmPayload:
            try:
                data = _validate_payload(EmailVerifyConfirmPayload, data)
            except ValidationError:
                _track_user_activity("email_verify_rejected", 400, details="invalid_payload")
                return jsonify({"message": "payload tidak valid"}), 400
        token_in = _compact_text(data.get("token"), max_len=512)
        if not token_in:
            _track_user_activity("email_verify_rejected", 400, details="missing_token")
            return jsonify({"message": "token wajib"}), 400
        email = db_manager.verify_email_with_token(token_in)
        if not email:
            _track_user_activity("email_verify_failed", 400, details="invalid_or_expired_token")
            return jsonify({"message": "token verifikasi invalid / expired"}), 400
        _track_user_activity("email_verify_success", 200, username=email, details="token_flow")
        return jsonify({"status": "ok", "message": "Email berhasil diverifikasi", "email": email})

    @app.route('/api/refresh', methods=['POST'])
    @jwt_required()
    def refresh_token():
        user = get_jwt_identity()
        if not user:
            return jsonify({"message": "Unauthorized"}), 401
        user_email = str(user).strip().lower()
        if require_email_verification and not db_manager.is_email_verified(user_email):
            response = jsonify({"message": "Email belum diverifikasi", "email_verification_required": True})
            response.delete_cookie(str(app.config.get("JWT_ACCESS_COOKIE_NAME") or "elara_access_token"))
            return response, 403
        new_token = create_access_token(identity=user)
        _register_user_session(user, new_token)
        response = jsonify(access_token=new_token)
        _set_access_cookie(response, new_token)
        return response

    @app.route('/api/me', methods=['GET'])
    @jwt_required()
    def me_api():
        email = str(get_jwt_identity() or "").strip()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        profile = db_manager.get_user_profile(email) or {}
        if not profile:
            return jsonify({"message": "User not found"}), 404
        security = db_manager.get_user_security_profile(email) or {}
        return jsonify(
            {
                "id": profile.get("id"),
                "email": profile.get("email", email),
                "name": profile.get("name", ""),
                "role": profile.get("role", "user"),
                "email_verified": bool(security.get("email_verified", True)),
                "mfa_enabled": bool(security.get("mfa_enabled", False)),
                "mfa_secret_set": bool(security.get("mfa_secret_set", False)),
            }
        )

    @app.route('/api/account/profile', methods=['GET'])
    @limiter.limit("60 per minute")
    @jwt_required()
    def account_profile_get_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        profile = db_manager.get_user_profile(email) or {}
        if not profile:
            return jsonify({"message": "User not found"}), 404
        security = db_manager.get_user_security_profile(email) or {}
        return jsonify(
            {
                "id": profile.get("id"),
                "email": profile.get("email", email),
                "name": profile.get("name", ""),
                "role": profile.get("role", "user"),
                "security": {
                    "email_verified": bool(security.get("email_verified", True)),
                    "mfa_enabled": bool(security.get("mfa_enabled", False)),
                    "mfa_secret_set": bool(security.get("mfa_secret_set", False)),
                },
            }
        )

    @app.route('/api/account/profile', methods=['PATCH'])
    @limiter.limit("30 per minute")
    @jwt_required()
    def account_profile_patch_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if AccountProfilePatchPayload:
            try:
                data = _validate_payload(AccountProfilePatchPayload, data)
            except ValidationError:
                return jsonify({"message": "payload tidak valid"}), 400
        name = _compact_text(data.get("name"), max_len=80)
        if not name:
            return jsonify({"message": "name wajib"}), 400
        if not db_manager.update_user_name(email, name):
            return jsonify({"message": "gagal update profile"}), 400
        _track_user_activity("account_profile_updated", 200, username=email)
        return jsonify({"status": "updated", "name": name})

    @app.route('/api/account/password', methods=['POST'])
    @limiter.limit("20 per minute")
    @jwt_required()
    def account_password_patch_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if AccountPasswordPatchPayload:
            try:
                data = _validate_payload(AccountPasswordPatchPayload, data)
            except ValidationError:
                return jsonify({"message": "payload tidak valid"}), 400
        current_password = _compact_text(data.get("current_password"), max_len=max_password_chars)
        new_password = _compact_text(data.get("new_password"), max_len=max_password_chars)
        if not current_password or not new_password or len(new_password) < 8:
            return jsonify({"message": "password tidak valid"}), 400
        if not db_manager.check_user(email, current_password):
            _track_user_activity("account_password_change_rejected", 401, username=email, details="invalid_current")
            return jsonify({"message": "password saat ini salah"}), 401
        if current_password == new_password:
            return jsonify({"message": "password baru harus berbeda"}), 400
        if not db_manager.set_user_password(email, new_password):
            return jsonify({"message": "gagal update password"}), 400
        _track_user_activity("account_password_changed", 200, username=email)
        return jsonify({"status": "updated", "message": "Password diperbarui"})

    @app.route('/api/account/mfa/status', methods=['GET'])
    @limiter.limit("60 per minute")
    @jwt_required()
    def account_mfa_status_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(db_manager.get_user_role(email) or "").strip().lower()
        security = db_manager.get_user_security_profile(email) or {}
        return jsonify(
            {
                "email": email,
                "role": role or "user",
                "admin_eligible": role in {"admin", "superuser"},
                "mfa_enabled": bool(security.get("mfa_enabled", False)),
                "mfa_secret_set": bool(security.get("mfa_secret_set", False)),
                "period_sec": mfa_totp_period_sec,
            }
        )

    @app.route('/api/account/mfa/setup', methods=['POST'])
    @limiter.limit("12 per minute")
    @jwt_required()
    def account_mfa_setup_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(db_manager.get_user_role(email) or "").strip().lower()
        if role not in {"admin", "superuser"}:
            return jsonify({"message": "Forbidden"}), 403
        secret = _generate_mfa_secret()
        if not db_manager.set_user_mfa_secret(email, secret, enabled=False):
            return jsonify({"message": "gagal setup mfa"}), 500
        _track_user_activity("account_mfa_setup", 200, username=email)
        return jsonify(
            {
                "status": "ok",
                "secret": secret,
                "otpauth_uri": _build_otpauth_uri(email, secret),
                "period_sec": mfa_totp_period_sec,
            }
        )

    @app.route('/api/account/mfa/enable', methods=['POST'])
    @limiter.limit("20 per minute")
    @jwt_required()
    def account_mfa_enable_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(db_manager.get_user_role(email) or "").strip().lower()
        if role not in {"admin", "superuser"}:
            return jsonify({"message": "Forbidden"}), 403
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if MfaOtpPayload:
            try:
                data = _validate_payload(MfaOtpPayload, data)
            except ValidationError:
                return jsonify({"message": "payload tidak valid"}), 400
        otp = _normalize_otp(data.get("otp"))
        secret = db_manager.get_user_mfa_secret(email)
        if not secret:
            return jsonify({"message": "setup mfa dulu"}), 400
        if not _verify_totp(secret, otp):
            _track_user_activity("account_mfa_enable_failed", 400, username=email, details="otp_invalid")
            return jsonify({"message": "OTP tidak valid"}), 400
        if not db_manager.enable_user_mfa(email):
            return jsonify({"message": "gagal enable mfa"}), 500
        _track_user_activity("account_mfa_enabled", 200, username=email)
        return jsonify({"status": "ok", "mfa_enabled": True})

    @app.route('/api/account/mfa/disable', methods=['POST'])
    @limiter.limit("20 per minute")
    @jwt_required()
    def account_mfa_disable_api():
        email = str(get_jwt_identity() or "").strip().lower()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(db_manager.get_user_role(email) or "").strip().lower()
        if role not in {"admin", "superuser"}:
            return jsonify({"message": "Forbidden"}), 403
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if MfaOtpPayload:
            try:
                data = _validate_payload(MfaOtpPayload, data)
            except ValidationError:
                return jsonify({"message": "payload tidak valid"}), 400
        otp = _normalize_otp(data.get("otp"))
        secret = db_manager.get_user_mfa_secret(email)
        if db_manager.is_user_mfa_enabled(email):
            if not secret or not _verify_totp(secret, otp):
                _track_user_activity("account_mfa_disable_failed", 400, username=email, details="otp_invalid")
                return jsonify({"message": "OTP tidak valid"}), 400
        if not db_manager.disable_user_mfa(email):
            return jsonify({"message": "gagal disable mfa"}), 500
        _track_user_activity("account_mfa_disabled", 200, username=email)
        return jsonify({"status": "ok", "mfa_enabled": False})

    @app.route('/api/logout', methods=['POST'])
    @jwt_required()
    def logout_api():
        email = str(get_jwt_identity() or "").strip()
        claims = get_jwt() or {}
        jti = str(claims.get("jti") or "")
        exp_ts = int(claims.get("exp") or 0)
        if not jwt_revoke_enabled:
            if jti:
                db_manager.revoke_user_session_jti(jti=jti)
            _track_user_activity("logout", 200, username=(email or "anonymous"))
            return jsonify({"status": "ok", "message": "Logout acknowledged"})
        if not jti or exp_ts <= 0:
            _track_user_activity(
                "logout_rejected",
                400,
                username=(email or "anonymous"),
                details="invalid_token_payload",
            )
            return jsonify({"message": "Invalid token payload"}), 400
        _revoke_jti(jti, exp_ts)
        db_manager.revoke_user_session_jti(jti=jti)
        response = jsonify({"status": "ok", "message": "Logged out"})
        response.delete_cookie(str(app.config.get("JWT_ACCESS_COOKIE_NAME") or "elara_access_token"))
        _track_user_activity("logout", 200, username=(email or "anonymous"))
        return response

    @app.route('/api/control', methods=['POST'])
    @limiter.limit("120 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def control():
        """
        Execute an NLP or system command via the Elara Skill Manager.
        ---
        tags:
          - Core NLP
        security:
          - Bearer: []
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              required:
                - command
              properties:
                command:
                  type: string
                  example: "nyalakan lampu ruang tamu"
        responses:
          200:
            description: Command executed successfully
          400:
            description: Empty command
          401:
            description: Unauthorized
        """
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        data = request.get_json(silent=True) or {}
        cmd = _compact_text(data.get('command'), max_len=max_command_chars)
        
        if not cmd:
            _track_user_activity("quick_command_rejected", 400, details="empty_or_too_long")
            return jsonify({"message": "Perintah kosong / melebihi batas panjang"}), 400

        request_id = uuid4().hex
        command_user_id = _resolve_command_user_id("web_user")
        prefer_background = _prefer_background_execution(data)
        log.info("Web Command: %s", _redact_command_for_log(cmd))
        res, queued, err = _submit_command(
            elara_skill.handle_command(cmd, command_user_id),
            request_id=request_id,
            source="web_control",
            background_only=prefer_background,
        )
        if err is not None:
            log.error(f"Web command failed (request_id={request_id}): {err}")
            _track_user_activity(
                "quick_command_failed",
                500,
                details=_redact_command_for_log(cmd),
            )
            return jsonify({"message": "Command execution error", "request_id": request_id}), 500
        if queued:
            _track_user_activity(
                "quick_command_queued",
                202,
                details=_redact_command_for_log(cmd),
            )
            return (
                jsonify(
                    {
                        "status": "queued",
                        "request_id": request_id,
                        "message": "Command accepted and running in background.",
                    }
                ),
                202,
            )
        _track_user_activity(
            "quick_command_success",
            200,
            details=_redact_command_for_log(cmd),
        )
        return jsonify({"status": "success", "message": res, "request_id": request_id})
    @app.route('/api/screener', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def screener():
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        # Try to reach stock screener skill directly for structured data
        try:
            stock_skill = _get_stock_screener_skill()
            if stock_skill and hasattr(stock_skill, "get_screen_snapshot"):
                wait_sec = float(getattr(Config, "STOCK_SCREEN_API_WAIT_SEC", 1.5))
                snapshot = stock_skill.get_screen_snapshot(force_refresh=True, wait_timeout_sec=wait_sec)
                payload = {
                    "results": snapshot.get("results", []),
                    "running": bool(snapshot.get("running", False)),
                    "updated_at": snapshot.get("updated_at", 0),
                }
                if snapshot.get("error"):
                    payload["error"] = snapshot["error"]
                # 202 indicates background job is still processing.
                if payload["running"] and not payload["results"]:
                    return jsonify(payload), 202
                return jsonify(payload)
            if stock_skill and hasattr(stock_skill, "_screen_results"):
                results = stock_skill._screen_results()
                return jsonify({"results": results, "running": False})
        except Exception as e:
            log.error(f"Screener skill error: {e}")
        # Fallback: use NLP command and return text
        try:
            request_id = uuid4().hex
            command_user_id = _resolve_command_user_id("web_user")
            res, queued, err = _submit_command(
                elara_skill.handle_command("screener saham", command_user_id),
                request_id=request_id,
                source="web_screener_fallback",
                background_only=False,
            )
            if err is not None:
                raise err
            if queued:
                return (
                    jsonify(
                        {
                            "status": "queued",
                            "request_id": request_id,
                            "message": "Screener command running in background.",
                        }
                    ),
                    202,
                )
            return jsonify({"text": res})
        except Exception as e:
            log.error(f"Screener fallback error: {e}")
            return jsonify({"message": "Screener unavailable"}), 503

    @app.route('/api/screener/visualization', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def screener_visualization():
        if not AUTH_DISABLED:
            verify_jwt_in_request()

        force_refresh = (request.args.get("refresh", "1").strip() == "1")
        wait_sec = float(request.args.get("wait", getattr(Config, "STOCK_SCREEN_API_WAIT_SEC", 1.5)))
        try:
            limit = int(request.args.get("limit", getattr(Config, "STOCK_SCREEN_LIMIT", 10)))
        except Exception as e:
            log.warning(f"Invalid screener visual limit query param: {e}")
            limit = int(getattr(Config, "STOCK_SCREEN_LIMIT", 10))
        limit = max(1, min(limit, 200))

        try:
            stock_skill = _get_stock_screener_skill()
            if not stock_skill or not hasattr(stock_skill, "get_screen_snapshot"):
                return jsonify({"message": "Screener skill unavailable"}), 503

            snapshot = stock_skill.get_screen_snapshot(force_refresh=force_refresh, wait_timeout_sec=wait_sec)
            updated_at = snapshot.get("updated_at", 0)
            results = list(snapshot.get("results") or [])[:limit]
            visual = None
            if not force_refresh:
                visual = _get_screener_visual_cache("stock", updated_at, limit)
            if visual is None:
                visual = _build_screener_visual_payload(results)
                _set_screener_visual_cache("stock", updated_at, limit, visual)

            payload = {
                "running": bool(snapshot.get("running", False)),
                "updated_at": updated_at,
                "results": results,
                **visual,
            }
            if snapshot.get("error"):
                payload["error"] = snapshot["error"]

            if payload["running"] and not payload["results"]:
                return jsonify(payload), 202
            return jsonify(payload)
        except Exception as e:
            log.error(f"Screener visualization error: {e}")
            return jsonify({"message": "Screener visualization unavailable"}), 503

    @app.route('/api/crypto', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def crypto_screener():
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        try:
            crypto_skill = _get_crypto_screener_skill()
            if crypto_skill and hasattr(crypto_skill, "get_screen_snapshot"):
                wait_sec = float(getattr(Config, "CRYPTO_SCREEN_API_WAIT_SEC", 1.2))
                snapshot = crypto_skill.get_screen_snapshot(force_refresh=True, wait_timeout_sec=wait_sec)
                payload = {
                    "results": snapshot.get("results", []),
                    "running": bool(snapshot.get("running", False)),
                    "updated_at": snapshot.get("updated_at", 0),
                }
                if snapshot.get("error"):
                    payload["error"] = snapshot["error"]
                if payload["running"] and not payload["results"]:
                    return jsonify(payload), 202
                return jsonify(payload)
        except Exception as e:
            log.error(f"Crypto screener skill error: {e}")

        try:
            request_id = uuid4().hex
            command_user_id = _resolve_command_user_id("web_user")
            res, queued, err = _submit_command(
                elara_skill.handle_command("screener crypto", command_user_id),
                request_id=request_id,
                source="web_crypto_fallback",
                background_only=False,
            )
            if err is not None:
                raise err
            if queued:
                return (
                    jsonify(
                        {
                            "status": "queued",
                            "request_id": request_id,
                            "message": "Crypto screener running in background.",
                        }
                    ),
                    202,
                )
            return jsonify({"text": res})
        except Exception as e:
            log.error(f"Crypto screener fallback error: {e}")
            return jsonify({"message": "Crypto screener unavailable"}), 503

    @app.route('/api/crypto/visualization', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def crypto_screener_visualization():
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        force_refresh = (request.args.get("refresh", "1").strip() == "1")
        wait_sec = float(request.args.get("wait", getattr(Config, "CRYPTO_SCREEN_API_WAIT_SEC", 1.2)))
        try:
            limit = int(request.args.get("limit", getattr(Config, "CRYPTO_SCREEN_LIMIT", 10)))
        except Exception as e:
            log.warning(f"Invalid crypto visual limit query param: {e}")
            limit = int(getattr(Config, "CRYPTO_SCREEN_LIMIT", 10))
        limit = max(1, min(limit, 200))

        try:
            crypto_skill = _get_crypto_screener_skill()
            if not crypto_skill or not hasattr(crypto_skill, "get_screen_snapshot"):
                return jsonify({"message": "Crypto screener skill unavailable"}), 503

            snapshot = crypto_skill.get_screen_snapshot(force_refresh=force_refresh, wait_timeout_sec=wait_sec)
            updated_at = snapshot.get("updated_at", 0)
            results = list(snapshot.get("results") or [])[:limit]
            visual = None
            if not force_refresh:
                visual = _get_screener_visual_cache("crypto", updated_at, limit)
            if visual is None:
                visual = _build_screener_visual_payload(results)
                _set_screener_visual_cache("crypto", updated_at, limit, visual)
            payload = {
                "running": bool(snapshot.get("running", False)),
                "updated_at": updated_at,
                "results": results,
                **visual,
            }
            if snapshot.get("error"):
                payload["error"] = snapshot["error"]

            if payload["running"] and not payload["results"]:
                return jsonify(payload), 202
            return jsonify(payload)
        except Exception as e:
            log.error(f"Crypto screener visualization error: {e}")
            return jsonify({"message": "Crypto screener visualization unavailable"}), 503

    @app.route('/api/mission-control', methods=['GET'])
    @limiter.limit("30 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def mission_control():
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        payload = {
            "ts": time.time(),
            "health": health_snapshot(),
            "stock": {"running": False, "updated_at": 0, "top": []},
            "crypto": {"running": False, "updated_at": 0, "top": []},
        }
        try:
            stock_skill = _get_stock_screener_skill()
            if stock_skill and hasattr(stock_skill, "get_screen_snapshot"):
                stock_snap = stock_skill.get_screen_snapshot(force_refresh=False, wait_timeout_sec=0.3)
                payload["stock"] = {
                    "running": bool(stock_snap.get("running", False)),
                    "updated_at": stock_snap.get("updated_at", 0),
                    "top": list(stock_snap.get("results") or [])[:5],
                    "error": stock_snap.get("error", ""),
                }
        except Exception as e:
            payload["stock"]["error"] = str(e)

        try:
            crypto_skill = _get_crypto_screener_skill()
            if crypto_skill and hasattr(crypto_skill, "get_screen_snapshot"):
                crypto_snap = crypto_skill.get_screen_snapshot(force_refresh=False, wait_timeout_sec=0.3)
                payload["crypto"] = {
                    "running": bool(crypto_snap.get("running", False)),
                    "updated_at": crypto_snap.get("updated_at", 0),
                    "top": list(crypto_snap.get("results") or [])[:5],
                    "error": crypto_snap.get("error", ""),
                }
        except Exception as e:
            payload["crypto"]["error"] = str(e)
        return jsonify(payload)

    @app.route('/api/market/realtime', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def market_realtime():
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        try:
            limit = int(request.args.get("limit", 90))
        except Exception:
            limit = 90
        stock_symbol = _normalize_market_symbol(request.args.get("stock_symbol"))
        crypto_symbol = _normalize_market_symbol(request.args.get("crypto_symbol"))
        stock_timeframe = _compact_text(request.args.get("stock_timeframe"), max_len=12).lower()
        crypto_timeframe = _compact_text(request.args.get("crypto_timeframe"), max_len=12).lower()
        force = str(request.args.get("refresh", "0")).strip() == "1"
        _sync_market_chart_cache(force=force)
        payload = _build_market_chart_payload(
            limit=limit,
            stock_symbol=stock_symbol,
            crypto_symbol=crypto_symbol,
            stock_timeframe=stock_timeframe,
            crypto_timeframe=crypto_timeframe,
        )
        try:
            _evaluate_market_alert_rules(force=False)
        except Exception as e:
            log.debug(f"market alert evaluate skipped: {e}")
        payload["selection"] = {
            "stock_symbol": stock_symbol,
            "crypto_symbol": crypto_symbol,
            "stock_timeframe": stock_timeframe or market_default_timeframe,
            "crypto_timeframe": crypto_timeframe or market_default_timeframe,
        }
        payload["ts"] = time.time()
        return jsonify(payload)

    @app.route('/api/alerts/rules', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alert_rules_list():
        actor = str(get_jwt_identity() or "").strip().lower()
        if not actor:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(getattr(g, "current_user_role", "") or db_manager.get_user_role(actor) or "").strip().lower()
        is_admin = role in {"admin", "superuser"}
        all_view = str(request.args.get("all", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
        active_only = str(request.args.get("active_only", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
        owner_filter = _compact_text(request.args.get("owner_username"), max_len=120).lower()
        try:
            limit = int(request.args.get("limit", 200))
        except Exception:
            limit = 200
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0

        if not is_admin or not all_view:
            owner_filter = actor

        result = db_manager.query_market_alert_rules(
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            owner_username=owner_filter,
            include_global=True,
            admin_view=bool(is_admin and all_view),
            active_only=active_only,
        ) or {}
        return jsonify(
            {
                "items": list(result.get("items") or []),
                "total": int(result.get("total") or 0),
                "limit": int(result.get("limit") or max(1, min(limit, 500))),
                "offset": int(result.get("offset") or max(0, offset)),
                "has_more": bool(result.get("has_more", False)),
                "viewer": actor,
                "is_admin": bool(is_admin),
            }
        )

    @app.route('/api/alerts/rules', methods=['POST'])
    @limiter.limit("30 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alert_rules_create():
        actor = str(get_jwt_identity() or "").strip().lower()
        if not actor:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(getattr(g, "current_user_role", "") or db_manager.get_user_role(actor) or "").strip().lower()
        is_admin = role in {"admin", "superuser"}
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"message": "Invalid payload"}), 400

        owner = actor
        if is_admin:
            owner_override = _compact_text(data.get("owner_username"), max_len=120).lower()
            if owner_override:
                owner = owner_override
        scope = _normalize_alert_scope(data.get("scope"))
        if not is_admin:
            scope = "user"

        owner_count = db_manager.query_market_alert_rules(
            limit=1,
            offset=0,
            owner_username=owner,
            include_global=False,
            admin_view=True,
            active_only=False,
        ) or {}
        if int(owner_count.get("total") or 0) >= market_alert_max_rules_per_user:
            return jsonify({"message": "Alert rule limit reached for user"}), 409

        cooldown_val = market_alert_default_cooldown_sec
        try:
            cooldown_raw = _as_float(data.get("cooldown_sec"))
            if cooldown_raw is not None:
                cooldown_val = int(cooldown_raw)
        except Exception:
            cooldown_val = market_alert_default_cooldown_sec

        market_kind = _normalize_market_kind(data.get("market_kind"))
        created = db_manager.create_market_alert_rule(
            owner_username=owner,
            name=_compact_text(data.get("name"), max_len=80) or "Price Alert",
            market_kind=market_kind,
            symbol=_normalize_alert_symbol(data.get("symbol"), market_kind=market_kind),
            condition=_normalize_alert_condition(data.get("condition")),
            target_price=data.get("target_price"),
            cooldown_sec=max(30, cooldown_val),
            notify_channel=_normalize_alert_channel(data.get("notify_channel")),
            scope=scope,
            is_active=bool(data.get("is_active", True)),
        )
        if not created:
            _track_user_activity("market_alert_create_failed", 400, username=actor)
            return jsonify({"message": "Invalid alert rule payload"}), 400
        _track_user_activity(
            "market_alert_created",
            201,
            username=actor,
            details=f"rule_id={created.get('id')} symbol={created.get('symbol')}",
        )
        return jsonify({"status": "ok", "rule": created}), 201

    @app.route('/api/alerts/rules/<int:rule_id>', methods=['PATCH'])
    @limiter.limit("40 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alert_rules_update(rule_id: int):
        actor = str(get_jwt_identity() or "").strip().lower()
        if not actor:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(getattr(g, "current_user_role", "") or db_manager.get_user_role(actor) or "").strip().lower()
        is_admin = role in {"admin", "superuser"}
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"message": "Invalid payload"}), 400

        sanitized_updates = {}
        if "name" in data:
            sanitized_updates["name"] = _compact_text(data.get("name"), max_len=80)
        if "symbol" in data:
            kind_for_symbol = _normalize_market_kind(data.get("market_kind"))
            sanitized_updates["symbol"] = _normalize_alert_symbol(
                data.get("symbol"),
                market_kind=kind_for_symbol,
            )
        if "market_kind" in data:
            sanitized_updates["market_kind"] = _normalize_market_kind(data.get("market_kind"))
        if "condition" in data:
            sanitized_updates["condition"] = _normalize_alert_condition(data.get("condition"))
        if "target_price" in data:
            sanitized_updates["target_price"] = _as_float(data.get("target_price"))
        if "cooldown_sec" in data:
            try:
                sanitized_updates["cooldown_sec"] = int(data.get("cooldown_sec"))
            except Exception:
                pass
        if "notify_channel" in data:
            sanitized_updates["notify_channel"] = _normalize_alert_channel(data.get("notify_channel"))
        if "is_active" in data:
            sanitized_updates["is_active"] = bool(data.get("is_active"))
        if is_admin and "scope" in data:
            sanitized_updates["scope"] = _normalize_alert_scope(data.get("scope"))
        if is_admin and "owner_username" in data:
            sanitized_updates["owner_username"] = _compact_text(data.get("owner_username"), max_len=120).lower()

        updated = db_manager.update_market_alert_rule(
            rule_id,
            actor_username=actor,
            actor_is_admin=is_admin,
            updates=sanitized_updates,
        )
        if not updated:
            _track_user_activity("market_alert_update_failed", 404, username=actor, details=f"rule_id={rule_id}")
            return jsonify({"message": "Rule not found or forbidden"}), 404
        _track_user_activity("market_alert_updated", 200, username=actor, details=f"rule_id={rule_id}")
        return jsonify({"status": "ok", "rule": updated})

    @app.route('/api/alerts/rules/<int:rule_id>', methods=['DELETE'])
    @limiter.limit("30 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alert_rules_delete(rule_id: int):
        actor = str(get_jwt_identity() or "").strip().lower()
        if not actor:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(getattr(g, "current_user_role", "") or db_manager.get_user_role(actor) or "").strip().lower()
        is_admin = role in {"admin", "superuser"}
        deleted = db_manager.delete_market_alert_rule(rule_id, actor_username=actor, actor_is_admin=is_admin)
        if not deleted:
            _track_user_activity("market_alert_delete_failed", 404, username=actor, details=f"rule_id={rule_id}")
            return jsonify({"message": "Rule not found or forbidden"}), 404
        _track_user_activity("market_alert_deleted", 200, username=actor, details=f"rule_id={rule_id}")
        return jsonify({"status": "ok", "rule_id": int(rule_id)})

    @app.route('/api/alerts/events', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alert_events_list():
        actor = str(get_jwt_identity() or "").strip().lower()
        if not actor:
            return jsonify({"message": "Unauthorized"}), 401
        role = str(getattr(g, "current_user_role", "") or db_manager.get_user_role(actor) or "").strip().lower()
        is_admin = role in {"admin", "superuser"}
        all_view = str(request.args.get("all", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
        owner_filter = _compact_text(request.args.get("owner_username"), max_len=120).lower()
        try:
            limit = int(request.args.get("limit", 100))
        except Exception:
            limit = 100
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0

        if not is_admin or not all_view:
            owner_filter = actor

        result = db_manager.query_market_alert_events(
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            owner_username=owner_filter,
            include_global=True,
            admin_view=bool(is_admin and all_view),
        ) or {}
        return jsonify(
            {
                "items": list(result.get("items") or []),
                "total": int(result.get("total") or 0),
                "limit": int(result.get("limit") or max(1, min(limit, 500))),
                "offset": int(result.get("offset") or max(0, offset)),
                "has_more": bool(result.get("has_more", False)),
                "viewer": actor,
                "is_admin": bool(is_admin),
            }
        )

    @app.route('/api/alerts/evaluate', methods=['POST'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def market_alerts_evaluate_now():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        data = request.get_json(silent=True) or {}
        force_refresh = bool(_coerce_bool((data or {}).get("refresh")))
        if force_refresh:
            _sync_market_chart_cache(force=True)
        result = _evaluate_market_alert_rules(force=True)
        _track_user_activity(
            "market_alert_manual_evaluate",
            200,
            details=f"checked={result.get('checked')} triggered={result.get('triggered')}",
        )
        return jsonify({"status": "ok", "result": result})

    @app.route('/api/dashboard/user', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def user_dashboard_data():
        email = str(get_jwt_identity() or "").strip()
        if not email:
            return jsonify({"message": "Unauthorized"}), 401
        profile = db_manager.get_user_profile(email) or {}
        metrics = {}
        health = {}
        try:
            metrics = db_manager.get_trading_metrics() or {}
        except Exception as e:
            log.warning(f"User dashboard metrics failed: {e}")
        try:
            health = health_snapshot() or {}
        except Exception as e:
            log.warning(f"User dashboard health snapshot failed: {e}")
        _sync_market_chart_cache(force=False)
        market_realtime_payload = _build_market_chart_payload(limit=60)
        skill_analytics = _markdown_skill_analytics_payload(include_recent=False)
        return jsonify(
            {
                "profile": {
                    "id": profile.get("id"),
                    "email": profile.get("email", email),
                    "name": profile.get("name", ""),
                    "role": profile.get("role", "user"),
                },
                "trading_metrics": metrics,
                "health": health,
                "skill_analytics": skill_analytics,
                "market_realtime": market_realtime_payload,
                "ts": time.time(),
            }
        )

    @app.route('/api/dashboard/admin', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_dashboard_data():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        try:
            activity_limit = int(request.args.get("activity_limit", 50))
        except Exception:
            activity_limit = 50
        activity_limit = max(5, min(activity_limit, 300))
        users = db_manager.list_users(limit=500) or []
        trading_metrics = {}
        status = {}
        user_activity = []
        user_activity_total = 0
        try:
            trading_metrics = db_manager.get_trading_metrics() or {}
        except Exception as e:
            log.warning(f"Admin dashboard metrics failed: {e}")
        try:
            status = health_snapshot() or {}
        except Exception as e:
            log.warning(f"Admin dashboard health snapshot failed: {e}")
        try:
            activity_result = db_manager.query_user_activities(limit=activity_limit, offset=0) or {}
            user_activity = list(activity_result.get("items") or [])
            user_activity_total = int(activity_result.get("total") or len(user_activity))
        except Exception as e:
            log.warning(f"Admin dashboard activity feed failed: {e}")
        _sync_market_chart_cache(force=False)
        market_realtime_payload = _build_market_chart_payload(limit=60)
        skill_analytics = _markdown_skill_analytics_payload(include_recent=True)

        role_counts = {"superuser": 0, "admin": 0, "user": 0}
        for row in users:
            role = str((row or {}).get("role") or "user").strip().lower()
            if role not in role_counts:
                role_counts[role] = 0
            role_counts[role] += 1

        return jsonify(
            {
                "users_total": len(users),
                "users_by_role": role_counts,
                "trading_metrics": trading_metrics,
                "health": status,
                "skill_analytics": skill_analytics,
                "user_activity": user_activity,
                "user_activity_total": user_activity_total,
                "market_realtime": market_realtime_payload,
                "ts": time.time(),
            }
        )

    def _parse_admin_activity_query(default_limit: int = 25, max_limit: int = 200):
        try:
            limit = int(request.args.get("limit", default_limit))
        except Exception:
            limit = default_limit
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0
        status_code = None
        status_code_raw = _compact_text(request.args.get("status_code"), max_len=8)
        if status_code_raw:
            try:
                status_code = int(status_code_raw)
            except Exception:
                status_code = None
        status_group = _compact_text(request.args.get("status_group"), max_len=4).lower()
        if status_group not in {"2xx", "3xx", "4xx", "5xx"}:
            status_group = ""
        return {
            "limit": max(1, min(limit, max_limit)),
            "offset": max(0, offset),
            "username": _compact_text(request.args.get("username"), max_len=120),
            "action": _compact_text(request.args.get("action"), max_len=120),
            "status_code": status_code,
            "status_group": status_group,
            "date_from": _compact_text(request.args.get("date_from"), max_len=40),
            "date_to": _compact_text(request.args.get("date_to"), max_len=40),
        }

    @app.route('/api/admin/activity', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_activity():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        params = _parse_admin_activity_query(default_limit=25, max_limit=200)
        result = db_manager.query_user_activities(**params) or {}
        return jsonify(
            {
                "items": list(result.get("items") or []),
                "total": int(result.get("total") or 0),
                "limit": int(result.get("limit") or params["limit"]),
                "offset": int(result.get("offset") or params["offset"]),
                "has_more": bool(result.get("has_more", False)),
            }
        )

    @app.route('/api/admin/activity/stats', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_activity_stats():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        params = _parse_admin_activity_query(default_limit=25, max_limit=200)
        try:
            top_n = int(request.args.get("top_n", 5))
        except Exception:
            top_n = 5
        top_n = max(1, min(top_n, 12))
        summary = db_manager.summarize_user_activities(
            username=params.get("username", ""),
            action=params.get("action", ""),
            status_code=params.get("status_code"),
            status_group=params.get("status_group", ""),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
            top_n=top_n,
        ) or {}
        return jsonify(summary)

    @app.route('/api/admin/activity/chain/verify', methods=['GET'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_activity_chain_verify():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        try:
            limit = int(request.args.get("limit", 5000))
        except Exception:
            limit = 5000
        limit = max(100, min(limit, 20000))
        result = db_manager.verify_activity_audit_chain(limit=limit) or {}
        status_code = 200 if bool(result.get("ok", False)) else 409
        return jsonify(result), status_code

    @app.route('/api/admin/sessions', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_sessions():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        try:
            limit = int(request.args.get("limit", 100))
        except Exception:
            limit = 100
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0
        username = _compact_text(request.args.get("username"), max_len=120)
        active_only_raw = str(request.args.get("active_only", "1") or "1").strip().lower()
        active_only = active_only_raw not in {"0", "false", "no", "off"}
        result = db_manager.query_user_sessions(
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            username=username,
            active_only=active_only,
        ) or {}
        return jsonify(
            {
                "items": list(result.get("items") or []),
                "total": int(result.get("total") or 0),
                "limit": int(result.get("limit") or max(1, min(limit, 500))),
                "offset": int(result.get("offset") or max(0, offset)),
                "has_more": bool(result.get("has_more", False)),
            }
        )

    @app.route('/api/admin/sessions/force-logout', methods=['POST'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_force_logout_user():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        data = request.get_json(silent=True) or {}
        username = _compact_text(data.get("username"), max_len=120)
        if not username:
            _track_user_activity("admin_force_logout_rejected", 400, details="missing_username")
            return jsonify({"message": "username required"}), 400
        revoked_rows = db_manager.revoke_user_sessions(username=username) or []
        revoked_count = 0
        for row in revoked_rows:
            jti = str((row or {}).get("jti") or "").strip()
            if not jti:
                continue
            try:
                exp_ts = int((row or {}).get("exp_ts") or 0)
            except Exception:
                exp_ts = 0
            if exp_ts <= 0:
                exp_ts = int(time.time() + jwt_block_fallback_ttl)
            _revoke_jti(jti, exp_ts)
            revoked_count += 1
        _track_user_activity(
            "admin_force_logout",
            200,
            details=f"user={username} sessions={revoked_count}",
        )
        return jsonify({"status": "ok", "username": username, "revoked_sessions": revoked_count})

    @app.route('/api/admin/activity/export.csv', methods=['GET'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_activity_export_csv():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        params = _parse_admin_activity_query(default_limit=1000, max_limit=5000)
        result = db_manager.query_user_activities(**params) or {}
        rows = list(result.get("items") or [])

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["timestamp", "username", "action", "endpoint", "status_code", "details", "method", "path", "id"])
        for row in rows:
            method = str(row.get("method") or "")
            path = str(row.get("path") or "")
            endpoint = f"{method} {path}".strip()
            writer.writerow(
                [
                    str(row.get("timestamp") or ""),
                    str(row.get("username") or ""),
                    str(row.get("action") or ""),
                    endpoint,
                    str(row.get("status_code") if row.get("status_code") is not None else ""),
                    str(row.get("details") or ""),
                    method,
                    path,
                    str(row.get("id") or ""),
                ]
            )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        response = app.response_class(buffer.getvalue(), mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename=\"elara_user_activity_{stamp}.csv\"'
        return response

    @app.route('/health/live', methods=['GET'])
    @limiter.limit("120 per minute")
    def health_live():
        payload = {
            "status": "ok",
            "kind": "live",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return jsonify(payload), 200

    @app.route('/healthz', methods=['GET'])
    @app.route('/health/ready', methods=['GET'])
    @limiter.limit("120 per minute")
    def health_ready():
        payload, status_code = _health_cached("ready", _build_ready_health_payload)
        return jsonify(payload), status_code

    @app.route('/api/health', methods=['GET'])
    @app.route('/health/deep', methods=['GET'])
    @limiter.limit("120 per minute")
    def health_deep():
        payload, status_code = _health_cached("deep", _build_deep_health_payload)
        return jsonify(payload), status_code

    def _metrics_authorized() -> bool:
        if prometheus_metrics_public or AUTH_DISABLED:
            return True
        if prometheus_metrics_token:
            auth = str(request.headers.get("Authorization") or "").strip()
            supplied = ""
            if auth.lower().startswith("bearer "):
                supplied = auth.split(" ", 1)[1].strip()
            supplied = supplied or str(request.headers.get("X-Prometheus-Token") or "").strip()
            if supplied and hmac.compare_digest(supplied, prometheus_metrics_token):
                return True
        try:
            verify_jwt_in_request()
            role = getattr(g, "current_user_role", None)
            if not role:
                ident = get_jwt_identity()
                role = db_manager.get_user_role(ident) if ident else None
            return role in ("admin", "superuser")
        except Exception:
            return False

    @app.route('/metrics', methods=['GET'])
    @limiter.limit("60 per minute")
    def prometheus_metrics():
        if not prometheus_metrics_enabled:
            return Response("metrics disabled\n", status=404, mimetype="text/plain")
        if not _metrics_authorized():
            return Response("unauthorized\n", status=401, mimetype="text/plain")
        try:
            record_runtime_health(health_snapshot() or {})
        except Exception as e:
            log.debug("Runtime metric snapshot skipped: %s", e)
        payload = OBSERVABILITY_REGISTRY.render_prometheus()
        return Response(payload, mimetype="text/plain; version=0.0.4; charset=utf-8")

    @app.route('/api/status', methods=['GET'])
    @limiter.limit(
        str(getattr(Config, "WEB_RATE_LIMIT_STATUS", "60 per minute")),
        exempt_when=lambda: getattr(g, "limiter_exempt", False),
    )
    def status():
        """
        Get the unified health and system status of Elara and the Trading Bot.
        ---
        tags:
          - System Health
        security:
          - Bearer: []
        responses:
          200:
            description: Current ecosystem state
            schema:
              type: object
          401:
            description: Unauthorized
        """
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        redis_client = get_redis()
        redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        status_key = str(
            getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
            or trader_status_key(redis_prefix)
        ).strip()
        trading_bot = None
        try:
            if redis_client:
                trading_bot = redis_get_json(redis_client, status_key)
            if not trading_bot:
                status_path = ""
                if bool(getattr(Config, "TRADING_STATUS_FILE_FALLBACK_ENABLED", True)):
                    status_path = getattr(Config, "TRADING_STATUS_PATH", "")
                if status_path and os.path.exists(status_path):
                    with open(status_path, "r") as f:
                        trading_bot = json.load(f)
        except Exception as e:
            log.warning(f"Failed loading trading status snapshot: {e}")
            trading_bot = None
        with state_lock:
            snapshot = {
                "suhu": system_state.get("suhu", 0),
                "arus": system_state.get("arus", 0),
                "kwhToday": system_state.get("kwhToday", 0),
                "rupiahToday": system_state.get("rupiahToday", 0),
                "logs": list(system_state.get("logs", [])),
                "devices": system_state.get("devices", {}),
                "trading_metrics": system_state.get("trading_metrics", {}),
                "log_flood": system_state.get("log_flood", False)
            }
        snapshot["trading_bot"] = trading_bot
        try:
            metrics = db_manager.get_trading_metrics()
            if metrics:
                snapshot["trading_metrics"] = metrics
        except Exception as e:
            log.warning(f"Failed reading trading metrics: {e}")
        try:
            snapshot["client_ip"] = request.headers.get("X-Forwarded-For", request.remote_addr)
            snapshot["server_ip"] = socket.gethostbyname(socket.gethostname())
        except Exception:
            snapshot["client_ip"] = None
            snapshot["server_ip"] = None
        snapshot["health"] = health_snapshot()
        snapshot["skill_analytics"] = _markdown_skill_analytics_payload(include_recent=False)
        if state_manager:
            try:
                snapshot["presence"] = state_manager.get_state("presence_state", user_id="system") or "unknown"
            except Exception:
                snapshot["presence"] = "unknown"
        return jsonify(snapshot)

    @app.route('/api/skills/analytics', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def skill_analytics():
        auth_error = _require_admin_role_for_selfops()
        include_recent = auth_error is None
        payload = _markdown_skill_analytics_payload(include_recent=include_recent)
        if auth_error is None:
            payload["viewer_scope"] = "admin"
            return jsonify(payload)
        payload["viewer_scope"] = "user"
        payload.pop("recent_events", None)
        return jsonify(payload)

    @app.route('/api/admin/skills/registry', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_skill_registry():
        auth_error = _require_admin_role_for_selfops()
        if auth_error is not None:
            return auth_error
        payload = _markdown_registry_payload()
        payload["viewer_scope"] = "admin"
        return jsonify(payload)

    @app.route('/api/admin/skills/registry/refresh', methods=['POST'])
    @limiter.limit("24 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_skill_registry_refresh():
        auth_error = _require_admin_role_for_selfops()
        if auth_error is not None:
            return auth_error
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        force_flag = _coerce_bool(data.get("force"))
        reason = _compact_text(data.get("reason"), max_len=64) or "admin_api"
        payload = _refresh_markdown_registry_payload(force=(True if force_flag is None else force_flag), reason=reason)
        payload["viewer_scope"] = "admin"
        return jsonify(payload)

    @app.route('/api/skills/catalog', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def skill_catalog():
        user_id = str(get_jwt_identity() or "").strip().lower() or "default"
        query = _compact_text(request.args.get("q"), max_len=160)
        try:
            limit = int(request.args.get("limit", 30))
        except Exception:
            limit = 30
        limit = max(1, min(limit, 120))

        payload = _markdown_skill_catalog_payload(user_id=user_id, query=query, limit=limit)
        payload["viewer_scope"] = "user"
        payload["query"] = query
        payload["limit"] = limit
        return jsonify(payload)

    @app.route('/api/skills/feedback', methods=['POST'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def skill_feedback():
        user_id = str(get_jwt_identity() or "").strip().lower() or "default"
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}

        user_msg = _compact_text(data.get("message") or data.get("query"), max_len=600)
        if not user_msg:
            return jsonify({"message": "message/query is required"}), 400

        feedback_value = data.get("feedback")
        if feedback_value is None:
            feedback_value = data.get("helpful")
        skill_name = _compact_text(data.get("skill_name") or data.get("skill"), max_len=96).strip().lower()
        match_kind = _compact_text(data.get("match_kind"), max_len=32).strip().lower()
        note = _compact_text(data.get("note"), max_len=240)

        payload = _submit_markdown_feedback_payload(
            user_id=user_id,
            user_msg=user_msg,
            feedback_value=feedback_value,
            skill_name=skill_name,
            match_kind=match_kind,
            note=note,
        )
        payload["viewer_scope"] = "user"
        if not bool(payload.get("available")):
            return jsonify(payload), 503
        if not bool(payload.get("recorded")):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route('/api/admin/skills/evaluation', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_skill_evaluation():
        auth_error = _require_admin_role_for_selfops()
        if auth_error is not None:
            return auth_error

        actor = str(get_jwt_identity() or "").strip().lower() or "default"
        target_user = _compact_text(request.args.get("user_id"), max_len=160).strip().lower() or actor
        query = _compact_text(request.args.get("q"), max_len=300)
        include_recent_flag = _coerce_bool(request.args.get("include_recent"))
        include_recent = True if include_recent_flag is None else bool(include_recent_flag)
        try:
            top_n = int(request.args.get("top", 12))
        except Exception:
            top_n = 12
        top_n = max(1, min(top_n, 50))

        payload = _markdown_skill_evaluation_payload(
            user_id=target_user,
            top_n=top_n,
            include_recent=include_recent,
            query=query,
        )
        payload["viewer_scope"] = "admin"
        payload["user_id"] = target_user
        payload["query"] = query
        payload["top_n"] = top_n
        if not bool(payload.get("available")):
            return jsonify(payload), 503
        return jsonify(payload)

    @app.route('/api/admin/skills/route-preview', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_skill_route_preview():
        auth_error = _require_admin_role_for_selfops()
        if auth_error is not None:
            return auth_error

        actor = str(get_jwt_identity() or "").strip().lower() or "default"
        target_user = _compact_text(request.args.get("user_id"), max_len=160).strip().lower() or actor
        query = _compact_text(request.args.get("q") or request.args.get("message"), max_len=300)
        if not query:
            return jsonify({"message": "q/message is required"}), 400
        try:
            top_k = int(request.args.get("top_k", 6))
        except Exception:
            top_k = 6
        top_k = max(1, min(top_k, 20))

        payload = _markdown_route_preview_payload(user_id=target_user, query=query, top_k=top_k)
        payload["viewer_scope"] = "admin"
        payload["user_id"] = target_user
        payload["query"] = query
        payload["top_k"] = top_k
        if not bool(payload.get("available", True)):
            return jsonify(payload), 503
        return jsonify(payload)

    @app.route('/api/admin/skills/feedback/reset', methods=['POST'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_skill_feedback_reset():
        auth_error = _require_admin_role_for_selfops()
        if auth_error is not None:
            return auth_error
        payload = _reset_markdown_feedback_payload()
        payload["viewer_scope"] = "admin"
        if not bool(payload.get("available", True)):
            return jsonify(payload), 503
        if not bool(payload.get("reset", False)):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route('/api/device', methods=['POST'])
    @limiter.limit("120 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def control_device():
        """
        Control a configured device (on/off/set) through DeviceControlSkill.
        ---
        tags:
          - Device Control
        """
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        if not mqtt_service:
            return jsonify({"message": "MQTT service not available"}), 503
        if not hasattr(mqtt_service, "device_config"):
            return jsonify({"message": "MQTT device config not available"}), 503
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if DeviceControlPayload:
            try:
                data = _validate_payload(DeviceControlPayload, data, by_alias=True)
            except ValidationError:
                return jsonify({"message": "Invalid payload"}), 400
        dev_type = _compact_text(data.get("type"), max_len=64).lower()
        room = _compact_text(data.get("room"), max_len=64).lower()
        action = _compact_text(data.get("action"), max_len=32).lower()
        value = data.get("value")

        if not dev_type or not room or not action:
            return jsonify({"message": "Invalid payload"}), 400
        if not _is_safe_device_token(dev_type) or not _is_safe_device_token(room):
            return jsonify({"message": "Invalid device/room format"}), 400

        catalog = _device_catalog(getattr(mqtt_service, "device_config", {}) or {})
        if catalog:
            if dev_type not in catalog:
                return jsonify({"message": "Unknown device type"}), 400
            if room not in catalog.get(dev_type, set()):
                return jsonify({"message": "Unknown room for device type"}), 400

        # Build NLPUnderstanding and route via DeviceControlSkill to enforce central logic/state
        intent_map = {
            "on": "turn_on_device",
            "off": "turn_off_device",
            "set": "set_device_value"
        }
        intent = intent_map.get(action)
        if not intent:
            return jsonify({"message": "Invalid action"}), 400

        if action == "set":
            if value is None:
                return jsonify({"message": "Missing value for set action"}), 400
            try:
                numeric_value = float(value)
            except Exception:
                return jsonify({"message": "Value must be numeric"}), 400
            if not math.isfinite(numeric_value):
                return jsonify({"message": "Value must be finite"}), 400
            if numeric_value < device_set_min or numeric_value > device_set_max:
                return (
                    jsonify(
                        {
                            "message": "Value out of allowed range",
                            "min": device_set_min,
                            "max": device_set_max,
                        }
                    ),
                    400,
                )
            value = int(numeric_value) if numeric_value.is_integer() else numeric_value

        entities = [
            Entity(type="device", value=dev_type),
            Entity(type="room", value=room)
        ]
        if action == "set" and value is not None:
            entities.append(Entity(type="number", value=value))
        nlu = NLPUnderstanding(
            intent=intent,
            confidence=1.0,
            entities=entities,
            raw_text=f"{action} {dev_type} {room} {value if value is not None else ''}".strip()
        )

        skill_manager = getattr(elara_skill, "skill_manager", None)
        target_skill = skill_manager.intent_map.get(intent) if skill_manager else None
        if not target_skill:
            return jsonify({"message": "Device control skill unavailable"}), 503

        try:
            request_id = uuid4().hex
            command_user_id = _resolve_command_user_id("web_user")
            prefer_background = _prefer_background_execution(data)
            res, queued, err = _submit_command(
                target_skill.handle(nlu, user_id=command_user_id),
                request_id=request_id,
                source="web_device_control",
                background_only=prefer_background,
            )
            if err is not None:
                raise err
            if queued:
                return (
                    jsonify(
                        {
                            "status": "queued",
                            "request_id": request_id,
                            "message": "Device command accepted and running in background.",
                        }
                    ),
                    202,
                )
            return jsonify({"status": "success", "message": res, "request_id": request_id})
        except Exception as e:
            log.error(f"Device control via skill failed: {e}")
            return jsonify({"message": "Device control error"}), 500
    @app.route('/api/tg/control', methods=['POST'])
    @limiter.limit("30 per minute")
    def tg_control():
        """
        Execute Telegram WebApp command with CSRF + dual-token validation.
        ---
        tags:
          - Telegram WebApp
        security:
          - Bearer: []
        """
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if TgControlPayload:
            try:
                data = _validate_payload(TgControlPayload, data, by_alias=True)
            except ValidationError:
                return jsonify({"message": "Invalid payload"}), 400
        cookie_token = request.cookies.get('tg_webapp_auth')
        header_token = request.headers.get("X-TG-TOKEN")
        csrf_header = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        valid_token = Config.TG_WEBAPP_TOKEN or ""
        cookie_ok = bool(cookie_token and valid_token and secrets.compare_digest(str(cookie_token), str(valid_token)))
        token_ok = bool(header_token and valid_token and secrets.compare_digest(str(header_token), str(valid_token)))
        cmd = _compact_text(data.get('command'), max_len=max_command_chars)
        if csrf_enabled and not csrf_header:
            return jsonify({"message": "Missing CSRF token"}), 400
        if not valid_token or not (cookie_ok and token_ok):
            return jsonify({"message": "Unauthorized"}), 403
        if not cmd:
            return jsonify({"message": "Perintah kosong / melebihi batas panjang"}), 400
        request_id = uuid4().hex
        prefer_background = _prefer_background_execution(data)
        log.info("TG WebApp Command: %s", _redact_command_for_log(cmd))
        res, queued, err = _submit_command(
            elara_skill.handle_command(cmd, "tg_webapp"),
            request_id=request_id,
            source="tg_web_control",
            background_only=prefer_background,
        )
        if err is not None:
            log.error(f"TG command failed (request_id={request_id}): {err}")
            return jsonify({"message": "Command execution error", "request_id": request_id}), 500
        if queued:
            return (
                jsonify(
                    {
                        "status": "queued",
                        "request_id": request_id,
                        "message": "Command accepted and running in background.",
                    }
                ),
                202,
            )
        return jsonify({"status": "success", "message": res, "request_id": request_id})

    def _validate_socket_token(auth_payload=None):
        if AUTH_DISABLED:
            return True
        token = ""
        try:
            auth_header = str(request.headers.get("Authorization", "") or "").strip()
            if auth_header.lower().startswith("bearer "):
                token = auth_header.split(" ", 1)[1].strip()
        except Exception:
            token = ""
        if not token:
            try:
                token = str(
                    request.cookies.get(str(app.config.get("JWT_ACCESS_COOKIE_NAME") or "elara_access_token")) or ""
                ).strip()
            except Exception:
                token = ""
        if not token and isinstance(auth_payload, dict):
            maybe = str(auth_payload.get("token") or "").strip()
            if maybe:
                token = maybe
        if not token:
            log.warning("Socket connect rejected: missing token")
            return False
        try:
            payload = decode_token(token) or {}
            jti = str(payload.get("jti") or "").strip()
            if jti and _is_jti_revoked(jti):
                log.warning("Socket connect rejected: token revoked (blocklist).")
                return False
            if jti and db_manager and db_manager.is_user_session_revoked(jti):
                log.warning("Socket connect rejected: token revoked (db).")
                return False
            return True
        except Exception as e:
            log.warning(f"Socket connect rejected: {e}")
            return False

    @socketio.on('connect')
    def handle_connect(auth=None):
        if not _validate_socket_token(auth_payload=auth):
            return False
        sid = str(getattr(request, "sid", "") or "").strip()
        _ensure_market_stream_loop_started()
        _upsert_market_subscription(sid, {})
        _emit_market_payload_to_sid(sid, force_refresh=True)
        log.info("Client Connected to Dashboard")

    @socketio.on('disconnect')
    def handle_disconnect():
        sid = str(getattr(request, "sid", "") or "").strip()
        _drop_market_subscription(sid)
        log.info("Client Disconnected")

    @socketio.on('market_subscribe')
    def handle_market_subscribe(data=None):
        sid = str(getattr(request, "sid", "") or "").strip()
        sub = _upsert_market_subscription(sid, data)
        if not sub:
            return
        _emit_market_payload_to_sid(sid, force_refresh=bool(sub.get("refresh")))

    @socketio.on('market_ping')
    def handle_market_ping(data=None):
        sid = str(getattr(request, "sid", "") or "").strip()
        if not sid:
            return
        seq = 0
        if isinstance(data, dict):
            try:
                seq = int(data.get("seq") or 0)
            except Exception:
                seq = 0
        payload = {"ts": time.time()}
        if seq > 0:
            payload["seq"] = seq
        try:
            socketio.emit("market_pong", payload, room=sid)
        except Exception as e:
            log.debug(f"market pong emit skipped sid={sid}: {e}")

    def _enqueue_trading_command(cmd: str, source: str, reason: str, user_id: str):
        payload = build_trade_command_message(
            cmd=cmd,
            source=source,
            reason=reason,
            user_id=user_id,
            priority=1,
        )
        try:
            redis_client = get_redis()
            redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
            queue_key = str(
                getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
                or trade_command_queue_key(redis_prefix)
            ).strip()
            if redis_client:
                redis_client.lpush(queue_key, json.dumps(payload))
                return True, "redis"
        except Exception as e:
            log.warning(f"Trading command redis enqueue failed: {e}")
        api_base = str(getattr(Config, "TRADER_API_BASE_URL", "") or "").rstrip("/")
        if api_base:
            try:
                timeout = max(0.2, float(getattr(Config, "TRADER_API_TIMEOUT_SEC", 2.0) or 2.0))
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{api_base}/command",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        **(
                            {"X-IPC-Token": str(getattr(Config, "TRADING_IPC_TOKEN", "") or "").strip()}
                            if str(getattr(Config, "TRADING_IPC_TOKEN", "") or "").strip()
                            else {}
                        ),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = getattr(resp, "status", None)
                    if status is None:
                        status = resp.getcode()
                    if status is None or int(status) < 400:
                        return True, "http"
            except Exception as e:
                log.warning(f"Trading command HTTP enqueue failed: {e}")
        if not bool(getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True)):
            return False, "none"
        try:
            command_path = getattr(Config, "TRADING_COMMAND_PATH", None)
            if not command_path:
                return False, "none"
            with open(command_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            return True, "file"
        except Exception as e:
            log.error(f"Trading command error: {e}")
            return False, "none"

    @app.route('/api/trading/command', methods=['POST'])
    @limiter.limit(
        str(getattr(Config, "WEB_RATE_LIMIT_TRADING_COMMAND", "20 per minute")),
        exempt_when=lambda: getattr(g, "limiter_exempt", False),
    )
    def trading_command():
        """
        Queue critical trading command (`stop|flatten|block|resume`) with replay-guard validation.
        """
        if not AUTH_DISABLED:
            verify_jwt_in_request()
        if not _verify_replay_guard():
            _audit_action("trading_command_rejected", 401, reason="replay_guard")
            _track_user_activity("trading_command_rejected", 401, details="replay_guard")
            return jsonify({"message": "Replay guard rejected request"}), 401
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if TradingCommandPayload:
            try:
                data = _validate_payload(TradingCommandPayload, data)
            except ValidationError:
                _audit_action("trading_command_rejected", 400, reason="invalid_payload")
                _track_user_activity("trading_command_rejected", 400, details="invalid_payload")
                return jsonify({"message": "Invalid payload"}), 400
        cmd = (data.get("cmd") or "").lower()
        if cmd not in ["stop", "flatten", "block", "resume"]:
            _audit_action("trading_command_rejected", 400, reason="invalid_cmd", cmd=cmd)
            _track_user_activity("trading_command_rejected", 400, details=f"invalid_cmd:{cmd}")
            return jsonify({"message": "Invalid cmd"}), 400
        source = "web_api"
        reason = _compact_text(data.get("reason"), max_len=max_reason_chars)
        user_id = _compact_text(data.get("user_id"), max_len=64) or _resolve_command_user_id("web_user")
        ok, transport = _enqueue_trading_command(cmd=cmd, source=source, reason=reason, user_id=user_id)
        if ok:
            _audit_action(
                "trading_command_queued",
                200,
                cmd=cmd,
                user_id=user_id,
                source=source,
                transport=transport,
            )
            _track_user_activity(
                "trading_command_queued",
                200,
                details=f"{cmd} via {transport}",
            )
            return jsonify({"status": "queued", "cmd": cmd, "transport": transport})
        _audit_action("trading_command_failed", 503, cmd=cmd, user_id=user_id, source=source)
        _track_user_activity("trading_command_failed", 503, details=cmd)
        return jsonify({"message": "Redis unavailable and file fallback disabled"}), 503

    @app.route('/api/trading/decisions', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def trading_decisions():
        try:
            limit = int(request.args.get("limit", 20))
        except Exception:
            limit = 20
        limit = max(1, min(limit, 200))
        paper_ledger_path = str(os.getenv("PAPER_LEDGER_PATH", "runtime/paper_ledger.jsonl")).strip()
        paper_rows = PaperAuditRepository(EventLedger(paper_ledger_path)).recent_rows(limit=limit)
        rows = paper_rows or (db_manager.get_recent_trades(limit=limit) or [])
        return jsonify({"count": len(rows), "items": rows})

    @app.route('/api/trading/decisions/<trace_id>/replay', methods=['GET'])
    @limiter.limit("60 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def trading_decision_replay(trace_id):
        paper_ledger_path = str(os.getenv("PAPER_LEDGER_PATH", "runtime/paper_ledger.jsonl")).strip()
        replay = PaperAuditRepository(EventLedger(paper_ledger_path)).replay(trace_id)
        if not replay.found:
            return jsonify({"message": "Decision not found"}), 404
        return jsonify(replay.to_dict())

    @app.route('/api/admin/panic', methods=['POST'])
    @limiter.limit(
        str(getattr(Config, "WEB_RATE_LIMIT_PANIC", "5 per minute")),
        exempt_when=lambda: getattr(g, "limiter_exempt", False),
    )
    def panic_button():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("panic_button_rejected", 401, reason="auth")
            _track_user_activity("panic_button_rejected", 401, details="auth")
            return auth_error
        if not _verify_replay_guard():
            _audit_action("panic_button_rejected", 401, reason="replay_guard")
            _track_user_activity("panic_button_rejected", 401, details="replay_guard")
            return jsonify({"message": "Replay guard rejected request"}), 401
        data = request.get_json(silent=True) or {}
        reason = _compact_text(data.get("reason"), max_len=max_reason_chars) or "panic_button"
        user_id = _compact_text(data.get("user_id"), max_len=64) or "admin"

        flatten_ok, flatten_transport = _enqueue_trading_command(
            cmd="flatten",
            source="panic_button",
            reason=reason,
            user_id=user_id,
        )
        stop_ok, stop_transport = _enqueue_trading_command(
            cmd="stop",
            source="panic_button",
            reason=reason,
            user_id=user_id,
        )
        payload = {
            "status": "queued" if (flatten_ok and stop_ok) else "partial_failure",
            "commands": {
                "flatten": {"queued": flatten_ok, "transport": flatten_transport},
                "stop": {"queued": stop_ok, "transport": stop_transport},
            },
            "reason": reason,
        }
        _audit_action(
            "panic_button_queued",
            200 if (flatten_ok and stop_ok) else 503,
            reason=reason,
            user_id=user_id,
            flatten_ok=flatten_ok,
            stop_ok=stop_ok,
        )
        _track_user_activity(
            "panic_button_triggered",
            200 if (flatten_ok and stop_ok) else 503,
            details=reason,
        )
        return jsonify(payload), (200 if (flatten_ok and stop_ok) else 503)

    @app.route('/api/admin/users', methods=['GET'])
    @limiter.limit("30 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_list_users():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            return auth_error
        try:
            limit = int(request.args.get("limit", 200))
        except Exception:
            limit = 200
        limit = max(1, min(limit, 1000))
        rows = db_manager.list_users(limit=limit) or []
        return jsonify({"users": rows, "count": len(rows)})

    @app.route('/api/admin/users', methods=['POST'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_create_user():
        """
        Create new user account (admin/superuser only).
        """
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("admin_create_user_rejected", 401, reason="auth")
            _track_user_activity("admin_create_user_rejected", 401, details="auth")
            return auth_error
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if AdminCreateUserPayload:
            try:
                data = _validate_payload(AdminCreateUserPayload, data)
            except ValidationError:
                _audit_action("admin_create_user_rejected", 400, reason="invalid_payload")
                _track_user_activity("admin_create_user_rejected", 400, details="invalid_payload")
                return jsonify({"message": "payload tidak valid"}), 400
        email = _compact_text(data.get("email"), max_len=max_email_chars)
        password = _compact_text(data.get("password"), max_len=max_password_chars)
        name = _compact_text(data.get("name"), max_len=80)
        role = _compact_text(data.get("role"), max_len=20).lower() or "user"

        if not email or not password:
            _audit_action("admin_create_user_rejected", 400, reason="missing_fields")
            _track_user_activity("admin_create_user_rejected", 400, details="missing_fields")
            return jsonify({"message": "email/password wajib"}), 400
        if role not in {"user", "admin", "superuser"}:
            _audit_action("admin_create_user_rejected", 400, reason="invalid_role", role=role)
            _track_user_activity("admin_create_user_rejected", 400, details=f"invalid_role:{role}")
            return jsonify({"message": "role tidak valid"}), 400

        created = db_manager.create_user(email=email, password=password, name=name, role=role) or {}
        if not created:
            _audit_action("admin_create_user_failed", 400, reason="create_failed", email=email)
            _track_user_activity("admin_create_user_failed", 400, details=email)
            return jsonify({"message": "Gagal create user / email sudah ada"}), 400
        _audit_action("admin_create_user_success", 201, email=email, role=role)
        _track_user_activity("admin_create_user_success", 201, details=f"{email} ({role})")
        return jsonify({"status": "created", "user": created}), 201

    @app.route('/api/admin/users/<int:user_id>', methods=['PATCH'])
    @limiter.limit("30 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    @jwt_required()
    def admin_update_user(user_id: int):
        """
        Update role/name/password for existing user (admin/superuser only).
        """
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("admin_update_user_rejected", 401, reason="auth", target_user_id=user_id)
            _track_user_activity("admin_update_user_rejected", 401, details=f"auth target={user_id}")
            return auth_error
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if AdminUpdateUserPayload:
            try:
                data = _validate_payload(AdminUpdateUserPayload, data, exclude_none=True)
            except ValidationError:
                _audit_action("admin_update_user_rejected", 400, reason="invalid_payload", target_user_id=user_id)
                _track_user_activity("admin_update_user_rejected", 400, details=f"invalid_payload target={user_id}")
                return jsonify({"message": "payload tidak valid"}), 400
        role_in = data.get("role")
        role = _compact_text(role_in, max_len=20).lower() if role_in is not None else None
        name_in = data.get("name")
        name = _compact_text(name_in, max_len=80) if name_in is not None else None
        password_in = data.get("password")
        password = _compact_text(password_in, max_len=max_password_chars) if password_in is not None else None

        if role is not None and role not in {"user", "admin", "superuser"}:
            _audit_action("admin_update_user_rejected", 400, reason="invalid_role", role=role, target_user_id=user_id)
            _track_user_activity("admin_update_user_rejected", 400, details=f"invalid_role:{role} target={user_id}")
            return jsonify({"message": "role tidak valid"}), 400

        updated = db_manager.update_user(
            user_id=user_id,
            role=role,
            name=name,
            password=password,
        ) or {}
        if not updated:
            _audit_action("admin_update_user_failed", 404, reason="not_found_or_update_failed", target_user_id=user_id)
            _track_user_activity("admin_update_user_failed", 404, details=f"target={user_id}")
            return jsonify({"message": "User tidak ditemukan / update gagal"}), 404
        _audit_action("admin_update_user_success", 200, target_user_id=user_id)
        _track_user_activity("admin_update_user_success", 200, details=f"target={user_id}")
        return jsonify({"status": "updated", "user": updated})

    @app.route('/api/selfops/status', methods=['GET'])
    @limiter.limit("20 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def selfops_status():
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("selfops_status_rejected", 401, reason="auth")
            return auth_error
        if not self_heal_service:
            _audit_action("selfops_status_unavailable", 503)
            return jsonify({"message": "Self-ops unavailable"}), 503
        _audit_action("selfops_status_success", 200)
        return jsonify(self_heal_service.status())

    @app.route('/api/selfops/heal', methods=['POST'])
    @limiter.limit("5 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def selfops_heal():
        """
        Trigger self-heal routine (sync/async mode with rate limit guard).
        """
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("selfops_heal_rejected", 401, reason="auth")
            _track_user_activity("selfops_heal_rejected", 401, details="auth")
            return auth_error
        if not self_heal_service:
            _audit_action("selfops_heal_unavailable", 503)
            _track_user_activity("selfops_heal_unavailable", 503)
            return jsonify({"message": "Self-ops unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        if SelfOpsHealPayload:
            try:
                payload = _validate_payload(SelfOpsHealPayload, payload, exclude_none=True)
            except ValidationError:
                _audit_action("selfops_heal_rejected", 400, reason="invalid_payload")
                _track_user_activity("selfops_heal_rejected", 400, details="invalid_payload")
                return jsonify({"message": "payload tidak valid"}), 400
        prefer_sync = _coerce_bool(payload.get("sync"))
        wait = True if prefer_sync is True else False
        report = self_heal_service.heal_now(wait=wait)
        if bool((report or {}).get("rate_limited")):
            _audit_action("selfops_heal_rejected", 429, reason="rate_limited")
            _track_user_activity("selfops_heal_rejected", 429, details="rate_limited")
            return jsonify(report), 429
        if bool((report or {}).get("queued")):
            _audit_action("selfops_heal_queued", 202, wait=wait)
            _track_user_activity("selfops_heal_queued", 202, details=f"wait={wait}")
            return jsonify(report), 202
        _audit_action("selfops_heal_success", 200, wait=wait)
        _track_user_activity("selfops_heal_success", 200, details=f"wait={wait}")
        return jsonify(report), 200

    @app.route('/api/selfops/deploy', methods=['POST'])
    @limiter.limit("3 per minute", exempt_when=lambda: getattr(g, "limiter_exempt", False))
    def selfops_deploy():
        """
        Trigger self-deploy workflow with optional pull/install/restart flags.
        """
        auth_error = _require_admin_role_for_selfops()
        if auth_error:
            _audit_action("selfops_deploy_rejected", 401, reason="auth")
            _track_user_activity("selfops_deploy_rejected", 401, details="auth")
            return auth_error
        if not self_heal_service:
            _audit_action("selfops_deploy_unavailable", 503)
            _track_user_activity("selfops_deploy_unavailable", 503)
            return jsonify({"message": "Self-ops unavailable"}), 503
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        if SelfOpsDeployPayload:
            try:
                data = _validate_payload(SelfOpsDeployPayload, data, exclude_none=True)
            except ValidationError:
                _audit_action("selfops_deploy_rejected", 400, reason="invalid_payload")
                _track_user_activity("selfops_deploy_rejected", 400, details="invalid_payload")
                return jsonify({"message": "payload tidak valid"}), 400
        pull = bool(data.get("pull", getattr(Config, "SELF_DEPLOY_AUTO_PULL", True)))
        install = bool(data.get("install", getattr(Config, "SELF_DEPLOY_AUTO_INSTALL", False)))
        restart = bool(data.get("restart", getattr(Config, "SELF_DEPLOY_AUTO_RESTART", False)))
        result = self_heal_service.deploy_now(pull=pull, install=install, restart=restart)
        _audit_action("selfops_deploy_success", 200, pull=pull, install=install, restart=restart)
        _track_user_activity(
            "selfops_deploy_success",
            200,
            details=f"pull={pull},install={install},restart={restart}",
        )
        return jsonify(result)

    def _register_api_v1_aliases():
        if not bool(getattr(Config, "WEB_ENABLE_API_V1_ALIASES", True)):
            return
        existing_paths = {r.rule for r in app.url_map.iter_rules()}
        for rule in list(app.url_map.iter_rules()):
            if not rule.rule.startswith("/api/"):
                continue
            if rule.rule.startswith("/api/v1/"):
                continue
            alias = f"/api/v1{rule.rule[4:]}"
            if alias in existing_paths:
                continue
            methods = sorted([m for m in rule.methods if m not in {"HEAD", "OPTIONS"}])
            endpoint_name = f"{rule.endpoint}__v1"
            view_fn = app.view_functions.get(rule.endpoint)
            if not view_fn:
                continue
            app.add_url_rule(alias, endpoint=endpoint_name, view_func=view_fn, methods=methods)
            existing_paths.add(alias)

    _register_api_v1_aliases()

    return app, socketio

