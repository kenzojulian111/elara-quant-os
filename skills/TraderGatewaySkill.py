import json
import logging
import os
import time
import asyncio
import hashlib
import inspect
import uuid
from datetime import datetime, timezone

try:
    import httpx
except Exception:  # pragma: no cover - optional dependency in lightweight test/runtime
    httpx = None

from config.settings import Config
from core.base import NLPUnderstanding, Skill, ElaraTool
from core.events import trade_command_queue_key, trader_status_key
from utils.redis_helper import get_redis, redis_get_json
from utils.service_contracts import TraderStatusContract

log = logging.getLogger("elara.skill.trader_gateway")


class _NullAsyncHTTPClient:
    async def get(self, *_args, **_kwargs):
        raise RuntimeError("httpx is not installed")

    async def post(self, *_args, **_kwargs):
        raise RuntimeError("httpx is not installed")

    async def aclose(self):
        return None


def _resolve_command_queue_dir(command_path: str) -> str:
    if not command_path:
        return ""
    if command_path.endswith(".d"):
        return command_path
    if os.path.isdir(command_path):
        return command_path
    return f"{command_path}.d"


def _write_command_queue_file(command_path: str, payload: dict) -> str:
    queue_dir = _resolve_command_queue_dir(command_path)
    if not queue_dir:
        return ""
    os.makedirs(queue_dir, exist_ok=True)
    filename = f"{int(time.time() * 1000)}_{os.getpid()}_{uuid.uuid4().hex}.json"
    path = os.path.join(queue_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return queue_dir


class TraderGatewaySkill(Skill):
    """
    Trading skill gateway:
    - Read status from Redis/HTTP/file
    - Send commands via Redis queue (preferred) or HTTP/file fallback
    This avoids direct in-process coupling with trading engine.
    """

    def __init__(self, life_manager=None, http_client=None, redis_client=None, db_manager=None):
        self.life_manager = life_manager
        self.db_manager = db_manager
        self.redis = redis_client or get_redis()
        self.redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        self.status_key = str(
            getattr(Config, "TRADING_STATUS_KEY", trader_status_key(self.redis_prefix))
            or trader_status_key(self.redis_prefix)
        ).strip()
        self.command_key = str(
            getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(self.redis_prefix))
            or trade_command_queue_key(self.redis_prefix)
        ).strip()
        self.status_path = getattr(Config, "TRADING_STATUS_PATH", "")
        self.command_path = getattr(Config, "TRADING_COMMAND_PATH", "")
        self.api_base = str(getattr(Config, "TRADER_API_BASE_URL", "") or "").rstrip("/")
        self.api_timeout = float(getattr(Config, "TRADER_API_TIMEOUT_SEC", 2.0) or 2.0)
        self.ipc_token = str(getattr(Config, "TRADING_IPC_TOKEN", "") or "").strip()
        self.dedup_ttl_sec = max(10, int(getattr(Config, "TRADER_GATEWAY_DEDUP_TTL_SEC", 60)))
        if http_client is not None:
            self.http = http_client
        elif httpx is not None:
            limits = httpx.Limits(max_keepalive_connections=10, max_connections=50, keepalive_expiry=10.0)
            self.http = httpx.AsyncClient(timeout=self.api_timeout, limits=limits)
        else:
            self.http = _NullAsyncHTTPClient()
        self.db_fallback_key = str(getattr(Config, "TRADER_GATEWAY_DB_FALLBACK_KEY", "trader_gateway:pending_command") or "trader_gateway:pending_command")

    def _ipc_headers(self) -> dict:
        if not self.ipc_token:
            return {}
        return {"X-IPC-Token": self.ipc_token}

    def _build_command_ids(self, cmd: str, args: dict | None, source: str) -> tuple[str, str]:
        bucket = int(time.time() // max(1, self.dedup_ttl_sec))
        raw = json.dumps(
            {
                "cmd": str(cmd or "").strip().lower(),
                "args": args or {},
                "source": str(source or "core"),
                "bucket": bucket,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        command_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return command_id, command_id

    def get_intents(self):
        return [
            "trading_start",
            "trading_stop",
            "trading_status",
            "hedge_fund_start",
            "hedge_fund_stop",
            "hedge_fund_status",
            "hedge_fund_brief",
        ]

    def get_tools(self):
        return [
            ElaraTool(
                name="get_trading_status",
                description="Get latest trading daemon status and risk metrics.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            ElaraTool(
                name="flatten_all_positions",
                description="Send command to flatten/close all open positions in trading daemon. MANDATORY: You must ask the user for explicit confirmation before calling this tool.",
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Optional reason for audit log.",
                        },
                        "confirmation": {
                            "type": "string",
                            "description": "Must be exactly 'CONFIRM_ACTION' to prove user authentication."
                        }
                    },
                    "required": ["confirmation"],
                },
            ),
            ElaraTool(
                name="place_limit_order",
                description="Place a manual limit order via the trading bot daemon (Reflex Mode) to execute strictly and fast when price hits. MANDATORY: You must ask the user for explicit confirmation before calling this tool.",
                parameters={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Asset symbol to trade (e.g. BBCA, BTC/USDT)"},
                        "side": {"type": "string", "description": "buy or sell"},
                        "amount": {"type": "number", "description": "Amount to trade"},
                        "price": {"type": "number", "description": "Target limit price"},
                        "confirmation": {
                            "type": "string",
                            "description": "Must be exactly 'CONFIRM_ACTION' to prove user authentication."
                        }
                    },
                    "required": ["symbol", "side", "amount", "price", "confirmation"],
                },
            ),
        ]

    def _normalize_intent(self, intent: str) -> str:
        alias_map = {
            "hedge_fund_start": "trading_start",
            "hedge_fund_stop": "trading_stop",
            "hedge_fund_status": "trading_status",
        }
        return alias_map.get(intent, intent)

    def _normalize_status_age(self, data: dict) -> dict:
        if not isinstance(data, dict):
            return {}
        ts = data.get("ts")
        if not ts:
            return data
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = time.time() - dt.timestamp()
            max_age = int(getattr(Config, "TRADING_STATUS_MAX_AGE_SEC", 120))
            if max_age > 0 and age > max_age:
                data = dict(data)
                data["is_running"] = False
                data["stale"] = True
        except Exception:
            return data
        return data

    def _normalize_status_payload(self, payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        data = self._normalize_status_age(payload)
        if not data:
            return {}
        return TraderStatusContract.from_payload(data).raw

    async def _await_if_needed(self, value):
        if asyncio.iscoroutine(value):
            return await value
        return value

    async def _load_status_from_redis(self) -> dict:
        if not self.redis:
            return {}
        data = await asyncio.to_thread(redis_get_json, self.redis, self.status_key)
        return self._normalize_status_payload(data or {})

    async def _load_status_from_http(self) -> dict:
        if not self.api_base:
            return {}
        try:
            resp = await self.http.get(f"{self.api_base}/status", headers=self._ipc_headers())
            if resp.status_code >= 400:
                return {}
            data = resp.json()
            return self._normalize_status_payload(data)
        except Exception:
            return {}

    def _load_status_from_file(self) -> dict:
        if not self.status_path or not os.path.exists(self.status_path):
            return {}
        try:
            with open(self.status_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._normalize_status_payload(data)
        except Exception:
            return {}

    async def _load_daemon_status(self) -> dict:
        for loader in (self._load_status_from_redis, self._load_status_from_http, self._load_status_from_file):
            # Call the loader to get either a dict or a coroutine
            result = loader()
            # Await if the loader returned a coroutine
            data = await result if asyncio.iscoroutine(result) else result
            if data:
                return data
        return {}

    async def _send_daemon_command(self, cmd: str, args: dict | None = None, source: str = "core") -> bool:
        command_id, execution_id = self._build_command_ids(cmd, args, source)
        payload = {
            "cmd": str(cmd or "").strip().lower(),
            "priority": 1,
            "source": source,
            "ts": time.time(),
            "command_id": command_id,
            "execution_id": execution_id,
        }
        if args:
            payload.update(args)

        allowed_cmds = {"stop", "flatten", "block", "resume", "limit_order"}
        if payload["cmd"] not in allowed_cmds:
            return False

        if self.redis:
            try:
                await asyncio.to_thread(self.redis.lpush, self.command_key, json.dumps(payload))
                return True
            except Exception as e:
                log.warning(f"Redis command enqueue failed: {e}")

        if self.api_base:
            try:
                resp = await self.http.post(
                    f"{self.api_base}/command",
                    json=payload,
                    headers=self._ipc_headers(),
                )
                if resp.status_code < 400:
                    return True
            except Exception as e:
                log.warning(f"Trader API command failed: {e}")

        if self.command_path:
            try:
                queue_dir = await asyncio.to_thread(_write_command_queue_file, self.command_path, payload)
                if queue_dir:
                    log.error("FALLBACK_ALERT: TraderGateway command fallback to queue dir=%s", queue_dir)
                    return True
            except Exception as e:
                log.error(f"Command file fallback failed: {e}")

        if self.db_manager and hasattr(self.db_manager, "set_runtime_setting"):
            try:
                stamp = int(time.time() * 1000)
                key = f"{self.db_fallback_key}:{stamp}"
                ok = await asyncio.to_thread(
                    self.db_manager.set_runtime_setting,
                    key,
                    payload,
                    "trading",
                    "trader_gateway_fallback",
                )
                if ok:
                    log.error("FALLBACK_ALERT: TraderGateway command fallback persisted to DB key=%s", key)
                    return True
            except Exception as e:
                log.error(f"DB fallback command persist failed: {e}")
        return False

    def _format_brief(self, daemon_status: dict) -> str:
        status = TraderStatusContract.from_payload(daemon_status)
        running = status.is_running
        sim_fallback = status.simulation_fallback
        control_state = status.control_state
        blocked = bool(daemon_status.get("life_blocked", False))
        daily_blocked = bool(daemon_status.get("daily_risk_blocked", False))
        dd = float(daemon_status.get("daily_drawdown_pct", 0.0) or 0.0)
        global_blocked = bool(daemon_status.get("global_risk_blocked", False))
        global_dd = float(daemon_status.get("global_drawdown_pct", 0.0) or 0.0)
        max_dd = float(daemon_status.get("max_drawdown_limit_pct", 10.0) or 10.0)
        lite_mode = bool(daemon_status.get("lite_mode", False))
        ram_usage = float(daemon_status.get("ram_usage_pct", 0.0) or 0.0)
        open_positions = int(daemon_status.get("open_positions", 0) or 0)
        equity = float(daemon_status.get("equity", 0.0) or 0.0)
        setup_error = str(daemon_status.get("setup_error") or "").strip()

        engine_state = "ONLINE" if running else "OFFLINE"
        if sim_fallback:
            engine_state += " (SIMULATION FALLBACK)"
        risk_state = "BLOCKED" if (blocked or daily_blocked or global_blocked) else "ACTIVE"
        dd_limit = float(getattr(Config, "DAILY_MAX_LOSS_PCT", 10.0))

        # Data Obfuscation (Phase 13 - Item 21)
        # Prevent absolute massive portfolio numbers from leaking to external LLMs
        obfuscate = getattr(Config, "OBFUSCATE_PORTFOLIO_DATA", True)
        eq_str = "[OBFUSCATED_DUE_TO_PRIVACY]" if obfuscate else f"${equity:,.2f}"

        return (
            "Hedge Fund Brief\n"
            f"- Engine: {engine_state}\n"
            f"- Control State: {control_state}\n"
            f"- Risk Gate: {risk_state}\n"
            f"- Equity: {eq_str}\n"
            f"- Open Positions: {open_positions}\n"
            f"- Daily DD: {dd:.2f}% (limit {dd_limit:.2f}%)\n"
            f"- Max DD: {global_dd:.2f}% (limit {max_dd:.2f}%)\n"
            f"- Lite Mode: {'ON' if lite_mode else 'OFF'} (RAM {ram_usage:.1f}%)"
            + (f"\n- Last Setup Error: {setup_error}" if setup_error else "")
        )

    async def execute_tool(self, name: str, args: dict, user_id: str = "default") -> str:
        _ = user_id

        # Request Deduplication (Phase 10 - Item 6) & Pre-Flight Checks (Phase 10 - Item 14)
        if name in ("place_limit_order", "flatten_all_positions"):
            # Critical Action Verification (Phase 11 - Item 22)
            confirm_enabled = bool(getattr(Config, "CRITICAL_CONFIRM_ENABLED", True))
            if confirm_enabled:
                confirmation = str(args.get("confirmation") or "").strip()
                if confirmation and confirmation != "CONFIRM_ACTION":
                    return "Critical Action Verification Gagal: Perintah DITOLAK karena Anda tidak memberikan parameter `confirmation: 'CONFIRM_ACTION'`. Harap pastikan identitas dan persetujuan pengguna sebelum mengeksekusi aksi kritis ini."
                if not confirmation and name == "place_limit_order":
                    return "Critical Action Verification Gagal: Perintah DITOLAK karena Anda tidak memberikan parameter `confirmation: 'CONFIRM_ACTION'`. Harap pastikan identitas dan persetujuan pengguna sebelum mengeksekusi aksi kritis ini."

            dedup_str = f"{user_id}:{name}:{json.dumps(args, sort_keys=True) if args else ''}"
            dedup_hash = hashlib.sha256(dedup_str.encode("utf-8")).hexdigest()
            dedup_key = f"{self.redis_prefix}dedup:{dedup_hash}"
            if self.redis:
                is_new = await asyncio.to_thread(self.redis.set, dedup_key, "1", nx=True, ex=self.dedup_ttl_sec)
                if not is_new:
                    return (
                        f"Perintah {name} sedang diproses/baru saja dieksekusi. "
                        f"Harap tunggu {self.dedup_ttl_sec} detik (Deduplication Guard)."
                    )

            # Pre-Flight Check
            daemon = await self._await_if_needed(self._load_daemon_status())
            if not daemon:
                return "Pre-Flight Check Gagal: Trading Engine OFFLINE atau tidak merespon."
            if not TraderStatusContract.from_payload(daemon).is_running:
                return "Pre-Flight Check Gagal: Trading Engine sedang tidak aktif."
            if daemon.get("life_blocked") or daemon.get("daily_risk_blocked") or daemon.get("global_risk_blocked"):
                return "Pre-Flight Check Gagal: Risk Guard sedang aktif (Blocked). Operasi dibatalkan demi keamanan."

        if name == "get_trading_status":
            nlu = NLPUnderstanding(
                intent="trading_status",
                confidence=1.0,
                entities=[],
                raw_text="tool:get_trading_status",
                source="tool_call",
            )
            result = await self.handle(nlu, user_id=user_id)
            return result or "Status trading tidak tersedia."

        if name == "flatten_all_positions":
            source = "tool_call"
            payload = args if isinstance(args, dict) else {}
            reason = str(payload.get("reason") or "").strip()
            if reason:
                source = f"{source}:{reason[:40]}"
            ok = await self._await_if_needed(self._send_daemon_command("flatten", source=source))
            if not ok:
                return "Gagal kirim flatten command ke trading daemon."
            return "Perintah flatten_all_positions dikirim ke trading daemon."

        if name == "place_limit_order":
            symbol = args.get("symbol")
            side = args.get("side")
            amount = args.get("amount")
            price = args.get("price")
            payload = {
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price
            }
            if await self._await_if_needed(self._send_daemon_command("limit_order", args=payload, source="ai_tool")):
                side_txt = str(side).upper() if side is not None else "UNKNOWN"
                return f"Perintah Limit Order {side_txt} {amount} {symbol} di {price} berhasil diantrekan (Reflex Mode)."
            return "Gagal mengirim perintah Limit Order ke trading daemon."

        return f"Tool {name} tidak didukung oleh TraderGatewaySkill."

    async def handle(self, nlu: NLPUnderstanding, user_id="default"):
        intent = self._normalize_intent(nlu.intent)
        daemon_status = await self._await_if_needed(self._load_daemon_status())

        if nlu.intent == "hedge_fund_brief":
            if daemon_status:
                return self._format_brief(daemon_status)
            return "Hedge Fund bot OFFLINE. Status tidak tersedia."

        if intent == "trading_start":
            if self.life_manager:
                decision = self.life_manager.evaluate_trading(user_id=user_id)
                if not decision.allowed:
                    return f"Trading ditolak: {decision.reason}"

            if TraderStatusContract.from_payload(daemon_status).is_running:
                if await self._await_if_needed(self._send_daemon_command("resume", source="core_skill")):
                    return "Trading daemon ONLINE. Perintah resume dikirim."
                return "Trading daemon ONLINE."

            return "Trading daemon OFFLINE. Jalankan service ElaraTrader terlebih dulu."

        if intent == "trading_stop":
            if await self._await_if_needed(self._send_daemon_command("stop", source="core_skill")):
                return "Perintah stop dikirim ke trading daemon."
            return "Gagal kirim stop command ke trading daemon."

        if intent == "trading_status":
            if not daemon_status:
                return "Hedge Fund bot OFFLINE (status tidak tersedia)."

            status = TraderStatusContract.from_payload(daemon_status)
            running = status.is_running
            sim_fallback = status.simulation_fallback
            control_state = status.control_state
            blocked = bool(daemon_status.get("life_blocked", False))
            global_blocked = bool(daemon_status.get("global_risk_blocked", False))
            setup_error = str(daemon_status.get("setup_error") or "").strip()
            daily_dd = float(daemon_status.get("daily_drawdown_pct", 0.0) or 0.0)
            global_dd = float(daemon_status.get("global_drawdown_pct", 0.0) or 0.0)
            lite_mode = bool(daemon_status.get("lite_mode", False))
            ram_usage = float(daemon_status.get("ram_usage_pct", 0.0) or 0.0)
            open_positions = int(daemon_status.get("open_positions", 0) or 0)
            equity = float(daemon_status.get("equity", 0.0) or 0.0)
            cash = float(daemon_status.get("cash", 0.0) or 0.0)

            state = "ONLINE" if running else "OFFLINE"
            if sim_fallback:
                state += " (SIMULATION FALLBACK)"
            if blocked:
                state += " (RISK BLOCKED)"
            if global_blocked:
                state += " (MAX-DD BLOCKED)"
            if setup_error:
                state += f" (SETUP ERROR: {setup_error})"

            # Data Obfuscation (Phase 13 - Item 21)
            obfuscate = getattr(Config, "OBFUSCATE_PORTFOLIO_DATA", True)
            eq_str = "[OBFUSCATED]" if obfuscate else f"${equity:,.2f}"
            cash_str = "[OBFUSCATED]" if obfuscate else f"${cash:,.2f}"

            return (
                f"Hedge Fund bot {state}. "
                f"Control {control_state}, "
                f"Equity {eq_str}, Cash {cash_str}, "
                f"Open positions {open_positions}, Daily DD {daily_dd:.2f}%, Max DD {global_dd:.2f}%, "
                f"Lite {'ON' if lite_mode else 'OFF'} (RAM {ram_usage:.1f}%)."
            )

        return "Command not recognized."

    async def close(self):
        close_fn = getattr(self.http, "aclose", None)
        if callable(close_fn):
            try:
                result = close_fn()
                await self._await_if_needed(result)
            except Exception:
                pass

    async def stop(self):
        await self.close()

