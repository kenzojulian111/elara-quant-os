"""
Standalone trading daemon.
Runs TradingBot in its own process to isolate from main Elara runtime.
"""
import os
import uuid
import asyncio
import signal
import sys
import logging
import json
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from core.events import (
    build_trade_command_message,
    core_heartbeat_key,
    parse_trade_command_message,
    parse_trade_signal_event,
    trade_command_queue_key,
    trade_signal_channel,
    trade_signal_stream_key,
    trader_status_key,
)
from utils.redis_helper import get_redis, redis_readiness_probe, redis_safe_call
from skills.advanced.trading.config import TradingConfig
from skills.advanced.trading.bot import TradingBot
from config.settings import Config
from skills.advanced.trading.ipc_server import SimpleIPCServer
from utils.json_logging import configure_root_logging_json
from utils.log_redaction import attach_redaction_filter
from utils.opentelemetry_tracing import install_background_tracing
from trading_core.coordination.external_bus import RedisStreamsExternalBusAdapter


log = logging.getLogger("elara.trading.daemon")
_daemon_log_path = str(getattr(TradingConfig, "DAEMON_LOG_FILE", "") or "").strip()
_handlers = [logging.StreamHandler()]
if _daemon_log_path:
    try:
        os.makedirs(os.path.dirname(_daemon_log_path) or ".", exist_ok=True)
        _handlers.append(
            RotatingFileHandler(
                _daemon_log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
        )
    except Exception:
        pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_handlers,
)
configure_root_logging_json(bool(getattr(Config, "LOG_JSON", False)))
if bool(getattr(Config, "LOG_REDACTION_ENABLED", True)):
    attach_redaction_filter(logging.getLogger())
    attach_redaction_filter(log)


def _write_json_atomic(path: str, payload: dict):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    last_error = None
    for attempt in range(3):
        tmp_path = (
            f"{path}.tmp.{os.getpid()}."
            f"{int(time.time() * 1000)}.{attempt}"
        )
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, path)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    if last_error:
        raise last_error


def _resolve_command_queue_dir(command_path: str) -> str:
    if not command_path:
        return ""
    if command_path.endswith(".d"):
        return command_path
    if os.path.isdir(command_path):
        return command_path
    return f"{command_path}.d"


def _write_command_queue_file(command_path: str, payload: dict) -> bool:
    queue_dir = _resolve_command_queue_dir(command_path)
    if not queue_dir:
        return False
    try:
        os.makedirs(queue_dir, exist_ok=True)
        filename = f"{int(time.time() * 1000)}_{os.getpid()}_{uuid.uuid4().hex}.json"
        _write_json_atomic(os.path.join(queue_dir, filename), payload)
        return True
    except Exception as e:
        log.warning(f"Failed enqueue command to queue dir '{queue_dir}': {e}")
        return False


def _write_status_payload(status_path, redis_client, redis_prefix, payload: dict):
    file_fallback_enabled = bool(getattr(Config, "TRADING_STATUS_FILE_FALLBACK_ENABLED", True))
    if file_fallback_enabled:
        try:
            _write_json_atomic(status_path, payload)
        except Exception as e:
            log.warning(f"Failed writing setup failure status file: {e}")

    if redis_client:
        redis_key = str(
            getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
            or trader_status_key(redis_prefix)
        ).strip()
        ok = redis_safe_call(
            redis_client,
            "set",
            redis_key,
            json.dumps(payload),
            default=False,
            purpose="trader_status_write",
        )
        if not ok:
            log.warning("Failed writing setup failure status to Redis.")


def _write_setup_failure_status(status_path, redis_client, redis_prefix, error_text: str):
    max_dd = float(getattr(TradingConfig, "MAX_DRAWDOWN_PCT", 10.0))
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "is_running": False,
        "setup_error": (error_text or "unknown setup error")[:500],
        "setup_failed_at": time.time(),
        "life_blocked": False,
        "daily_risk_blocked": False,
        "daily_drawdown_pct": 0.0,
        "global_risk_blocked": False,
        "global_drawdown_pct": 0.0,
        "max_drawdown_limit_pct": max_dd,
        "lite_mode": False,
        "lite_reason": "",
        "ram_usage_pct": 0.0,
        "open_positions": 0,
        "equity": 0.0,
        "cash": 0.0,
    }
    _write_status_payload(status_path, redis_client, redis_prefix, payload)


