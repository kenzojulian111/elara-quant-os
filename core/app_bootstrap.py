import asyncio
import importlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

os.makedirs("logs", exist_ok=True)
fallback_file_handler = RotatingFileHandler(
    "logs/elara_backend.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        fallback_file_handler
    ]
)
log = logging.getLogger("elara.main")
_OPTIONAL_COMPONENT_IMPORT_ERRORS: Dict[str, str] = {}
_OPTIONAL_ATTR_CACHE: Dict[str, Any] = {}


def _mark_component_import_error(name: str, error: Exception):
    _OPTIONAL_COMPONENT_IMPORT_ERRORS[name] = str(error)

try:
    from utils.telegram_logger import setup_telegram_logging
except ImportError as e:
    _mark_component_import_error("telegram_logger", e)
    log.error(f"Telegram logger import failed; continuing without Telegram log sink: {e}")
    setup_telegram_logging = lambda: None

try:
    from config.settings import Config
    from core.events import (
        build_core_heartbeat_event,
        build_trade_command_message,
        build_trade_signal_event,
        core_heartbeat_key,
        trade_signal_channel,
        trade_signal_stream_key,
    )
    from core.state_manager import StateManager
    from core.skill_manager import SkillManager
    from core.nlp_engine import ElaraNLP
    from core.life import LifeManager
    from database.database_manager import DatabaseManager
    from skills.elara_skill import ElaraSkill
    from utils.common import async_runner
    from utils.log_redaction import attach_redaction_filter
    from utils.self_ops import SelfOpsManager
    from utils.redis_helper import get_redis, redis_readiness_probe
    from utils.system_policy import effective_required_components, subsystem_runtime_enabled
    from trading_core.coordination.external_bus import RedisStreamsExternalBusAdapter
except ImportError as e:
    log.critical(f"Gagal import Core Modules. Cek struktur folder! Error: {e}")
    sys.exit(1)

def _load_optional_attr(
    module_path: str,
    attr_name: str,
    component_name: str,
    *,
    log_level: str = "warning",
):
    cache_key = f"{module_path}:{attr_name}"
    if cache_key in _OPTIONAL_ATTR_CACHE:
        return _OPTIONAL_ATTR_CACHE[cache_key]
    try:
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        _OPTIONAL_ATTR_CACHE[cache_key] = attr
        return attr
    except Exception as e:
        _OPTIONAL_ATTR_CACHE[cache_key] = None
        _mark_component_import_error(component_name, e)
        if log_level == "error":
            log.error(f"{component_name} disabled: {e}")
        else:
            log.warning(f"{component_name} disabled: {e}")
        return None


class UserProfileService:
    def __init__(self, db_manager):
        self.db = db_manager
    
    def get_profile(self, user_id):
        profile = {}
        if self.db and hasattr(self.db, "get_user_profile"):
            try:
                loaded = self.db.get_user_profile(user_id)
                if isinstance(loaded, dict):
                    profile = loaded
            except Exception:
                profile = {}

        raw_name = (
            profile.get("name")
            or profile.get("full_name")
            or profile.get("email")
            or user_id
            or "User"
        )
        name = str(raw_name).strip() or "User"
        location = str(profile.get("location") or os.getenv("DEFAULT_LOCATION", "")).strip()

        prefs = profile.get("briefing_preferences")
        if isinstance(prefs, str):
            prefs = [p.strip() for p in prefs.split(",") if p.strip()]
        elif not isinstance(prefs, list):
            env_prefs = os.getenv("BRIEFING_PREFERENCES", "weather,calendar,tasks,system")
            prefs = [p.strip() for p in str(env_prefs or "").split(",") if p.strip()]
        if not prefs:
            prefs = ["system"]

        return {
            "name": name,
            "location": location,
            "briefing_preferences": prefs,
        }

class DummyMQTT:
    def publish(self, *_, **__):
        return False

    def control_device(self, *_, **__):
        return False


def _looks_like_placeholder_secret(secret: str) -> bool:
    s = str(secret or "").strip().lower()
    if not s:
        return False
    weak_exact = {
        "rahasia_illahi_elara_2025",
        "rahasia_default",
        "default",
        "default_secret",
        "secret",
        "password",
        "12345678",
    }
    if s in weak_exact:
        return True
    markers = [
        "abcd1234",
        "your_api_key",
        "replace-with-",
        "replace_me",
        "changeme",
        "example",
        "dummy",
        "test_key",
    ]
    return any(m in s for m in markers)


def _is_production_like_env() -> bool:
    env_candidates = [
        getattr(Config, "ENV", ""),
        os.getenv("APP_ENV", ""),
        os.getenv("FLASK_ENV", ""),
    ]
    normalized = {str(v or "").strip().lower() for v in env_candidates if str(v or "").strip()}
    return any(v in {"production", "prod"} for v in normalized)


def _is_live_trading_mode() -> bool:
    raw_mode = str(
        os.getenv("TRADING_MODE")
        or os.getenv("BOT_MODE")
        or getattr(Config, "TRADING_MODE", "")
        or ""
    ).strip().lower()
    return raw_mode == "live"


def _parse_csv_set(raw_value: str) -> set:
    return {
        part.strip().lower()
        for part in str(raw_value or "").split(",")
        if part.strip()
    }


def _parse_csv_list(raw_value: str) -> list:
    return [
        part.strip()
        for part in str(raw_value or "").split(",")
        if part.strip()
    ]


