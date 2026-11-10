import json
import logging
import os
import threading
import time
import uuid
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.settings import Config
from core.events import build_trade_command_message, parse_trade_command_message, trade_command_queue_key, trader_status_key
from utils.observability import REGISTRY as OBSERVABILITY_REGISTRY
from utils.observability import record_trader_status

log = logging.getLogger("elara.trading.ipc")


def _write_json_atomic(path: str, payload: dict):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
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


def _resolve_command_queue_dir(command_path: str) -> str:
    if not command_path:
        return ""
    if command_path.endswith(".d"):
        return command_path
    if os.path.isdir(command_path):
        return command_path
    return f"{command_path}.d"


def _expected_ipc_token() -> str:
    return str(getattr(Config, "TRADING_IPC_TOKEN", "") or "").strip()


def _is_loopback(ip: str) -> bool:
    if not ip:
        return False
    return ip == "127.0.0.1" or ip == "::1" or ip.startswith("127.")


def _extract_auth_token(headers) -> str:
    token = str(headers.get("X-IPC-Token", "") or "").strip()
    if token:
        return token
    auth = str(headers.get("Authorization", "") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def _write_command_queue_file(command_path: str, payload: dict) -> str:
    queue_dir = _resolve_command_queue_dir(command_path)
    if not queue_dir:
        return ""
    os.makedirs(queue_dir, exist_ok=True)
    filename = f"{int(time.time() * 1000)}_{os.getpid()}_{uuid.uuid4().hex}.json"
    _write_json_atomic(os.path.join(queue_dir, filename), payload)
    return queue_dir


def _resolve_status_key(redis_prefix: str) -> str:
    return str(
        getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
        or trader_status_key(redis_prefix)
    ).strip()


def _resolve_command_key(redis_prefix: str) -> str:
    return str(
        getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
        or trade_command_queue_key(redis_prefix)
    ).strip()


def _build_ipc_command_payload(data: dict) -> dict:
    parsed_envelope = parse_trade_command_message(data)
    if parsed_envelope:
        data = parsed_envelope
    cmd = str((data or {}).get("cmd") or "").strip().lower()
    source = str((data or {}).get("source") or "ipc").strip() or "ipc"
    reason = str((data or {}).get("reason") or "")
    user_id = str((data or {}).get("user_id") or "ipc").strip() or "ipc"
    command_id = str((data or {}).get("command_id") or "").strip() or uuid.uuid4().hex
    execution_id = str((data or {}).get("execution_id") or "").strip() or command_id
    decision_id = str((data or {}).get("decision_id") or "").strip()
    try:
        priority = int((data or {}).get("priority") or 1)
    except Exception:
        priority = 1

    if cmd == "limit_order":
        payload = {
            "event": "trade.command.v1",
            "source": source,
            "ts": float(time.time()),
            "version": 1,
            "payload": {
                "cmd": cmd,
                "reason": reason,
                "priority": priority,
                "user_id": user_id,
                "command_id": command_id,
                "execution_id": execution_id,
            },
        }
        if decision_id:
            payload["payload"]["decision_id"] = decision_id
    else:
        payload = build_trade_command_message(
            cmd=cmd,
            source=source,
            reason=reason,
            priority=priority,
            user_id=user_id,
            command_id=command_id,
            decision_id=decision_id,
            execution_id=execution_id,
        )

    nested = payload.get("payload") if isinstance(payload, dict) else None
    if isinstance(nested, dict):
        for field in ("symbol", "side", "amount", "price"):
            if field in (data or {}):
                nested[field] = data.get(field)
    return payload


def _decode_json_payload(raw) -> dict:
    if raw is None:
        return {}
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_status_payload(redis_client, redis_prefix: str, status_key: str, status_path: str) -> tuple[dict, bytes]:
    if redis_client and redis_prefix:
        key = status_key or _resolve_status_key(redis_prefix)
        val = redis_client.get(key)
        if val:
            if isinstance(val, bytes):
                return _decode_json_payload(val), val
            text = str(val)
            return _decode_json_payload(text), text.encode("utf-8")
    if status_path:
        with open(status_path, "r", encoding="utf-8") as f:
            data = f.read()
        return _decode_json_payload(data), data.encode("utf-8")
    return {}, b"{}"


def _record_status_metrics(payload: dict, *, source: str = "trader_ipc") -> None:
    stale_after_sec = max(
        5.0,
        float(getattr(Config, "TRADING_STATUS_STALE_SEC", 30.0) or 30.0),
    )
    record_trader_status(payload, source=source, stale_after_sec=stale_after_sec)
    OBSERVABILITY_REGISTRY.gauge("elara_trader_ipc_up", 1.0, source=source)


class _Handler(BaseHTTPRequestHandler):
    status_path = None
    command_path = None
    redis_client = None
    redis_prefix = ""
    status_key = ""
    command_queue_key = ""

    def _set_headers(self, code=200, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def _authorize_request(self) -> bool:
        client_ip = self.client_address[0] if self.client_address else ""
        if _is_loopback(client_ip):
            return True
        expected = _expected_ipc_token()
        if expected:
            provided = _extract_auth_token(self.headers)
            return bool(provided) and hmac.compare_digest(provided, expected)
        return False

    def _authorize_metrics_request(self) -> bool:
        if bool(getattr(Config, "PROMETHEUS_METRICS_PUBLIC", False)):
            return True
        expected = str(getattr(Config, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
        if expected:
            provided = _extract_auth_token(self.headers)
            provided = provided or str(self.headers.get("X-Prometheus-Token", "") or "").strip()
            if provided and hmac.compare_digest(provided, expected):
                return True
        return self._authorize_request()

    def do_GET(self):
        if self.path.startswith("/metrics"):
            if not bool(getattr(Config, "PROMETHEUS_METRICS_ENABLED", True)):
                self._set_headers(404, content_type="text/plain; charset=utf-8")
                self.wfile.write(b"metrics disabled\n")
                return
            if not self._authorize_metrics_request():
                log.warning("IPC unauthorized metrics request from %s", self.client_address)
                self._set_headers(401, content_type="text/plain; charset=utf-8")
                self.wfile.write(b"unauthorized\n")
                return
            try:
                payload, _raw = _read_status_payload(
                    self.redis_client,
                    self.redis_prefix,
                    self.status_key,
                    self.status_path,
                )
                _record_status_metrics(payload)
            except Exception as e:
                log.debug("IPC metrics status snapshot skipped: %s", e)
                OBSERVABILITY_REGISTRY.gauge("elara_trader_ipc_up", 1.0, source="trader_ipc")
            self._set_headers(200, content_type="text/plain; version=0.0.4; charset=utf-8")
            self.wfile.write(OBSERVABILITY_REGISTRY.render_prometheus().encode("utf-8"))
            return

        if not self._authorize_request():
            log.warning("IPC unauthorized request from %s", self.client_address)
            self._set_headers(401)
            self.wfile.write(b"{\"error\":\"unauthorized\"}")
            return
        if self.path.startswith("/status"):
            try:
                payload, raw = _read_status_payload(
                    self.redis_client,
                    self.redis_prefix,
                    self.status_key,
                    self.status_path,
                )
                if raw and raw != b"{}":
                    _record_status_metrics(payload)
                    self._set_headers(200)
                    self.wfile.write(raw)
                    return
            except Exception as e:
                log.error(f"IPC /status handler failed: {e}")
            self._set_headers(404)
            self.wfile.write(b"{}")
        else:
            self._set_headers(404)
            self.wfile.write(b"{}")

    def do_POST(self):
        if not self._authorize_request():
            log.warning("IPC unauthorized request from %s", self.client_address)
            self._set_headers(401)
            self.wfile.write(b"{\"error\":\"unauthorized\"}")
            return
        if self.path.startswith("/command"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
                payload = _build_ipc_command_payload(data)
                if payload:
                    if self.redis_client and self.redis_prefix:
                        key = self.command_queue_key or _resolve_command_key(self.redis_prefix)
                        self.redis_client.lpush(key, json.dumps(payload))
                    elif self.command_path:
                        _write_command_queue_file(self.command_path, payload)
                    self._set_headers(200)
                    self.wfile.write(b"{\"status\":\"queued\"}")
                    return
            except Exception as e:
                log.error(f"IPC /command handler failed: {e}")
            self._set_headers(400)
            self.wfile.write(b"{\"error\":\"bad command\"}")
        else:
            self._set_headers(404)
            self.wfile.write(b"{}")


class SimpleIPCServer:
    def __init__(self, host, port, status_path, command_path):
        self.host = host
        self.port = port
        self.status_path = status_path
        self.command_path = command_path
        self.httpd = None
        self.thread = None
        self.redis_client = None
        self.redis_prefix = ""

    def start(self, redis_client=None, redis_prefix=""):
        _Handler.status_path = self.status_path
        _Handler.command_path = self.command_path
        _Handler.redis_client = redis_client
        _Handler.redis_prefix = redis_prefix
        _Handler.status_key = _resolve_status_key(redis_prefix)
        _Handler.command_queue_key = _resolve_command_key(redis_prefix)
        self.httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=1)

