import json
import logging
import os
import threading
import time
from typing import Any, Optional

try:
    import redis
    from redis.exceptions import RedisError, ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
    _REDIS_IMPORT_ERROR = None
except ImportError as e:
    redis = None
    RedisError = RedisConnectionError = RedisTimeoutError = OSError
    _REDIS_IMPORT_ERROR = e

log = logging.getLogger("elara.redis")

_REDIS_LOCK = threading.Lock()
_REDIS_POOL = None
_REDIS_POOL_URL = ""
_REDIS_CLIENT = None
_REDIS_NEXT_RETRY_TS = 0.0
_REDIS_LAST_PING_TS = 0.0
_REDIS_LAST_PING_OK = False


class RedisUnavailableError(RuntimeError):
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _redis_url() -> str:
    if "REDIS_URL" in os.environ:
        return str(os.environ.get("REDIS_URL", "") or "").strip()
    try:
        from config.settings import Config
        return str(getattr(Config, "REDIS_URL", "") or "").strip()
    except Exception:
        return ""


def _redis_connect_config():
    return {
        "max_attempts": max(1, _env_int("REDIS_CONNECT_RETRY_MAX_ATTEMPTS", 3)),
        "base_backoff": max(0.05, _env_float("REDIS_CONNECT_RETRY_BASE_SEC", 0.15)),
        "max_backoff": max(0.2, _env_float("REDIS_CONNECT_RETRY_MAX_SEC", 2.0)),
        "socket_timeout": max(0.2, _env_float("REDIS_SOCKET_TIMEOUT_SEC", 1.5)),
        "connect_timeout": max(0.2, _env_float("REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 1.5)),
        "max_connections": max(4, _env_int("REDIS_MAX_CONNECTIONS", 64)),
        "healthcheck_ttl": max(1.0, _env_float("REDIS_HEALTHCHECK_TTL_SEC", 6.0)),
    }


def _build_pool(url: str):
    if redis is None:
        raise RedisUnavailableError(f"redis package unavailable: {_REDIS_IMPORT_ERROR}")
    cfg = _redis_connect_config()
    return redis.ConnectionPool.from_url(
        url,
        max_connections=cfg["max_connections"],
        socket_timeout=cfg["socket_timeout"],
        socket_connect_timeout=cfg["connect_timeout"],
        health_check_interval=30,
        retry_on_timeout=True,
        socket_keepalive=True,
        decode_responses=False,
    )


def _can_attempt_connect(force: bool = False) -> bool:
    if force:
        return True
    return time.time() >= float(_REDIS_NEXT_RETRY_TS or 0.0)


def _ping_client(client) -> bool:
    global _REDIS_LAST_PING_TS, _REDIS_LAST_PING_OK
    cfg = _redis_connect_config()
    now = time.time()
    if _REDIS_LAST_PING_OK and (now - float(_REDIS_LAST_PING_TS or 0.0)) < cfg["healthcheck_ttl"]:
        return True
    try:
        client.ping()
        _REDIS_LAST_PING_OK = True
        _REDIS_LAST_PING_TS = now
        return True
    except Exception:
        _REDIS_LAST_PING_OK = False
        _REDIS_LAST_PING_TS = now
        return False


def get_redis(*, require: bool = False, force_reconnect: bool = False, purpose: str | None = None):
    """
    Get shared Redis client using singleton connection pool.
    Includes exponential-backoff retries to avoid hot-loop reconnect latency.
    """
    global _REDIS_POOL, _REDIS_POOL_URL, _REDIS_CLIENT, _REDIS_NEXT_RETRY_TS

    if redis is None:
        if require:
            raise RedisUnavailableError(f"redis package unavailable: {_REDIS_IMPORT_ERROR}")
        return None

    url = _redis_url()
    if not url:
        if require:
            raise RedisUnavailableError("REDIS_URL kosong.")
        return None
    if not _can_attempt_connect(force_reconnect):
        if require:
            raise RedisUnavailableError("Redis connect backoff active.")
        return None

    with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            if not force_reconnect and _ping_client(_REDIS_CLIENT):
                return _REDIS_CLIENT
            _REDIS_CLIENT = None

        cfg = _redis_connect_config()
        if _REDIS_POOL is None or _REDIS_POOL_URL != url:
            try:
                _REDIS_POOL = _build_pool(url)
                _REDIS_POOL_URL = url
            except Exception as e:
                log.warning(f"Redis pool init failed: {e}")
                _REDIS_NEXT_RETRY_TS = time.time() + cfg["base_backoff"]
                if require:
                    raise RedisUnavailableError(f"Redis pool init failed: {e}") from e
                return None

        last_error = None
        delay = cfg["base_backoff"]
        for attempt in range(1, cfg["max_attempts"] + 1):
            try:
                client = redis.Redis(connection_pool=_REDIS_POOL)
                if not _ping_client(client):
                    raise RedisConnectionError("Redis ping failed.")
                _REDIS_CLIENT = client
                _REDIS_NEXT_RETRY_TS = 0.0
                return _REDIS_CLIENT
            except Exception as e:
                last_error = e
                if attempt >= cfg["max_attempts"]:
                    break
                time.sleep(delay)
                delay = min(cfg["max_backoff"], delay * 2.0)

        log.warning(f"Redis connection failed after {cfg['max_attempts']} attempt(s): {last_error}")
        _REDIS_CLIENT = None
        _REDIS_NEXT_RETRY_TS = time.time() + min(cfg["max_backoff"], delay)
        if require:
            raise RedisUnavailableError(
                f"Redis connection failed after {cfg['max_attempts']} attempt(s): {last_error}"
            ) from last_error
        return None


def redis_safe_call(
    client,
    func,
    *args,
    default=None,
    retries: int | None = None,
    purpose: str | None = None,
    **kwargs,
):
    func_name = None
    if isinstance(func, str):
        func_name = func
        func = None
    elif callable(func):
        func_name = getattr(func, "__name__", None)

    if client is None:
        client = get_redis()
    if client is None:
        return default

    call_fn = func if callable(func) else (getattr(client, func_name, None) if func_name else None)
    if not callable(call_fn):
        return default

    max_attempts = retries if retries is not None else max(1, _env_int("REDIS_OP_RETRY_MAX_ATTEMPTS", 2))
    backoff = max(0.05, _env_float("REDIS_OP_RETRY_BACKOFF_SEC", 0.1))
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_fn(*args, **kwargs)
        except (RedisError, RedisConnectionError, RedisTimeoutError, OSError) as e:
            last_err = e
            if attempt >= max_attempts:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 1.5)
            refreshed = get_redis(force_reconnect=True)
            if refreshed:
                client = refreshed
                call_fn = getattr(client, func_name, None) if func_name else func
                if not callable(call_fn):
                    break
    if last_err:
        log.warning(f"Redis op failed{f' ({purpose})' if purpose else ''}: {last_err}")
    return default


def redis_get_json(client, key: str):
    if not client:
        return None
    if not isinstance(key, str) or not key:
        return None
    try:
        val = redis_safe_call(client, "get", key, default=None, purpose="get_json")
        if not val:
            return None
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        if not isinstance(val, str):
            return None
        return json.loads(val)
    except Exception:
        return None


def redis_set_json(client, key: str, value: Any, ttl: Optional[int] = None) -> bool:
    if not client:
        return False
    if not isinstance(key, str) or not key:
        return False
    try:
        data = json.dumps(value)
        if ttl is not None:
            ttl_int = int(ttl)
            if ttl_int > 0:
                ok = redis_safe_call(client, "setex", key, ttl_int, data, default=None, purpose="set_json")
            else:
                ok = redis_safe_call(client, "set", key, data, default=None, purpose="set_json")
        else:
            ok = redis_safe_call(client, "set", key, data, default=None, purpose="set_json")
        return bool(ok)
    except Exception:
        return False


def redis_readiness_probe(
    client=None,
    *,
    redis_prefix: str = "elara:",
    latency_threshold_ms: float | None = None,
    pubsub_timeout_sec: float = 0.5,
) -> dict[str, Any]:
    """
    Validate Redis as a usable event bus, not merely an open TCP port.
    Checks auth/ping, write/read/delete, pubsub roundtrip, and latency budget.
    """
    started = time.perf_counter()
    checks: dict[str, dict[str, Any]] = {}
    token = f"{os.getpid()}:{time.time_ns()}"
    prefix = str(redis_prefix or "elara:")
    key = f"{prefix}readiness:{os.getpid()}:{time.time_ns()}"
    channel = f"{key}:pubsub"

    try:
        client = client or get_redis(require=True, force_reconnect=True, purpose="readiness")
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": 0.0,
            "checks": {"connect": {"status": "down", "error": str(e)}},
            "error": str(e),
        }

    if client is None:
        return {
            "ok": False,
            "latency_ms": 0.0,
            "checks": {"connect": {"status": "down", "error": "client is None"}},
            "error": "client is None",
        }

    try:
        client.ping()
        checks["ping"] = {"status": "ok"}
    except Exception as e:
        checks["ping"] = {"status": "down", "error": str(e)}

    try:
        client.set(key, token, ex=10)
        raw = client.get(key)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if str(raw) != token:
            raise RuntimeError("read-after-write mismatch")
        client.delete(key)
        checks["read_write"] = {"status": "ok"}
    except Exception as e:
        checks["read_write"] = {"status": "down", "error": str(e)}

    pubsub = None
    try:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel)
        client.publish(channel, token)
        deadline = time.time() + max(0.05, float(pubsub_timeout_sec))
        delivered = False
        while time.time() < deadline:
            message = pubsub.get_message(timeout=0.05)
            if not message:
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if str(data) == token:
                delivered = True
                break
        if not delivered:
            raise RuntimeError("pubsub message not observed")
        checks["pubsub"] = {"status": "ok"}
    except Exception as e:
        checks["pubsub"] = {"status": "down", "error": str(e)}
    finally:
        if pubsub:
            try:
                pubsub.close()
            except Exception:
                pass

    stream_key = f"{key}:stream"
    stream_id = ""
    try:
        stream_id = client.xadd(
            stream_key,
            {"token": token},
            maxlen=10,
            approximate=True,
        )
        rows = client.xrange(stream_key, min="-", max="+", count=10)
        observed = False
        # rows may be None or contain items in different shapes depending on client
        for entry in (rows or []):
            # entry is typically a (id, fields) tuple, but be defensive
            try:
                _, fields = entry
            except Exception:
                # not a tuple of two, try to treat entry as mapping
                fields = entry if isinstance(entry, dict) else {}
            if not fields:
                continue
            # fields may have bytes keys/values
            value = None
            try:
                value = fields.get("token")
            except Exception:
                # fields might be a list or other structure
                pass
            if value is None:
                try:
                    value = fields.get(b"token")
                except Exception:
                    pass
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if str(value) == token:
                observed = True
                break
        if not observed:
            raise RuntimeError("stream message not observed")
        client.xdel(stream_key, stream_id)
        checks["streams"] = {"status": "ok"}
    except Exception as e:
        checks["streams"] = {"status": "down", "error": str(e)}

    latency_ms = (time.perf_counter() - started) * 1000.0
    threshold = latency_threshold_ms
    if threshold is None:
        threshold = max(1.0, _env_float("REDIS_READINESS_MAX_LATENCY_MS", 500.0))
    checks["latency"] = {
        "status": "ok" if latency_ms <= float(threshold) else "slow",
        "latency_ms": round(latency_ms, 2),
        "threshold_ms": float(threshold),
    }
    ok = all(str(item.get("status")) == "ok" for item in checks.values())
    return {
        "ok": ok,
        "latency_ms": round(latency_ms, 2),
        "checks": checks,
    }