def _write_json_atomic(path: str, payload: dict):
    parent_dir = os.path.dirname(path) or "."
    os.makedirs(parent_dir, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _parse_web_proxy_hops(raw_value: Any) -> int:
    hops = int(raw_value)
    if hops < 1:
        raise ValueError("WEB_REVERSE_PROXY_HOPS must be >= 1")
    return hops


def _resolve_core_heartbeat_key(redis_prefix: str) -> str:
    return str(
        getattr(Config, "CORE_HEARTBEAT_KEY", core_heartbeat_key(redis_prefix))
        or core_heartbeat_key(redis_prefix)
    ).strip()


def _resolve_core_heartbeat_path(file_fallback_enabled: bool) -> str:
    heartbeat_path = str(getattr(Config, "CORE_HEARTBEAT_PATH", "") or "").strip()
    if file_fallback_enabled and not heartbeat_path:
        heartbeat_path = os.path.join(getattr(Config, "root_path", os.getcwd()), "runtime", "core_heartbeat.json")
    return heartbeat_path


def _validate_runtime_config():
    secret = str(getattr(Config, "SECRET_KEY", "") or "").strip()
    jwt_secret = str(getattr(Config, "JWT_SECRET_KEY", "") or "").strip()
    db_uri = str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "") or "").strip()
    using_sqlite = db_uri.startswith("sqlite")
    is_prod_env = _is_production_like_env()
    is_live_mode = _is_live_trading_mode()
    state_backend = str(getattr(Config, "STATE_BACKEND", "auto") or "auto").strip().lower()
    state_backend_fail_fast = bool(getattr(Config, "STATE_BACKEND_FAIL_FAST", False))
    state_require_redis_prod = bool(getattr(Config, "STATE_REQUIRE_REDIS_FOR_PROD", True))
    state_require_redis_live = bool(getattr(Config, "STATE_REQUIRE_REDIS_FOR_LIVE_TRADING", True))
    cors_origins_raw = str(getattr(Config, "CORS_ORIGINS", "") or "").strip()
    cors_origins = _parse_csv_list(cors_origins_raw)
    state_requires_redis = (
        (is_prod_env and state_require_redis_prod)
        or (is_live_mode and state_require_redis_live)
    )
    debug_enabled = bool(getattr(Config, "DEBUG", False))
    llm_primary = str(getattr(Config, "LLM_PRIMARY_PROVIDER", "") or "").strip().lower()
    nlu_providers = _parse_csv_set(getattr(Config, "LLM_NLU_PROVIDERS", ""))
    openai_key = str(getattr(Config, "OPENAI_API_KEY", "") or "").strip()
    nvidia_key = str(getattr(Config, "NVIDIA_API_KEY", "") or "").strip()
    groq_key = str(getattr(Config, "GROQ_API_KEY", "") or "").strip()
    gemini_key = str(getattr(Config, "GEMINI_API_KEY", "") or "").strip()
    llm_allow_no_key_in_dev = bool(getattr(Config, "LLM_ALLOW_NO_API_KEY_IN_DEV", True))
    llm_require_primary_key = bool(getattr(Config, "LLM_REQUIRE_PRIMARY_API_KEY", is_prod_env))
    llm_base_url = str(os.getenv("LLM_BASE_URL", "") or "").strip()
    llm_nvidia_endpoint = bool(llm_base_url and "integrate.api.nvidia.com" in llm_base_url.lower())
    web_proxy_required_prod = bool(getattr(Config, "WEB_REVERSE_PROXY_REQUIRED_IN_PROD", True))
    web_proxy_enabled = bool(getattr(Config, "WEB_REVERSE_PROXY_ENABLED", False))
    try:
        web_proxy_hops = _parse_web_proxy_hops(getattr(Config, "WEB_REVERSE_PROXY_HOPS", 1))
    except Exception:
        log.critical("WEB_REVERSE_PROXY_HOPS harus berupa integer >= 1.")
        sys.exit(1)

    if Config.ALLOW_DEV_BACKDOOR and is_prod_env:
        log.critical("ALLOW_DEV_BACKDOOR aktif di production. Nonaktifkan untuk keamanan.")
        sys.exit(1)

    if debug_enabled and is_prod_env:
        log.critical("DEBUG mode aktif di production. Set DEBUG=0.")
        sys.exit(1)

    if not secret and not Config.ALLOW_DEV_BACKDOOR:
        log.critical("SECRET_KEY kosong. Set env SECRET_KEY atau aktifkan ALLOW_DEV_BACKDOOR=1 untuk mode dev.")
        sys.exit(1)
    if _looks_like_placeholder_secret(secret) and not Config.ALLOW_DEV_BACKDOOR:
        log.critical(
            "SECRET_KEY terdeteksi lemah/default. "
            "Ganti SECRET_KEY dengan nilai kuat atau aktifkan ALLOW_DEV_BACKDOOR=1 untuk mode dev."
        )
        sys.exit(1)
    if _looks_like_placeholder_secret(secret):
        log.warning("SECRET_KEY lemah/default (DEV BACKDOOR MODE). Jangan gunakan di production.")
    if not jwt_secret:
        log.critical("JWT_SECRET_KEY kosong. Set env JWT_SECRET_KEY atau SECRET_KEY.")
        sys.exit(1)
    if _looks_like_placeholder_secret(jwt_secret) and not Config.ALLOW_DEV_BACKDOOR:
        log.critical("JWT_SECRET_KEY terdeteksi lemah/default. Ganti dengan nilai kuat.")
        sys.exit(1)
    if Config.BOT_TOKEN and Config.TG_ALLOWED_USER == 0:
        log.warning("TG_ALLOWED_USER belum diset. Bot Telegram akan menerima semua user.")
    if getattr(Config, "REDIS_EVENT_BUS_REQUIRED", False) and not getattr(Config, "REDIS_URL", ""):
        log.critical("REDIS_EVENT_BUS_REQUIRED=1 tapi REDIS_URL kosong.")
        sys.exit(1)
    if cors_origins_raw == "*" or "*" in cors_origins:
        msg = (
            "CORS_ORIGINS='*' terdeteksi. "
            "Gunakan allowlist origin eksplisit (contoh: https://dashboard.elara.com)."
        )
        if is_prod_env:
            log.critical(msg)
            sys.exit(1)
        log.warning(msg)
    if is_prod_env and web_proxy_required_prod and not web_proxy_enabled:
        log.critical(
            "Production membutuhkan reverse proxy guard. "
            "Set WEB_REVERSE_PROXY_ENABLED=1 dan jalankan app di belakang Nginx/Traefik."
        )
        sys.exit(1)
    if web_proxy_enabled and web_proxy_hops < 1:
        log.critical("WEB_REVERSE_PROXY_HOPS harus >= 1 saat WEB_REVERSE_PROXY_ENABLED=1.")
        sys.exit(1)
    if using_sqlite and not getattr(Config, "DB_ALLOW_SQLITE", True):
        log.critical(
            "DATABASE_URL memakai SQLite tapi DB_ALLOW_SQLITE=0. "
            "Gunakan PostgreSQL/MySQL atau set DB_ALLOW_SQLITE=1 hanya untuk dev."
        )
        sys.exit(1)
    if using_sqlite and is_prod_env and getattr(Config, "DB_REQUIRE_SERVER_FOR_PROD", True):
        log.critical(
            "Production environment terdeteksi dengan backend SQLite. "
            "Wajib gunakan PostgreSQL/MySQL (set DATABASE_URL ke server DB)."
        )
        sys.exit(1)
    if using_sqlite and is_live_mode and getattr(Config, "DB_REQUIRE_SERVER_FOR_LIVE_TRADING", True):
        log.critical(
            "TRADING_MODE=live terdeteksi dengan backend SQLite. "
            "Mode live wajib PostgreSQL/MySQL untuk menghindari lock contention."
        )
        sys.exit(1)
    if state_backend not in {"auto", "file", "redis", "memory"}:
        log.warning(f"STATE_BACKEND tidak dikenal ({state_backend}). Gunakan auto|file|redis|memory.")
    if state_requires_redis:
        if state_backend in {"file", "memory"}:
            log.critical(
                "STATE_BACKEND=file/memory tidak diizinkan untuk production/live. "
                "Gunakan STATE_BACKEND=redis (atau auto dengan Redis aktif)."
            )
            sys.exit(1)
        if not getattr(Config, "REDIS_URL", ""):
            log.critical(
                "State backend Redis diwajibkan untuk production/live, "
                "tetapi REDIS_URL kosong."
            )
            sys.exit(1)
        if not state_backend_fail_fast:
            log.critical(
                "STATE_BACKEND_FAIL_FAST wajib aktif (1) untuk production/live "
                "agar tidak fallback diam-diam ke memory/file."
            )
            sys.exit(1)
    if bool(getattr(Config, "STATE_ENCRYPTION_ENABLED", False)) and not str(
        getattr(Config, "STATE_ENCRYPTION_KEY", "") or ""
    ).strip():
        msg = "STATE_ENCRYPTION_ENABLED=1 tetapi STATE_ENCRYPTION_KEY kosong."
        if bool(getattr(Config, "STATE_ENCRYPTION_REQUIRED", False)) or is_prod_env:
            log.critical(msg)
            sys.exit(1)
        log.warning(msg)
    if using_sqlite:
        log.warning(
            "Database backend masih SQLite. Aman untuk dev/single-node, "
            "tapi untuk 24/7 high-concurrency disarankan PostgreSQL/MySQL."
        )
    required = set(getattr(Config, "REQUIRED_COMPONENTS", []) or [])
    if required:
        missing_required = [name for name in sorted(required) if name in _OPTIONAL_COMPONENT_IMPORT_ERRORS]
        if missing_required:
            details = ", ".join(
                f"{name}: {_OPTIONAL_COMPONENT_IMPORT_ERRORS.get(name, '')}"
                for name in missing_required
            )
            log.critical(f"Komponen required gagal di-import: {details}")
            sys.exit(1)
    if getattr(Config, "FAIL_FAST_STARTUP", False) and _OPTIONAL_COMPONENT_IMPORT_ERRORS:
        details = ", ".join(
            f"{name}: {err}" for name, err in sorted(_OPTIONAL_COMPONENT_IMPORT_ERRORS.items())
        )
        log.critical(f"FAIL_FAST_STARTUP aktif dan ada komponen gagal di-import: {details}")
        sys.exit(1)
    if getattr(Config, "REDIS_EVENT_BUS_REQUIRED", False):
        redis_client = get_redis()
        if not redis_client:
            log.critical("Redis event bus diwajibkan, tetapi koneksi Redis gagal.")
            sys.exit(1)
        redis_prefix = str(getattr(Config, "REDIS_PREFIX", "elara:") or "elara:")
        readiness = redis_readiness_probe(redis_client, redis_prefix=redis_prefix)
        if not bool(readiness.get("ok")):
            log.critical("Redis event bus readiness failed: %s", readiness)
            sys.exit(1)

    primary_missing_msg = ""
    if llm_primary == "openai":
        if llm_nvidia_endpoint and not (nvidia_key or openai_key):
            primary_missing_msg = (
                "LLM_BASE_URL mengarah ke NVIDIA tetapi NVIDIA_API_KEY/OPENAI_API_KEY kosong."
            )
        elif nvidia_key and not llm_base_url:
            primary_missing_msg = (
                "NVIDIA_API_KEY terdeteksi tapi LLM_BASE_URL kosong. "
                "Set LLM_BASE_URL=https://integrate.api.nvidia.com/v1."
            )
        elif not openai_key and not llm_base_url and not nvidia_key:
            primary_missing_msg = (
                "LLM_PRIMARY_PROVIDER=openai tetapi OPENAI_API_KEY/NVIDIA_API_KEY kosong "
                "dan LLM_BASE_URL tidak diset."
            )
    elif llm_primary == "groq" and not groq_key:
        primary_missing_msg = "LLM_PRIMARY_PROVIDER=groq tetapi GROQ_API_KEY kosong."
    elif llm_primary == "gemini" and not gemini_key:
        primary_missing_msg = "LLM_PRIMARY_PROVIDER=gemini tetapi GEMINI_API_KEY kosong."

    if primary_missing_msg:
        strict_llm_guard = bool(llm_require_primary_key or is_prod_env)
        dev_no_key_override = bool(llm_allow_no_key_in_dev and not is_prod_env and not llm_require_primary_key)
        if strict_llm_guard and not dev_no_key_override:
            log.critical(primary_missing_msg)
            sys.exit(1)
        log.warning(
            f"{primary_missing_msg} Menjalankan fallback tanpa LLM cloud "
            "(rule-based NLP / local fallback response)."
        )
    elif llm_nvidia_endpoint and openai_key and not nvidia_key:
        log.warning(
            "LLM_BASE_URL mengarah ke NVIDIA dan NVIDIA_API_KEY kosong. "
            "OPENAI_API_KEY dipakai sebagai fallback auth; disarankan isi NVIDIA_API_KEY eksplisit."
        )

    if is_live_mode:
        if ("openai" in nlu_providers) and not openai_key:
            log.warning("LLM_NLU_PROVIDERS mengandung openai tapi OPENAI_API_KEY kosong.")
        if ("groq" in nlu_providers) and not groq_key:
            log.warning("LLM_NLU_PROVIDERS mengandung groq tapi GROQ_API_KEY kosong.")
        if ("gemini" in nlu_providers) and not gemini_key:
            log.warning("LLM_NLU_PROVIDERS mengandung gemini tapi GEMINI_API_KEY kosong.")

    if getattr(Config, "LLM_AGENT_ORCHESTRATOR_ENABLED", False):
        allowlist = _parse_csv_set(getattr(Config, "LLM_AGENT_TOOL_ALLOWLIST", ""))
        denylist = _parse_csv_set(getattr(Config, "LLM_AGENT_TOOL_DENYLIST", ""))
        if not allowlist:
            msg = (
                "LLM agent orchestrator aktif tapi LLM_AGENT_TOOL_ALLOWLIST kosong. "
                "Set allowlist eksplisit untuk mencegah tool call liar."
            )
            if is_prod_env:
                log.critical(msg)
                sys.exit(1)
            log.warning(msg)
        if "self_deploy" not in denylist:
            log.warning("Disarankan menaruh 'self_deploy' di LLM_AGENT_TOOL_DENYLIST.")
        if "self_heal" not in denylist:
            log.warning("Disarankan menaruh 'self_heal' di LLM_AGENT_TOOL_DENYLIST.")