def _write_startup_status(status_path, redis_client, redis_prefix, stage: str = "booting"):
    max_dd = float(getattr(TradingConfig, "MAX_DRAWDOWN_PCT", 10.0))
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "is_running": False,
        "setup_in_progress": True,
        "setup_stage": str(stage or "booting")[:64],
        "life_blocked": False,
        "daily_risk_blocked": False,
        "daily_drawdown_pct": 0.0,
        "global_risk_blocked": False,
        "global_drawdown_pct": 0.0,
        "max_drawdown_limit_pct": max_dd,
        "lite_mode": False,
        "lite_reason": "",
        "ram_usage_pct": 0.0,
        "open_positions": 0,
        "equity": 0.0,
        "cash": 0.0,
    }
    _write_status_payload(status_path, redis_client, redis_prefix, payload)


def _maybe_run_alembic_upgrade():
    if not bool(getattr(Config, "DB_USE_ALEMBIC", False)):
        return
    if not bool(getattr(Config, "DB_ALEMBIC_AUTO_UPGRADE", False)):
        return

    cfg_path = str(getattr(Config, "DB_ALEMBIC_CONFIG", "") or "").strip()
    if not cfg_path or not os.path.exists(cfg_path):
        log.warning("DB_ALEMBIC_AUTO_UPGRADE aktif tapi alembic config tidak ditemukan.")
        return

    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command

        alembic_cfg = AlembicConfig(cfg_path)
        alembic_cfg.set_main_option("sqlalchemy.url", str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "")))
        alembic_command.upgrade(alembic_cfg, "head")
        log.info("Alembic upgrade head completed for trader daemon.")
    except Exception as e:
        log.error(f"Alembic upgrade failed: {e}")


def _collect_trading_config_errors(cfg: TradingConfig) -> list:
    errors = []
    try:
        errors.extend(cfg.validate_runtime_schema())
    except Exception as e:
        errors.append(f"Trading config schema validation crashed: {e}")
    try:
        errors.extend(cfg.validate_exchange_ip_whitelist())
    except Exception as e:
        errors.append(f"Exchange IP whitelist validation crashed: {e}")
    return [str(e) for e in errors if str(e).strip()]


def _enqueue_command(
    command_path,
    redis_client,
    redis_prefix,
    cmd: str,
    source: str = "system",
    reason: str = "",
    command_id: str = "",
) -> bool:
    payload = build_trade_command_message(
        cmd=str(cmd or "").lower().strip(),
        source=source,
        reason=reason,
        priority=1,
        command_id=command_id,
        execution_id=command_id,
    )
    if not payload.get("payload", {}).get("cmd"):
        return False

    if redis_client:
        key = str(
            getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
            or trade_command_queue_key(redis_prefix)
        ).strip()
        ok = redis_safe_call(
            redis_client,
            "lpush",
            key,
            json.dumps(payload),
            default=False,
            purpose="trader_command_enqueue",
        )
        if ok:
            return True
        log.warning("Failed enqueue command to Redis.")

    if not bool(getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True)):
        return False

    if command_path:
        if _write_command_queue_file(command_path, payload):
            return True
    return False