def _build_life_signal_callback():
    redis_client = get_redis()
    redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
    channel = str(
        getattr(Config, "TRADING_SIGNAL_CHANNEL", trade_signal_channel(redis_prefix))
        or trade_signal_channel(redis_prefix)
    ).strip()
    stream_enabled = bool(getattr(Config, "REDIS_STREAM_EVENT_BUS_ENABLED", False))
    stream_required = bool(getattr(Config, "REDIS_STREAM_EVENT_BUS_REQUIRED", False))
    stream_key = str(
        getattr(Config, "REDIS_STREAM_EVENT_KEY", trade_signal_stream_key(redis_prefix))
        or trade_signal_stream_key(redis_prefix)
    ).strip()
    stream_maxlen = max(100, int(getattr(Config, "REDIS_STREAM_EVENT_MAXLEN", 100_000) or 100_000))
    stream_bus = (
        RedisStreamsExternalBusAdapter(redis_client, stream_key=stream_key, maxlen=stream_maxlen)
        if redis_client and stream_enabled
        else None
    )
    command_path = str(getattr(Config, "TRADING_COMMAND_PATH", "") or "").strip()
    if not command_path:
        command_path = os.path.join(getattr(Config, "root_path", os.getcwd()), "runtime", "trading_command.json")
    block_cmd = str(getattr(Config, "LIFE_TRADING_BLOCK_COMMAND", "stop") or "stop").lower().strip()
    resume_cmd = str(getattr(Config, "LIFE_TRADING_RESUME_COMMAND", "resume") or "resume").lower().strip()
    file_fallback_enabled = bool(getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True))

    def _enqueue_file(payload: dict):
        if not file_fallback_enabled:
            return
        try:
            _write_json_atomic(command_path, payload)
        except Exception as e:
            log.error(f"FALLBACK_ALERT: Life signal fallback file write gagal ({command_path}): {e}")

    async def _publish_async(signal_event, file_payload: dict):
        command_cmd = str(file_payload.get("payload", {}).get("cmd") or file_payload.get("cmd") or "")
        command_user = str(
            file_payload.get("payload", {}).get("user_id") or file_payload.get("user_id") or "default"
        )
        if redis_client:
            durable_ready = not stream_enabled
            if stream_enabled and stream_bus:
                try:
                    durable = await asyncio.to_thread(
                        stream_bus.publish,
                        signal_event.event,
                        signal_event.to_dict(),
                        source="elara-core",
                    )
                    durable_ready = True
                    log.info(
                        f"Core->RedisStream trade_signal appended | stream={stream_key} id={durable.stream_id} cmd={command_cmd}"
                    )
                except Exception as e:
                    log.warning(f"Life signal durable stream write gagal ({stream_key}): {e}")
            if stream_required and not durable_ready:
                log.error("FALLBACK_ALERT: Required Redis Stream write failed; Pub/Sub publish blocked.")
            else:
                try:
                    await asyncio.to_thread(redis_client.publish, channel, signal_event.to_json())
                    log.info(
                        f"Core->Redis trade_signal sent | channel={channel} cmd={command_cmd} user={command_user}"
                    )
                    return
                except Exception as e:
                    log.warning(f"Life signal publish gagal ({channel}): {e}")
                    if durable_ready and stream_enabled:
                        log.info(f"Trade signal remains durable in Redis Stream | stream={stream_key} cmd={command_cmd}")
                        return

        if file_fallback_enabled:
            await asyncio.to_thread(_enqueue_file, file_payload)
            log.error(
                f"FALLBACK_ALERT: Core->File trade_signal fallback | path={command_path} cmd={command_cmd} user={command_user}"
            )
        else:
            log.error("FALLBACK_ALERT: Trade signal dropped: Redis unavailable and file fallback disabled.")

    def _publish(event: dict):
        allowed = bool(event.get("allowed", False))
        cmd = resume_cmd if allowed else block_cmd
        source = "life_manager"
        reason = str(event.get("reason") or "")
        user_id = str(event.get("user_id") or "default")
        signal_event = build_trade_signal_event(
            cmd=cmd,
            source=source,
            reason=reason,
            user_id=user_id,
        )
        file_payload = build_trade_command_message(
            cmd=cmd,
            source=source,
            reason=reason,
            user_id=user_id,
            priority=1,
        )
        async_runner.run_coroutine_fire_and_forget(_publish_async(signal_event, file_payload))

    return _publish