async def _trade_signal_stream_bridge(stop_event, redis_client, redis_prefix, command_path):
    stream_key = str(
        getattr(Config, "REDIS_STREAM_EVENT_KEY", trade_signal_stream_key(redis_prefix))
        or trade_signal_stream_key(redis_prefix)
    ).strip()
    stream_maxlen = max(100, int(getattr(Config, "REDIS_STREAM_EVENT_MAXLEN", 100_000) or 100_000))
    group = str(getattr(Config, "REDIS_STREAM_TRADER_GROUP", "elara-trader") or "elara-trader").strip()
    consumer = str(getattr(Config, "REDIS_STREAM_TRADER_CONSUMER", "") or "").strip() or f"trader-{os.getpid()}"
    claim_idle_ms = max(1000, int(getattr(Config, "REDIS_STREAM_CLAIM_IDLE_MS", 30_000) or 30_000))
    backoff = 0.5
    max_backoff = 10.0
    while not stop_event.is_set():
        client = redis_client or get_redis()
        if not client:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(max_backoff, backoff * 2.0)
            continue
        try:
            bus = RedisStreamsExternalBusAdapter(client, stream_key=stream_key, maxlen=stream_maxlen)
            bus.ensure_group(group)
            log.info(f"Trade signal stream bridge consuming: stream={stream_key} group={group} consumer={consumer}")
            backoff = 0.5
            while not stop_event.is_set():
                messages = await asyncio.to_thread(
                    bus.claim_stale,
                    group=group,
                    consumer=consumer,
                    min_idle_ms=claim_idle_ms,
                    count=100,
                )
                if not messages:
                    messages = await asyncio.to_thread(
                    bus.consume,
                    group=group,
                    consumer=consumer,
                    count=100,
                    block_ms=0,
                    include_pending=True,
                    )
                if not messages:
                    messages = await asyncio.to_thread(
                        bus.consume,
                        group=group,
                        consumer=consumer,
                        count=100,
                        block_ms=1000,
                    )
                for message in messages:
                    parsed = parse_trade_signal_event(message.payload)
                    if not parsed:
                        await asyncio.to_thread(bus.acknowledge, group=group, stream_ids=(message.stream_id,))
                        log.warning(f"Invalid durable trade signal acknowledged: stream_id={message.stream_id}")
                        continue
                    cmd = str(parsed.get("cmd") or "").lower().strip()
                    source = str(parsed.get("source") or "redis_stream")
                    reason = str(parsed.get("reason") or "")
                    ok = _enqueue_command(
                        command_path=command_path,
                        redis_client=client,
                        redis_prefix=redis_prefix,
                        cmd=cmd,
                        source=source,
                        reason=reason,
                        command_id=message.event_id,
                    )
                    if ok:
                        await asyncio.to_thread(bus.acknowledge, group=group, stream_ids=(message.stream_id,))
                        log.info(
                            f"Trader<-RedisStream trade_signal received | stream={stream_key} id={message.stream_id} cmd={cmd}"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"Trade signal stream bridge stopped due to error: {e}")
        redis_client = None
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(max_backoff, backoff * 2.0)


async def _trade_signal_bridge(stop_event, redis_client, redis_prefix, command_path):
    if bool(getattr(Config, "REDIS_STREAM_EVENT_BUS_ENABLED", False)):
        await _trade_signal_stream_bridge(stop_event, redis_client, redis_prefix, command_path)
        return
    channel = str(
        getattr(Config, "TRADING_SIGNAL_CHANNEL", trade_signal_channel(redis_prefix))
        or trade_signal_channel(redis_prefix)
    ).strip()
    backoff = 0.5
    max_backoff = 10.0

    while not stop_event.is_set():
        client = redis_client or get_redis()
        if not client:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(max_backoff, backoff * 2.0)
            continue

        pubsub = None
        try:
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await asyncio.to_thread(pubsub.subscribe, channel)
            log.info(f"Trade signal bridge subscribed: {channel}")
            backoff = 0.5

            while not stop_event.is_set():
                message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                parsed = parse_trade_signal_event(message.get("data"))
                if not parsed:
                    continue
                cmd = str(parsed.get("cmd") or "").lower().strip()
                source = str(parsed.get("source") or "pubsub")
                reason = str(parsed.get("reason") or "")
                ok = _enqueue_command(
                    command_path=command_path,
                    redis_client=client,
                    redis_prefix=redis_prefix,
                    cmd=cmd,
                    source=source,
                    reason=reason,
                )
                if ok:
                    log.info(
                        f"Trader<-Redis trade_signal received | channel={channel} cmd={cmd} source={source}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"Trade signal bridge stopped due to error: {e}")
        finally:
            if pubsub:
                try:
                    await asyncio.to_thread(pubsub.close)
                except Exception:
                    pass
        redis_client = None
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(max_backoff, backoff * 2.0)


def _pop_commands(command_path, redis_client, redis_prefix):
    cmds = []

    local_redis = redis_client or get_redis()
    if local_redis:
        try:
            key = str(
                getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
                or trade_command_queue_key(redis_prefix)
            ).strip()
            max_batch = max(1, int(getattr(Config, "TRADING_COMMAND_POP_MAX_BATCH", 1000) or 1000))
            parse_failures = 0
            for _ in range(max_batch):
                raw = redis_safe_call(
                    local_redis,
                    "rpop",
                    key,
                    default=None,
                    purpose="trade_command_pop",
                )
                if raw is None:
                    break
                parsed = parse_trade_command_message(raw)
                if parsed:
                    cmds.append(parsed)
                else:
                    parse_failures += 1
            if parse_failures > 0:
                log.warning(f"Dropped {parse_failures} malformed trade command message(s) from Redis queue.")
        except Exception:
            pass

    if bool(getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True)) and command_path:
        queue_dir = _resolve_command_queue_dir(command_path)
        if queue_dir and os.path.isdir(queue_dir):
            try:
                max_batch = max(1, int(getattr(Config, "TRADING_COMMAND_POP_MAX_BATCH", 1000) or 1000))
            except Exception:
                max_batch = 1000
            try:
                entries = [name for name in os.listdir(queue_dir) if name.endswith(".json")]
                entries.sort()
                for name in entries[:max_batch]:
                    path = os.path.join(queue_dir, name)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        os.remove(path)
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            parsed = parse_trade_command_message(item)
                            if parsed:
                                cmds.append(parsed)
                    except Exception as e:
                        log.warning(f"Failed reading queued command '{path}': {e}")
            except Exception as e:
                log.warning(f"Failed listing queued commands in '{queue_dir}': {e}")

        if len(cmds) < 1 and os.path.exists(command_path) and not os.path.isdir(command_path):
            try:
                with open(command_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(command_path)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    parsed = parse_trade_command_message(item)
                    if parsed:
                        cmds.append(parsed)
            except Exception:
                cmds = []

    normalized = []
    for item in cmds:
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("cmd") or "").lower().strip()
        if not cmd:
            continue
        command = dict(item)
        command["cmd"] = cmd
        normalized.append(command)
    return normalized