def _build_life_manager(storage):
    return LifeManager(
        storage,
        min_sleep_hours=float(getattr(Config, "LIFE_MIN_SLEEP_HOURS", 5.0)),
        max_stress=int(getattr(Config, "LIFE_MAX_STRESS", 7)),
        min_energy=int(getattr(Config, "LIFE_MIN_ENERGY", 0)),
        min_focus_minutes=int(getattr(Config, "LIFE_MIN_FOCUS_MINUTES", 120)),
        context_max_age_sec=int(getattr(Config, "LIFE_CONTEXT_MAX_AGE_SEC", 86400)),
        signal_callback=_build_life_signal_callback(),
    )


def _install_global_error_handlers():
    try:
        root_logger = logging.getLogger()
        try:
            from utils.json_logging import configure_root_logging_json
            configure_root_logging_json(bool(getattr(Config, "LOG_JSON", False)))
        except Exception as e:
            log.warning(f"JSON logging setup warning: {e}")
        attach_redaction_filter(root_logger)

        app_log_path = str(getattr(Config, "LOG_FILE_PATH", "") or "").strip()
        if app_log_path:
            os.makedirs(os.path.dirname(app_log_path) or ".", exist_ok=True)
            max_bytes = int(getattr(Config, "LOG_MAX_BYTES", 5 * 1024 * 1024))
            backup_count = int(getattr(Config, "LOG_BACKUP_COUNT", 5))
            existing = [
                h for h in root_logger.handlers
                if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(app_log_path)
            ]
            if not existing:
                app_handler = RotatingFileHandler(
                    app_log_path,
                    maxBytes=max(1024 * 1024, max_bytes),
                    backupCount=max(1, backup_count),
                    encoding="utf-8",
                )
                app_handler.setLevel(logging.INFO)
                app_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                    )
                )
                root_logger.addHandler(app_handler)

        log_path = str(getattr(Config, "CRITICAL_LOG_PATH", "") or "").strip()
        if log_path:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max(1024 * 1024, int(getattr(Config, "LOG_MAX_BYTES", 5 * 1024 * 1024))),
                backupCount=max(1, int(getattr(Config, "LOG_BACKUP_COUNT", 5))),
                encoding="utf-8",
            )
            file_handler.setLevel(logging.ERROR)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root_logger.addHandler(file_handler)
        try:
            from utils.json_logging import configure_root_logging_json
            configure_root_logging_json(bool(getattr(Config, "LOG_JSON", False)))
        except Exception:
            pass
    except Exception as e:
        log.warning(f"Gagal pasang CRITICAL_LOG_PATH handler: {e}")

    default_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return default_excepthook(exc_type, exc_value, exc_tb)
        log.critical("Uncaught exception in main thread.", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _excepthook


def _build_gemini_model():
    gemini_model = None
    if Config.GEMINI_API_KEY:
        if _looks_like_placeholder_secret(Config.GEMINI_API_KEY):
            log.warning("GEMINI_API_KEY terlihat placeholder, Gemini dinonaktifkan.")
            return None
        try:
            import google.generativeai as genai
            genai.configure(api_key=Config.GEMINI_API_KEY)
            model_name = getattr(Config, "GEMINI_MODEL", "gemini-1.5-flash-latest")
            gemini_model = genai.GenerativeModel(model_name)
            log.info(f"Gemini AI Connected ({model_name}).")
        except Exception as e:
            log.warning(f"Gemini AI Error: {e}")
    return gemini_model


def _build_llm_clients():
    """
    Build primary (OpenAI-compatible) and fallback (Groq) async clients.
    Supports local endpoint via `LLM_BASE_URL` (e.g. Ollama OpenAI-compatible API).
    """
    openai_client = None
    groq_client = None

    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    is_nvidia_endpoint = bool(base_url and "integrate.api.nvidia.com" in str(base_url).lower())
    openai_key = str(getattr(Config, "OPENAI_API_KEY", "") or "").strip()
    nvidia_key = str(getattr(Config, "NVIDIA_API_KEY", "") or "").strip()
    local_llm_key = str(os.getenv("LLM_LOCAL_API_KEY", "") or "").strip()
    key_source = "none"

    # OpenAI SDK expects non-empty API key; local endpoints usually ignore the value.
    if is_nvidia_endpoint:
        api_key = nvidia_key or openai_key or local_llm_key
    else:
        api_key = openai_key or nvidia_key or local_llm_key
    if nvidia_key and is_nvidia_endpoint:
        key_source = "nvidia"
    elif openai_key:
        key_source = "openai"
    elif nvidia_key:
        key_source = "nvidia"
    elif local_llm_key:
        key_source = "local"
    if base_url and not api_key:
        api_key = "local-llm"
        key_source = "placeholder"

    if not base_url and not api_key:
        log.warning("OPENAI_API_KEY kosong. Primary OpenAI client dinonaktifkan.")
    elif is_nvidia_endpoint and not nvidia_key and openai_key:
        log.warning(
            "LLM_BASE_URL mengarah ke NVIDIA tetapi NVIDIA_API_KEY kosong. "
            "Sementara memakai OPENAI_API_KEY sebagai fallback; disarankan isi NVIDIA_API_KEY."
        )
    elif nvidia_key and not base_url:
        log.warning(
            "NVIDIA_API_KEY terdeteksi tapi LLM_BASE_URL kosong. "
            "Set LLM_BASE_URL=https://integrate.api.nvidia.com/v1 untuk NVIDIA endpoint."
        )
    else:
        try:
            from openai import AsyncOpenAI

            openai_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            target_name = "Local LLM" if base_url else "OpenAI Cloud"
            log.info(
                f"Primary LLM Client initialized: {target_name} "
                f"({base_url or 'default'}, key_source={key_source})"
            )
        except ImportError:
            log.error("Library 'openai' not found. Install with: pip install openai")
        except Exception as e:
            log.error(f"Failed to init Primary Client: {e}")

    groq_key = str(getattr(Config, "GROQ_API_KEY", "") or "").strip()
    if groq_key:
        try:
            from groq import AsyncGroq

            groq_client = AsyncGroq(api_key=groq_key)
            log.info("Groq Client initialized (Secondary).")
        except ImportError:
            log.warning("Groq library missing. Install: pip install groq")
        except Exception as e:
            log.error(f"Groq init failed: {e}")
    else:
        log.debug("Groq API Key empty. Groq client disabled.")

    return openai_client, groq_client

def _load_device_config():
    device_config = {"devices": {}, "topics": {}}
    config_path = Config.DEVICE_CONFIG_PATH
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                device_config = json.load(f)
                log.info(f"Device config loaded from {config_path}")
        except Exception as e:
            log.error(f"Gagal baca devices.json: {e}")
    else:
        log.warning(f"{config_path} tidak ditemukan. Menggunakan config kosong.")
    return device_config


async def _init_db(db_manager):
    async def _run_alembic_upgrade(cfg_path: str):
        def _upgrade():
            from alembic.config import Config as AlembicConfig
            from alembic import command as alembic_command

            alembic_cfg = AlembicConfig(cfg_path)
            script_location = str(alembic_cfg.get_main_option("script_location") or "").strip()
            if script_location and not os.path.isabs(script_location):
                cfg_dir = os.path.dirname(os.path.abspath(cfg_path))
                resolved_script_location = os.path.abspath(os.path.join(cfg_dir, script_location))
                alembic_cfg.set_main_option("script_location", resolved_script_location)
            alembic_cfg.set_main_option("sqlalchemy.url", str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "")))
            alembic_command.upgrade(alembic_cfg, "head")

        await asyncio.to_thread(_upgrade)

    try:
        if getattr(Config, "DB_USE_ALEMBIC", False) and getattr(Config, "DB_ALEMBIC_AUTO_UPGRADE", False):
            cfg_path_raw = str(getattr(Config, "DB_ALEMBIC_CONFIG", "") or "").strip()
            cfg_path = ""
            if cfg_path_raw:
                candidates = [cfg_path_raw]
                if not os.path.isabs(cfg_path_raw):
                    root_path = str(getattr(Config, "root_path", os.getcwd()) or os.getcwd())
                    candidates.append(os.path.join(root_path, cfg_path_raw))
                for candidate in candidates:
                    if candidate and os.path.exists(candidate):
                        cfg_path = candidate
                        break
            if cfg_path and os.path.exists(cfg_path):
                try:
                    await _run_alembic_upgrade(cfg_path)
                    log.info("Alembic upgrade head completed.")
                except Exception as e:
                    log.error(f"Alembic upgrade failed: {e}")
            else:
                log.warning("DB_ALEMBIC_AUTO_UPGRADE aktif tapi alembic config tidak ditemukan.")
        if getattr(Config, "DB_AUTO_CREATE_TABLES", True):
            await asyncio.to_thread(db_manager.ensure_tables)
        await asyncio.to_thread(db_manager.ensure_bootstrap_user)
    except Exception as e:
        log.warning(f"DB bootstrap skipped: {e}")


def _build_mqtt_service(device_config, state_manager, db_manager):
    if not subsystem_runtime_enabled("mqtt"):
        log.info("MQTTService skipped (runtime policy disabled).")
        return DummyMQTT()
    requested_components = set(getattr(Config, "REQUIRED_COMPONENTS", []) or [])
    if "mqtt" not in requested_components:
        log.info("MQTTService skipped (mqtt not requested in REQUIRED_COMPONENTS).")
        return DummyMQTT()
    mqtt_cls = _load_optional_attr(
        "runtime.interfaces.mqtt_service",
        "MQTTService",
        "mqtt",
        log_level="error",
    )
    if mqtt_cls:
        return mqtt_cls(None, device_config, state_manager=state_manager, db_manager=db_manager)
    log.warning("MQTTService unavailable. Device control will be no-op.")
    return DummyMQTT()


def _register_skills(
    skill_manager,
    nlp_engine,
    state_manager,
    db_manager,
    life_manager,
    mqtt_service,
    device_config,
    gemini_model,
    openai_client,
    groq_client,
    self_heal_service=None,
):
    security_skill = None
    log.info("Registering Skills...")
    def _register_optional_skill(component_name, module_path, class_name, builder):
        skill_cls = _load_optional_attr(module_path, class_name, component_name, log_level="warning")
        if not skill_cls:
            return None
        try:
            skill = builder(skill_cls)
            skill_manager.register(skill)
            return skill
        except Exception as e:
            log.error(f"Skill registration failed [{component_name}]: {e}", exc_info=True)
            return None

    _register_optional_skill(
        "skill.simple_chat",
        "skills.SimpleChatSkill",
        "SimpleChatSkill",
        lambda cls: cls(),
    )
    _register_optional_skill(
        "skill.life",
        "skills.LifeSkill",
        "LifeSkill",
        lambda cls: cls(life_manager),
    )
    _register_optional_skill(
        "skill.goal_engine",
        "skills.GoalEngineSkill",
        "GoalEngineSkill",
        lambda cls: cls(state_manager, getattr(Config, "root_path", None), skill_manager=skill_manager),
    )
    security_skill = _register_optional_skill(
        "skill.security",
        "skills.SecuritySkill",
        "SecuritySkill",
        lambda cls: cls(state_manager, db_manager),
    )
    _register_optional_skill(
        "skill.productivity",
        "skills.DeepFokusSkill",
        "ProductivityAnalyticsSkill",
        lambda cls: cls(db_manager),
    )
    _register_optional_skill(
        "skill.weather",
        "skills.WeatherSkill",
        "WeatherSkill",
        lambda cls: cls(),
    )
    _register_optional_skill(
        "skill.calendar",
        "skills.CalendarSkill",
        "CalendarSkill",
        lambda cls: cls(),
    )
    _register_optional_skill(
        "skill.spotify",
        "skills.SpotifySkill",
        "SpotifySkill",
        lambda cls: cls(life_manager),
    )
    if subsystem_runtime_enabled("device_control") and "mqtt" in set(getattr(Config, "REQUIRED_COMPONENTS", []) or []):
        _register_optional_skill(
            "skill.device_control",
            "skills.DeviceControlSkill",
            "DeviceControlSkill",
            lambda cls: cls(state_manager, mqtt_service, device_config),
        )
    else:
        log.info("DeviceControlSkill skipped (runtime policy disabled or mqtt not requested).")
    if getattr(Config, "ENABLE_TRADING_SKILL", True):
        _register_optional_skill(
            "skill.trader_gateway",
            "skills.TraderGatewaySkill",
            "TraderGatewaySkill",
            lambda cls: cls(life_manager=life_manager, db_manager=db_manager),
        )
        _register_optional_skill(
            "skill.crypto_screener",
            "skills.CryptoScreenerSkill",
            "CryptoScreenerSkill",
            lambda cls: cls(),
        )
    _register_optional_skill(
        "skill.llm",
        "skills.LLMSkill",
        "LLMSkill",
        lambda cls: cls(
            state_manager,
            gemini_model=gemini_model,
            openai_client=openai_client,
            groq_client=groq_client,
            primary_provider=getattr(Config, "LLM_PRIMARY_PROVIDER", "groq"),
            skill_manager=skill_manager,
            nlp_engine=nlp_engine,
            db_manager=db_manager,
            markdown_registry=getattr(skill_manager, "markdown_registry", None),
        ),
    )
    _register_optional_skill(
        "skill.stock_screener",
        "skills.StockScreenerSkill",
        "StockScreenerSkill",
        lambda cls: cls(),
    )
    _register_optional_skill(
        "skill.home_adguard",
        "skills.HomeAdguardSkill",
        "HomeAdguardSkill",
        lambda cls: cls(),
    )

    if self_heal_service:
        _register_optional_skill(
            "skill.self_ops",
            "skills.SelfOpsSkill",
            "SelfOpsSkill",
            lambda cls: cls(self_heal_service),
        )

    if security_skill:
        try:
            security_skill.start_services()
            setattr(skill_manager, "_security_service", security_skill)
        except Exception as e:
            log.error(f"Security service bootstrap failed: {e}", exc_info=True)

    _register_optional_skill(
        "skill.system_health",
        "skills.SystemHealthSkill",
        "SystemHealthSkill",
        lambda cls: cls(),
    )
    if subsystem_runtime_enabled("knowledge"):
        _register_optional_skill(
            "skill.knowledge",
            "skills.KnowledgeSkill",
            "KnowledgeSkill",
            lambda cls: cls(nlp_engine),
        )
    _register_optional_skill(
        "skill.internet",
        "skills.InternetSkill",
        "InternetSkill",
        lambda cls: cls(nlp_engine),
    )

    exec_map = {
        "tasks": skill_manager.intent_map.get("get_productivity_report"),
        "trading": skill_manager.intent_map.get("trading_status"),
        "weather": skill_manager.intent_map.get("get_weather"),
        "calendar": skill_manager.intent_map.get("get_schedule"),
        "system": skill_manager.intent_map.get("check_system_status")
    }
    exec_map = {k: v for k, v in exec_map.items() if v is not None}
    _register_optional_skill(
        "skill.executive_briefing",
        "skills.ExecutiveBriefingSkill",
        "ExecutiveBriefingSkill",
        lambda cls: cls(exec_map, UserProfileService(db_manager)),
    )