async def _command_consumer_loop(stop_event, command_path, redis_client, redis_prefix, bot):
    poll_sec = max(0.1, float(getattr(Config, "TRADING_COMMAND_POLL_SEC", 0.5) or 0.5))
    while not stop_event.is_set():
        try:
            commands = _pop_commands(command_path, redis_client, redis_prefix)
            for command in commands:
                cmd = str(command.get("cmd") or "").strip().lower()
                try:
                    await bot.process_command(command)
                except Exception as e:
                    log.warning(f"Failed processing trading command '{cmd}': {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"Trading command consumer failed: {e}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_sec)
        except asyncio.TimeoutError:
            continue


def _parse_heartbeat_epoch(raw) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val > 0 else 0.0
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except Exception:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0
    if isinstance(raw, dict):
        return _parse_heartbeat_epoch(raw.get("ts") or raw.get("timestamp"))
    return 0.0


def _read_core_heartbeat_epoch(redis_client, heartbeat_key: str, heartbeat_path: str) -> float:
    if not redis_client and heartbeat_key:
        redis_client = get_redis()
    if redis_client and heartbeat_key:
        try:
            payload = redis_safe_call(
                redis_client,
                "get",
                heartbeat_key,
                default=None,
                purpose="core_heartbeat_read",
            )
            ts = _parse_heartbeat_epoch(payload)
            if ts > 0:
                return ts
            if payload:
                obj = json.loads(payload)
                ts = _parse_heartbeat_epoch(obj)
                if ts > 0:
                    return ts
        except Exception:
            pass

    file_fallback_enabled = bool(getattr(Config, "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", True))
    if file_fallback_enabled and heartbeat_path and os.path.exists(heartbeat_path):
        try:
            with open(heartbeat_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            ts = _parse_heartbeat_epoch(payload)
            if ts > 0:
                return ts
        except Exception:
            pass

    return 0.0


def _resolve_watchdog_heartbeat_key(redis_prefix: str) -> str:
    return str(
        getattr(Config, "CORE_HEARTBEAT_KEY", core_heartbeat_key(redis_prefix))
        or core_heartbeat_key(redis_prefix)
    ).strip()


def _resolve_watchdog_heartbeat_path(base_dir: str, file_fallback_enabled: bool) -> str:
    if not file_fallback_enabled:
        return ""
    return str(
        getattr(
            Config,
            "CORE_HEARTBEAT_PATH",
            os.path.join(base_dir, "runtime", "core_heartbeat.json"),
        )
        or os.path.join(base_dir, "runtime", "core_heartbeat.json")
    ).strip()


async def _core_heartbeat_watchdog(stop_event, redis_client, redis_prefix, base_dir: str, bot: TradingBot):
    enabled = bool(getattr(Config, "TRADER_REQUIRE_CORE_HEARTBEAT", False))
    if not enabled:
        return

    stale_sec = max(5, int(getattr(Config, "TRADER_CORE_HEARTBEAT_STALE_SEC", 30)))
    check_sec = max(2, int(getattr(Config, "TRADER_CORE_HEARTBEAT_CHECK_SEC", 5)))
    grace_sec = max(5, int(getattr(Config, "TRADER_CORE_HEARTBEAT_GRACE_SEC", 120)))
    file_fallback_enabled = bool(getattr(Config, "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", True))
    heartbeat_key = _resolve_watchdog_heartbeat_key(redis_prefix)
    heartbeat_path = _resolve_watchdog_heartbeat_path(base_dir, file_fallback_enabled)

    start_ts = time.time()
    last_seen = 0.0
    active_issue_reason = ""
    log.info(
        f"Core heartbeat watchdog enabled (stale>{stale_sec}s, grace={grace_sec}s, key='{heartbeat_key}')."
    )

    while not stop_event.is_set():
        hb_ts = _read_core_heartbeat_epoch(redis_client, heartbeat_key, heartbeat_path)
        now = time.time()
        if hb_ts > 0:
            last_seen = hb_ts

        uptime = now - start_ts
        age = now - last_seen if last_seen > 0 else None

        issue_reason = ""
        issue_text = ""
        if uptime >= grace_sec:
            if last_seen <= 0:
                issue_reason = "core_heartbeat_missing"
                issue_text = "Core heartbeat missing after grace period."
            elif age is not None and age > stale_sec:
                issue_reason = "core_heartbeat_stale"
                issue_text = f"Core heartbeat stale ({age:.1f}s > {stale_sec}s)."

        control_state = str(getattr(bot, "_control_state", "") or "")
        control_reason = str(getattr(bot, "_control_reason", "") or "")
        watchdog_owned_block = control_state == "blocked" and control_reason.startswith("core_heartbeat_")

        if issue_reason:
            if issue_reason != active_issue_reason:
                if control_state == "blocked" and not watchdog_owned_block:
                    log.error(
                        "%s Trader already blocked by '%s'; preserving existing control state.",
                        issue_text,
                        control_reason or "unknown",
                    )
                else:
                    log.error("%s Blocking new entries; trader stays alive for protective exits.", issue_text)
            if control_state != "blocked" or watchdog_owned_block:
                if control_state != "blocked" or control_reason != issue_reason:
                    await bot.process_command({"cmd": "block", "reason": issue_reason})
            active_issue_reason = issue_reason
        elif active_issue_reason:
            if watchdog_owned_block:
                await bot.process_command({"cmd": "resume", "reason": "core_heartbeat_recovered"})
                log.info("Core heartbeat recovered. Resuming trader entry flow.")
            else:
                log.info("Core heartbeat recovered. Preserving existing trader control state.")
            active_issue_reason = ""

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=check_sec)
        except asyncio.TimeoutError:
            continue