def _start_interfaces(elara, state_manager, db_manager, life_manager, mqtt_service, self_heal_service=None):
    app = None
    socketio = None
    services = []

    class _WebLifecycleService:
        def __init__(self, flask_app):
            self.app = flask_app

        def stop(self):
            hooks = getattr(self.app, "extensions", {}).get("elara_run_shutdown_hooks")
            if callable(hooks):
                hooks()

    required_components = set(effective_required_components(getattr(Config, "REQUIRED_COMPONENTS", []) or []))
    create_web_app = _load_optional_attr(
        "runtime.interfaces.web_factory",
        "create_web_app",
        "web",
        log_level="error",
    )
    telegram_service_cls = _load_optional_attr(
        "runtime.interfaces.telegram_service",
        "TelegramService",
        "telegram",
        log_level="warning",
    )
    enable_stream = subsystem_runtime_enabled("stream")
    stream_service_cls = None
    if enable_stream:
        stream_service_cls = _load_optional_attr(
            "runtime.interfaces.stream_service",
            "StreamService",
            "stream",
            log_level="warning",
        )
    scheduler_service_cls = _load_optional_attr(
        "runtime.interfaces.scheduler_service",
        "SchedulerService",
        "scheduler",
        log_level="warning",
    )
    backup_service_cls = _load_optional_attr(
        "runtime.interfaces.backup_service",
        "BackupService",
        "backup",
        log_level="warning",
    )
    health_service_cls = _load_optional_attr(
        "runtime.interfaces.health_service",
        "HealthService",
        "health",
        log_level="warning",
    )
    enable_presence = subsystem_runtime_enabled("presence")
    presence_service_cls = None
    if enable_presence:
        presence_service_cls = _load_optional_attr(
            "runtime.interfaces.presence_service",
            "PresenceService",
            "presence",
            log_level="warning",
        )
    enable_voice = subsystem_runtime_enabled("voice")
    voice_service_cls = None
    if enable_voice:
        voice_service_cls = _load_optional_attr(
            "runtime.interfaces.voice_service",
            "VoiceService",
            "voice",
            log_level="warning",
        )

    def _is_critical(name: str) -> bool:
        return name in required_components

    def _start_named_service(name, factory, critical=False):
        try:
            svc = factory()
            if not svc:
                return None
            start_fn = getattr(svc, "start", None)
            if callable(start_fn):
                start_fn()
            services.append((name, svc))
            return svc
        except Exception as e:
            log.error(f"{name} service gagal start: {e}", exc_info=True)
            if critical or getattr(Config, "FAIL_FAST_STARTUP", False):
                raise
            return None

    if create_web_app:
        try:
            app, socketio = create_web_app(
                elara,
                db_manager,
                mqtt_service=mqtt_service,
                state_manager=state_manager,
                self_heal_service=self_heal_service,
            )
            app.app_context().push()
            db_manager.app = app
            db_manager.ensure_bootstrap_user()
            services.append(("web", _WebLifecycleService(app)))
        except Exception as e:
            app = None
            socketio = None
            log.error(f"Web app startup gagal, fallback headless mode: {e}", exc_info=True)
            if _is_critical("web") or getattr(Config, "FAIL_FAST_STARTUP", False):
                raise
    else:
        log.warning("Web app disabled; running headless.")

    if self_heal_service:
        _start_named_service("self_heal", lambda: self_heal_service, critical=_is_critical("self_heal"))

    security_service = getattr(getattr(elara, "skill_manager", None), "_security_service", None)
    if security_service and hasattr(security_service, "stop"):
        services.append(("security", security_service))

    if mqtt_service and hasattr(mqtt_service, "start"):
        mqtt_service.socketio = socketio
        _start_named_service("mqtt", lambda: mqtt_service, critical=_is_critical("mqtt"))

    if telegram_service_cls:
        _start_named_service("telegram", lambda: telegram_service_cls(elara), critical=_is_critical("telegram"))

    if enable_voice:
        if voice_service_cls:
            _start_named_service("voice", lambda: voice_service_cls(elara), critical=_is_critical("voice"))
        else:
            log.warning("VoiceService enabled but dependency/module missing.")
    else:
        log.info("VoiceService skipped (runtime policy disabled).")

    if enable_stream:
        if stream_service_cls:
            _start_named_service("stream", lambda: stream_service_cls(), critical=_is_critical("stream"))
        else:
            log.warning("StreamService enabled but dependency/module missing.")
    else:
        log.info("StreamService skipped (runtime policy disabled).")

    backup_service = backup_service_cls() if backup_service_cls else None
    if scheduler_service_cls:
        _start_named_service(
            "scheduler",
            lambda: scheduler_service_cls(elara, backup_service=backup_service),
            critical=_is_critical("scheduler"),
        )

    if enable_presence:
        if presence_service_cls:
            _start_named_service(
                "presence",
                lambda: presence_service_cls(elara, state_manager),
                critical=_is_critical("presence"),
            )
        else:
            log.warning("PresenceService enabled but dependency/module missing.")
    else:
        log.info("PresenceService skipped (runtime policy disabled).")

    if health_service_cls and life_manager and getattr(Config, "HEALTH_POLL_PATH", ""):
        _start_named_service(
            "health",
            lambda: health_service_cls(life_manager),
            critical=_is_critical("health"),
        )

    return app, socketio, services


def _start_watchdog(services):
    if not services:
        return

    async def _watchdog_loop():
        last_restart = {}
        # Beri waktu startup awal supaya service yang butuh handshake tidak dianggap unhealthy terlalu cepat.
        await asyncio.sleep(20)
        while True:
            for name, svc in services:
                try:
                    if hasattr(svc, "is_healthy") and not svc.is_healthy():
                        now = time.time()
                        prev = float(last_restart.get(name, 0))
                        if (now - prev) < 60:
                            continue
                        log.warning(f"{name} service unhealthy. Restarting...")
                        try:
                            if hasattr(svc, "stop"):
                                svc.stop()
                        except Exception as e:
                            log.debug(f"{name} stop before restart warning: {e}")
                        svc.start()
                        last_restart[name] = now
                except Exception as e:
                    log.error(f"Watchdog error for {name}: {e}")
            await asyncio.sleep(60)

    async_runner.run_coroutine_fire_and_forget(_watchdog_loop())


def _heartbeat_payload() -> dict:
    payload = build_core_heartbeat_event(pid=os.getpid())
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    return payload


def _start_core_heartbeat_publisher():
    interval = max(1, int(getattr(Config, "CORE_HEARTBEAT_INTERVAL_SEC", 5)))
    redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
    file_fallback_enabled = bool(getattr(Config, "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", True))
    heartbeat_path = _resolve_core_heartbeat_path(file_fallback_enabled)
    heartbeat_key = _resolve_core_heartbeat_key(redis_prefix)
    redis_client = get_redis()

    def _write_heartbeat_file(payload: dict):
        _write_json_atomic(heartbeat_path, payload)

    async def _write_heartbeat():
        payload = _heartbeat_payload()

        if heartbeat_path and file_fallback_enabled:
            try:
                await asyncio.to_thread(_write_heartbeat_file, payload)
            except Exception as e:
                log.warning(f"Core heartbeat file write failed: {e}")

        if redis_client and heartbeat_key:
            try:
                await asyncio.to_thread(redis_client.set, heartbeat_key, json.dumps(payload))
                log.debug(f"Core->Redis heartbeat sent | key={heartbeat_key} ts={payload.get('ts')}")
            except Exception as e:
                log.warning(f"Core heartbeat Redis write failed: {e}")

    async def _heartbeat_loop():
        # Write immediately so trader watchdog can see heartbeat during startup.
        await _write_heartbeat()
        while True:
            await asyncio.sleep(interval)
            await _write_heartbeat()

    async_runner.run_coroutine_fire_and_forget(_heartbeat_loop())