async def main():
    if bool(getattr(Config, "OTEL_ENABLED", False)):
        install_background_tracing(
            service_name=str(getattr(Config, "OTEL_SERVICE_NAME", "elara-trading-daemon") or "elara-trading-daemon")
        )
    from database.database_manager import DatabaseManager
    from core.life import LifeManager

    cfg = TradingConfig()
    status_path = getattr(Config, "TRADING_STATUS_PATH", None) or \
        os.path.join(cfg.BASE_DIR, "runtime", "trading_status.json")
    command_path = getattr(Config, "TRADING_COMMAND_PATH", None) or \
        os.path.join(cfg.BASE_DIR, "runtime", "trading_command.json")
    redis_client = get_redis()
    redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
    if bool(getattr(Config, "REDIS_EVENT_BUS_REQUIRED", False)):
        if not redis_client:
            log.critical("Redis event bus diwajibkan, tetapi koneksi Redis gagal.")
            return
        readiness = redis_readiness_probe(redis_client, redis_prefix=str(redis_prefix or "elara:"))
        if not bool(readiness.get("ok")):
            log.critical("Redis event bus readiness failed: %s", readiness)
            return

    cfg_errors = _collect_trading_config_errors(cfg)
    if cfg_errors:
        for err in cfg_errors:
            log.critical(f"Invalid trading config: {err}")
        _write_setup_failure_status(
            status_path,
            redis_client,
            redis_prefix,
            f"invalid trading config: {' | '.join(cfg_errors[:3])}",
        )
        return

    _write_startup_status(status_path, redis_client, redis_prefix, stage="booting")

    _maybe_run_alembic_upgrade()
    dbm = DatabaseManager()
    life = LifeManager(
        min_sleep_hours=float(getattr(Config, "LIFE_MIN_SLEEP_HOURS", 5.0)),
        max_stress=int(getattr(Config, "LIFE_MAX_STRESS", 7)),
        min_energy=int(getattr(Config, "LIFE_MIN_ENERGY", 0)),
        min_focus_minutes=int(getattr(Config, "LIFE_MIN_FOCUS_MINUTES", 120)),
        context_max_age_sec=int(getattr(Config, "LIFE_CONTEXT_MAX_AGE_SEC", 86400)),
    )
    bot = TradingBot(cfg, dbm, life_manager=life, status_path=status_path, redis_client=redis_client)

    stop_event = asyncio.Event()
    ipc_server = None
    signal_bridge_task = None
    command_consumer_task = None
    if redis_client:
        signal_bridge_task = asyncio.create_task(
            _trade_signal_bridge(stop_event, redis_client, redis_prefix, command_path)
        )
    heartbeat_task = asyncio.create_task(
        _core_heartbeat_watchdog(
            stop_event=stop_event,
            redis_client=redis_client,
            redis_prefix=redis_prefix,
            base_dir=cfg.BASE_DIR,
            bot=bot,
        )
    )

    if bool(getattr(Config, "TRADING_IPC_ENABLED", True)):
        try:
            ipc_port = int(getattr(Config, "TRADING_IPC_PORT", 8768))
        except Exception:
            ipc_port = 8768
        ipc_host = getattr(Config, "TRADING_IPC_HOST", "127.0.0.1")
        ipc_server = SimpleIPCServer(ipc_host, ipc_port, status_path, command_path)
        ipc_server.start(redis_client=redis_client, redis_prefix=redis_prefix)
        log.info(f"IPC server listening on http://{ipc_host}:{ipc_port}")
    else:
        log.info("Trader IPC server disabled (TRADING_IPC_ENABLED=0).")

    command_consumer_task = asyncio.create_task(
        _command_consumer_loop(
            stop_event=stop_event,
            command_path=command_path,
            redis_client=redis_client,
            redis_prefix=redis_prefix,
            bot=bot,
        )
    )

    def _handle_sig(*_):
        log.info("Signal received, stopping bot...")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _handle_sig)
        except (NotImplementedError, RuntimeError, ValueError) as e:
            log.debug(f"Signal handler not supported on this platform ({s}): {e}")

    # Setup bot
    ok = await bot.setup()
    if not ok:
        log.error("Bot setup failed.")
        err_text = getattr(bot, "last_setup_error", "") or "exchange/data setup failed"
        _write_setup_failure_status(status_path, redis_client, redis_prefix, err_text)
        try:
            await bot.connector.close_connection()
        except Exception:
            pass
        try:
            if ipc_server:
                ipc_server.stop()
        except Exception:
            pass
        if signal_bridge_task:
            signal_bridge_task.cancel()
            try:
                await signal_bridge_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if command_consumer_task:
            command_consumer_task.cancel()
            try:
                await command_consumer_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        return

    runner = asyncio.create_task(bot.run_live())
    stop_waiter = asyncio.create_task(stop_event.wait())

    # Stop when OS signal arrives OR bot loop exits itself.
    _, pending = await asyncio.wait(
        [runner, stop_waiter],
        return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    try:
        await bot.shutdown()
    except Exception as e:
        log.error(f"Error during shutdown: {e}")
    if not runner.done():
        runner.cancel()
    try:
        await runner
    except Exception as e:
        log.warning(f"Runner task cancelled/failed during shutdown: {e}")
    try:
        if ipc_server:
            ipc_server.stop()
    except Exception as e:
        log.warning(f"IPC server stop failed: {e}")
    if signal_bridge_task:
        signal_bridge_task.cancel()
        try:
            await signal_bridge_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if command_consumer_task:
        command_consumer_task.cancel()
        try:
            await command_consumer_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if heartbeat_task:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log = logging.getLogger("elara.trading.daemon")
        log.error(f"Daemon crash: {e}", exc_info=True)
        sys.exit(1)