def _shutdown_services(services):
    for name, svc in reversed(services or []):
        stopped = False
        for method in ("stop", "shutdown", "close"):
            if hasattr(svc, method):
                try:
                    getattr(svc, method)()
                    stopped = True
                    break
                except Exception as e:
                    log.warning(f"Service shutdown error [{name}.{method}]: {e}")
        if not stopped:
            log.debug(f"No shutdown method available for service: {name}")


def _shutdown_runtime(state_manager, services, db_manager=None):
    _shutdown_services(services)
    if db_manager:
        try:
            db_manager.shutdown()
        except Exception as e:
            log.warning(f"DatabaseManager shutdown error: {e}")
    try:
        state_manager.shutdown()
    except Exception as e:
        log.warning(f"StateManager shutdown error: {e}")
    try:
        async_runner.shutdown()
    except Exception as e:
        log.warning(f"AsyncRunner shutdown error: {e}")


def _close_llm_client(client, name):
    if not client:
        return
    close_fn = getattr(client, "close", None)
    if not close_fn:
        return
    try:
        coro = close_fn()
        if hasattr(coro, "__await__"):
            async_runner.run_coroutine(coro, timeout=5)
        else:
            close_fn()
    except Exception as e:
        log.warning(f"{name} client close warning: {e}")


def _run_forever(
    app,
    socketio,
    state_manager,
    services,
    openai_client=None,
    groq_client=None,
    db_manager=None,
):
    port = getattr(Config, 'PORT', 5000)
    stop_requested = {"value": False}

    def _handle_signal(sig, _frame):
        stop_requested["value"] = True
        log.warning(f"Received signal {sig}. Starting graceful shutdown.")
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass

    try:
        if app and socketio:
            import socket
            try:
                local_ip = socket.gethostbyname(socket.gethostname())
            except Exception as e:
                log.warning(f"Failed resolving local host IP, fallback 0.0.0.0: {e}")
                local_ip = "0.0.0.0"
            log.info(f"SYSTEM ONLINE @ http://{local_ip}:{port} (bind 0.0.0.0)")
            socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True, use_reloader=False)
        else:
            log.info("SYSTEM ONLINE (headless mode).")
            while True:
                if stop_requested["value"]:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down Elara...")
    except Exception as e:
        log.error(f"Fatal runtime error: {e}", exc_info=True)
    finally:
        _close_llm_client(openai_client, "OpenAI")
        _close_llm_client(groq_client, "Groq")
        _shutdown_runtime(state_manager, services, db_manager=db_manager)


class CoreBootstrap:
    def run(self):
        log.info("SYSTEM BOOT SEQUENCE INITIATED...")
        _install_global_error_handlers()
        setup_telegram_logging()

        _validate_runtime_config()

        state_manager = StateManager(db_path=getattr(Config, "STATE_DB_PATH", None))
        db_manager = DatabaseManager()
        life_manager = _build_life_manager(state_manager)
        self_ops_manager = SelfOpsManager(root_path=getattr(Config, "root_path", os.getcwd()))
        self_heal_cls = _load_optional_attr(
            "runtime.interfaces.self_heal_service",
            "SelfHealService",
            "self_heal",
            log_level="warning",
        )
        self_heal_service = self_heal_cls(self_ops_manager) if self_heal_cls else None
        gemini_model = _build_gemini_model()
        openai_client, groq_client = _build_llm_clients()

        device_config = _load_device_config()
        async_runner.run_coroutine(_init_db(db_manager), timeout=120)
        mqtt_service = _build_mqtt_service(device_config, state_manager, db_manager)
        providers_raw = str(getattr(Config, "LLM_NLU_PROVIDERS", "") or "").strip()
        providers = _parse_csv_list(providers_raw)
        if not providers:
            primary = str(getattr(Config, "LLM_PRIMARY_PROVIDER", "") or "").strip().lower()
            if primary:
                providers = [primary]
        nlp_engine = ElaraNLP(
            state_manager,
            providers=providers,
            gemini_model=gemini_model,
            openai_client=openai_client,
            groq_client=groq_client,
            tool_cache_ttl_sec=int(getattr(Config, "NLP_TOOL_CACHE_TTL_SEC", 300) or 300),
        )
        skill_manager = SkillManager()
        _register_skills(
            skill_manager,
            nlp_engine,
            state_manager,
            db_manager,
            life_manager,
            mqtt_service,
            device_config,
            gemini_model,
            openai_client,
            groq_client,
            self_heal_service=self_heal_service,
        )
        elara = ElaraSkill(nlp_engine, skill_manager, state_manager)
        log.info("Starting Interfaces...")
        app, socketio, services = _start_interfaces(
            elara,
            state_manager,
            db_manager,
            life_manager,
            mqtt_service,
            self_heal_service=self_heal_service,
        )
        _start_core_heartbeat_publisher()
        _start_watchdog(services)
        _run_forever(
            app,
            socketio,
            state_manager,
            services,
            openai_client=openai_client,
            groq_client=groq_client,
            db_manager=db_manager,
        )


def main():
    CoreBootstrap().run()

if __name__ == "__main__":
    main()


