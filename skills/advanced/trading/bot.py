from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from decimal import Decimal
from datetime import datetime, timezone

from config.settings import Config
from core.events import trader_status_key
from core.runtime_events import append_runtime_event
from skills.advanced.trading.exchange import ExchangeConnector
from utils.observability import record_exchange_latency, record_exchange_order_event, record_trader_status


class TradingBot:
    def __init__(self, config, db_manager, life_manager=None, status_path=None, redis_client=None):
        self.config = config
        self.db_manager = db_manager
        self.life_manager = life_manager
        self.status_path = status_path
        self.redis_client = redis_client
        self.log = logging.getLogger("TradingBot")
        self.connector = ExchangeConnector(config, self.log)
        self._runtime_cfg_last_values: dict[str, str] = {}
        self._credential_reload_pending = False
        self._open_order_sync_interval_sec = 60
        self._status_interval_sec = max(2, int(getattr(Config, "TRADING_STATUS_UPDATE_SEC", 5) or 5))
        self._running = False
        self.last_setup_error = ""
        self.exchange_time_drift_high = False
        self.simulation_fallback = str(getattr(self.config, "MODE", "paper") or "paper").lower() != "live"
        self.lite_mode = self.simulation_fallback
        self.lite_reason = "paper_mode" if self.simulation_fallback else ""
        self._control_state = "active"
        self._control_reason = ""
        self._last_command = ""
        self._processed_command_ids: dict[str, float] = {}
        self._command_dedup_ttl_sec = max(
            30,
            int(getattr(Config, "TRADING_EXECUTION_DEDUP_TTL_SEC", 600) or 600),
        )

    @staticmethod
    def _fingerprint(value: str) -> str:
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _refresh_runtime_settings(self, force: bool = False):
        _ = force
        if not self.db_manager or not hasattr(self.db_manager, "get_runtime_settings"):
            return {}
        settings = self.db_manager.get_runtime_settings("trading") or {}
        if not isinstance(settings, dict):
            return {}
        for raw_key, raw_value in settings.items():
            key = str(raw_key or "").strip().lower()
            if key == "daily_max_loss_pct":
                self.config.DAILY_MAX_LOSS_PCT = float(raw_value)
            elif key == "max_open_positions":
                self.config.MAX_OPEN_POSITIONS = int(raw_value)
            elif key == "rsi_buy_min":
                self.config.RSI_BUY_MIN = float(raw_value)
            elif key == "order_use_limit_chase":
                self.config.ORDER_USE_LIMIT_CHASE = bool(raw_value)
            elif key == "api_key":
                value = str(raw_value or "")
                self.config.API_KEY = value
                self.connector.update_runtime_credentials(api_key=value)
                self._runtime_cfg_last_values["api_key"] = self._fingerprint(value)
                self._credential_reload_pending = True
            elif key == "api_secret":
                value = str(raw_value or "")
                self.config.API_SECRET = value
                self.connector.update_runtime_credentials(api_secret=value)
                self._runtime_cfg_last_values["api_secret"] = self._fingerprint(value)
                self._credential_reload_pending = True
        return settings

    async def _maybe_reload_exchange_credentials(self):
        if not self._credential_reload_pending:
            return False
        ok = await self.connector.ensure_fresh_credentials()
        if ok:
            self._credential_reload_pending = False
            return True
        return False

    async def _sync_open_orders_with_exchange(self, force: bool = False):
        if not force and str(getattr(self.config, "MODE", "") or "").lower() != "live":
            return False
        if not self.db_manager or not hasattr(self.db_manager, "get_trades_with_open_exchange_orders"):
            return False

        changed = False
        rows = self.db_manager.get_trades_with_open_exchange_orders(limit=200) or []
        for row in rows:
            symbol = str(row.get("symbol") or "")
            order_id = str(row.get("exchange_order_id") or "")
            trade_id = row.get("trade_id")
            if not symbol or not order_id or trade_id is None:
                continue
            fetched = await self.connector.fetch_order(symbol, order_id)
            if not fetched:
                continue
            status = str(fetched.get("status") or "")
            updates = {
                "exchange_order_status": status,
                "exchange_order_last_sync": datetime.now(timezone.utc).replace(tzinfo=None),
            }
            if hasattr(self.db_manager, "update_trade"):
                self.db_manager.update_trade(trade_id, updates)
            if hasattr(self.db_manager, "log_trade_update"):
                self.db_manager.log_trade_update(
                    {
                        "trade_id": trade_id,
                        "event_type": "order_sync",
                        "exchange_order_status": status,
                        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
                    }
                )
            changed = True
        return changed

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return float(default)
            if isinstance(value, Decimal):
                return float(value)
            return float(value)
        except Exception:
            return float(default)

    def _collect_status_metrics(self) -> tuple[float, float, int]:
        if not self.db_manager or not hasattr(self.db_manager, "get_trading_metrics"):
            return 0.0, 0.0, 0
        try:
            metrics = self.db_manager.get_trading_metrics() or {}
        except Exception as e:
            self.log.debug(f"Trading metrics unavailable for status payload: {e}")
            return 0.0, 0.0, 0

        equity = self._to_float(metrics.get("equity"), 0.0)
        cash = self._to_float(metrics.get("cash"), 0.0)
        open_positions = 0
        try:
            rows = metrics.get("open_positions")
            if isinstance(rows, (list, tuple, set)):
                open_positions = len(rows)
            elif rows is not None:
                open_positions = int(rows)
        except Exception:
            open_positions = 0
        return equity, cash, max(0, open_positions)

    @staticmethod
    def _write_json_atomic(path: str, payload: dict):
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        last_error = None
        for attempt in range(3):
            tmp_path = f"{path}.tmp.{os.getpid()}.{int(datetime.now(timezone.utc).timestamp() * 1000)}.{attempt}"
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

    def _build_status_payload(
        self,
        *,
        is_running: bool,
        setup_in_progress: bool = False,
        setup_stage: str = "",
        setup_error: str = "",
    ) -> dict:
        equity, cash, open_positions = self._collect_status_metrics()
        derived_lite_mode = bool(self.simulation_fallback or self._control_state != "active")
        derived_lite_reason = str(self.lite_reason or "")
        if self._control_state != "active":
            derived_lite_reason = self._control_reason or self._control_state
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "is_running": bool(is_running),
            "setup_in_progress": bool(setup_in_progress),
            "setup_stage": str(setup_stage or "").strip(),
            "setup_error": str(setup_error or "").strip(),
            "simulation_fallback": bool(self.simulation_fallback),
            "exchange_time_drift_high": bool(self.exchange_time_drift_high),
            "life_blocked": False,
            "daily_risk_blocked": False,
            "daily_drawdown_pct": 0.0,
            "global_risk_blocked": False,
            "global_drawdown_pct": 0.0,
            "max_drawdown_limit_pct": self._to_float(getattr(self.config, "MAX_DRAWDOWN_PCT", 10.0), 10.0),
            "lite_mode": derived_lite_mode,
            "lite_reason": derived_lite_reason,
            "control_state": str(self._control_state or "active"),
            "last_command": str(self._last_command or ""),
            "ram_usage_pct": 0.0,
            "open_positions": int(open_positions),
            "equity": float(equity),
            "cash": float(cash),
        }
        return payload

    def _cleanup_processed_command_ids(self) -> None:
        now = time.time()
        stale = [key for key, expires_at in self._processed_command_ids.items() if expires_at <= now]
        for key in stale:
            self._processed_command_ids.pop(key, None)

    @staticmethod
    def _stable_command_fingerprint(command: dict) -> str:
        comparable = {
            key: value
            for key, value in dict(command or {}).items()
            if key not in {"ts", "timestamp"}
        }
        raw = json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _command_identity(self, command: dict) -> str:
        for key in ("execution_id", "command_id", "decision_id"):
            value = str((command or {}).get(key) or "").strip()
            if value:
                return f"{key}:{value}"
        return self._stable_command_fingerprint(command)

    def _dedup_setting_key(self, identity: str) -> str:
        digest = hashlib.sha256(str(identity or "").encode("utf-8")).hexdigest()
        return f"executed_command:{digest}"

    @staticmethod
    def _is_trade_execution_command(command: dict) -> bool:
        return str((command or {}).get("cmd") or "").strip().lower() in {"limit_order"}

    @staticmethod
    def _stable_execution_id(identity: str) -> str:
        digest = hashlib.sha256(str(identity or "").encode("utf-8")).hexdigest()
        return f"exec-{digest[:40]}"

    @staticmethod
    def _stable_client_order_id(execution_id: str) -> str:
        digest = hashlib.sha256(str(execution_id or "").encode("utf-8")).hexdigest()
        return f"elara-{digest[:32]}"

    @staticmethod
    def _config_hash() -> str:
        try:
            return Config.config_hash()
        except Exception:
            return ""

    def _exchange_label(self) -> str:
        return str(
            getattr(self.config, "EXCHANGE_NAME", "")
            or getattr(Config, "EXCHANGE_NAME", "")
            or "unknown"
        ).strip().lower() or "unknown"

    def _ensure_trade_command_ids(self, command: dict, identity: str) -> tuple[str, str]:
        execution_id = str((command or {}).get("execution_id") or "").strip()
        if not execution_id:
            execution_id = self._stable_execution_id(identity)
            command["execution_id"] = execution_id
        client_order_id = str((command or {}).get("client_order_id") or "").strip()
        if not client_order_id:
            client_order_id = self._stable_client_order_id(execution_id)
            command["client_order_id"] = client_order_id
        command.setdefault("config_hash", self._config_hash())
        return execution_id, client_order_id

    def _emit_order_event(self, event_type: str, command: dict, payload: dict | None = None) -> None:
        event_payload = {
            "cmd": str((command or {}).get("cmd") or ""),
            "symbol": str((command or {}).get("symbol") or ""),
            "side": str((command or {}).get("side") or ""),
            **dict(payload or {}),
        }
        append_runtime_event(
            event_type,
            source="trading_bot",
            payload=event_payload,
            correlation_id=str((command or {}).get("command_id") or (command or {}).get("execution_id") or ""),
            trace_id=str((command or {}).get("trace_id") or ""),
            decision_id=str((command or {}).get("decision_id") or ""),
            execution_id=str((command or {}).get("execution_id") or ""),
            config_hash=str((command or {}).get("config_hash") or self._config_hash()),
        )

    def _mark_command_execution_status(self, command: dict, status: str, payload: dict | None = None) -> None:
        if not self.db_manager or not hasattr(self.db_manager, "mark_trade_execution_status"):
            return
        execution_id = str((command or {}).get("execution_id") or "").strip()
        if not execution_id:
            return
        try:
            self.db_manager.mark_trade_execution_status(
                execution_id,
                status,
                payload=payload or {},
            )
        except Exception as e:
            self.log.debug(f"Command execution status update failed: {e}")

    def _claim_command_identity(self, command: dict) -> bool:
        identity = self._command_identity(command)
        if not identity:
            return True
        self._cleanup_processed_command_ids()
        now = time.time()
        if self._processed_command_ids.get(identity, 0.0) > now:
            return False

        if (
            self._is_trade_execution_command(command)
            and self.db_manager
            and hasattr(self.db_manager, "claim_trade_execution")
        ):
            execution_id, client_order_id = self._ensure_trade_command_ids(command, identity)
            try:
                claimed = self.db_manager.claim_trade_execution(
                    execution_id,
                    command_id=str((command or {}).get("command_id") or "").strip(),
                    decision_id=str((command or {}).get("decision_id") or "").strip(),
                    client_order_id=client_order_id,
                    cmd=str((command or {}).get("cmd") or "").strip().lower(),
                    payload=dict(command or {}),
                )
            except Exception as e:
                self.log.warning("Trade execution journal claim failed: %s", e)
                return False
            if not claimed:
                self._processed_command_ids[identity] = now + self._command_dedup_ttl_sec
                return False
            self._processed_command_ids[identity] = now + self._command_dedup_ttl_sec
            return True

        setting_key = self._dedup_setting_key(identity)
        if self.db_manager and hasattr(self.db_manager, "get_runtime_setting"):
            try:
                existing = self.db_manager.get_runtime_setting(
                    setting_key,
                    default=None,
                    scope="trade_execution",
                )
                if existing:
                    self._processed_command_ids[identity] = now + self._command_dedup_ttl_sec
                    return False
            except Exception as e:
                self.log.debug(f"Command dedup read failed: {e}")

        self._processed_command_ids[identity] = now + self._command_dedup_ttl_sec
        if self.db_manager and hasattr(self.db_manager, "set_runtime_setting"):
            try:
                self.db_manager.set_runtime_setting(
                    setting_key,
                    {
                        "identity": identity,
                        "cmd": str((command or {}).get("cmd") or ""),
                        "claimed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    scope="trade_execution",
                    updated_by="trader_command_dedup",
                )
            except Exception as e:
                self.log.debug(f"Command dedup persist failed: {e}")
        return True

    def _write_command_status(self, *, stage: str, error: str = "") -> None:
        self._write_status(
            self._build_status_payload(
                is_running=self._running,
                setup_in_progress=False,
                setup_stage=stage,
                setup_error=error,
            )
        )

    def _write_status(self, payload: dict) -> None:
        try:
            stale_after = max(5.0, float(self._status_interval_sec or 5) * 3.0)
            record_trader_status(payload, source="trading_bot", stale_after_sec=stale_after)
        except Exception as e:
            self.log.debug(f"Failed recording trading status metrics: {e}")

        if self.status_path and bool(getattr(Config, "TRADING_STATUS_FILE_FALLBACK_ENABLED", True)):
            try:
                self._write_json_atomic(self.status_path, payload)
            except Exception as e:
                self.log.warning(f"Failed writing trading status file: {e}")

        if self.redis_client:
            try:
                redis_prefix = str(getattr(Config, "REDIS_PREFIX", "elara:") or "elara:")
                redis_key = str(
                    getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
                    or trader_status_key(redis_prefix)
                ).strip()
                self.redis_client.set(redis_key, json.dumps(payload))
            except Exception as e:
                self.log.debug(f"Failed writing trading status to Redis: {e}")

    async def _process_limit_order_command(self, command: dict, reason: str) -> bool:
        symbol = str(command.get("symbol") or "").strip()
        side = str(command.get("side") or "").strip().lower()
        try:
            amount = Decimal(str(command.get("amount")))
            price = Decimal(str(command.get("price")))
        except Exception:
            self.log.warning("Trading command rejected: invalid limit_order amount/price")
            self._mark_command_execution_status(command, "rejected", {"reason": "invalid amount/price"})
            self._emit_order_event("ORDER_REJECTED", command, {"reason": "invalid amount/price"})
            record_exchange_order_event(
                exchange=self._exchange_label(),
                symbol=symbol,
                side=side,
                status="rejected",
            )
            return False
        if not symbol or side not in {"buy", "sell"} or amount <= 0 or price <= 0:
            self.log.warning("Trading command rejected: invalid limit_order payload")
            self._mark_command_execution_status(command, "rejected", {"reason": "invalid payload"})
            self._emit_order_event("ORDER_REJECTED", command, {"reason": "invalid payload"})
            record_exchange_order_event(
                exchange=self._exchange_label(),
                symbol=symbol,
                side=side,
                status="rejected",
            )
            return False

        mode = str(getattr(self.config, "MODE", "paper") or "paper").lower()
        if self.simulation_fallback or mode != "live":
            record_exchange_order_event(
                exchange=self._exchange_label(),
                symbol=symbol,
                side=side,
                status="simulated",
            )
            self.log.info(
                "Trading command applied: limit_order simulated symbol=%s side=%s amount=%s price=%s",
                symbol,
                side,
                amount,
                price,
            )
            self._mark_command_execution_status(
                command,
                "simulated",
                {
                    "symbol": symbol,
                    "side": side,
                    "amount": str(amount),
                    "price": str(price),
                },
            )
            self._emit_order_event(
                "ORDER_SIMULATED",
                command,
                {
                    "amount": str(amount),
                    "price": str(price),
                    "mode": mode,
                    "simulation_fallback": bool(self.simulation_fallback),
                },
            )
            self._write_command_status(stage="limit_order_simulated")
            return True

        if not self.connector.async_exchange:
            self.log.warning("Trading command rejected: live exchange connector unavailable")
            self._mark_command_execution_status(command, "rejected", {"reason": "exchange connector unavailable"})
            self._emit_order_event("ORDER_REJECTED", command, {"reason": "exchange connector unavailable"})
            record_exchange_order_event(
                exchange=self._exchange_label(),
                symbol=symbol,
                side=side,
                status="rejected",
            )
            self._write_command_status(stage="limit_order_rejected", error="exchange connector unavailable")
            return False

        qty, px = self.connector._apply_exchange_precision(symbol, amount, price, side=side)
        exchange = self._exchange_label()
        started = time.perf_counter()
        try:
            order = await self.connector.async_exchange.create_limit_order(symbol, side, qty, px)
            record_exchange_latency(
                exchange=exchange,
                operation="create_limit_order",
                symbol=symbol,
                duration_sec=time.perf_counter() - started,
                status="ok" if order else "empty",
            )
        except Exception as e:
            record_exchange_latency(
                exchange=exchange,
                operation="create_limit_order",
                symbol=symbol,
                duration_sec=time.perf_counter() - started,
                status="error",
            )
            record_exchange_order_event(exchange=exchange, symbol=symbol, side=side, status="failed")
            self.log.warning("Trading command failed: exchange limit_order error: %s", e)
            self._mark_command_execution_status(command, "failed", {"reason": "exchange create_limit_order error"})
            self._emit_order_event("ORDER_FAILED", command, {"reason": "exchange create_limit_order error"})
            self._write_command_status(stage="limit_order_failed", error="exchange create_limit_order error")
            return False
        if not order:
            self.log.warning("Trading command failed: exchange returned empty order")
            self._mark_command_execution_status(command, "failed", {"reason": "empty exchange order"})
            self._emit_order_event("ORDER_FAILED", command, {"reason": "empty exchange order"})
            record_exchange_order_event(exchange=exchange, symbol=symbol, side=side, status="failed")
            return False

        order_id = str(order.get("id") or "")
        status = str(order.get("status") or "open")
        trade_id = None
        if self.db_manager and hasattr(self.db_manager, "create_new_trade"):
            direction = "LONG" if side == "buy" else "SHORT"
            trade_id = self.db_manager.create_new_trade(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "status": "OPEN",
                    "entry_price": px,
                    "initial_size": qty,
                    "current_size": qty,
                    "entry_reason": reason or str(command.get("source") or "limit_order"),
                    "exchange_order_id": order_id,
                    "exchange_order_status": status,
                    "exchange_order_last_sync": datetime.now(timezone.utc).replace(tzinfo=None),
                }
            )
        if trade_id and self.db_manager and hasattr(self.db_manager, "log_trade_update"):
            self.db_manager.log_trade_update(
                {
                    "trade_id": trade_id,
                    "event_type": "limit_order_submitted",
                    "message": "Limit order submitted to exchange",
                    "detail": json.dumps(
                        {
                            "exchange_order_status": status,
                            "exchange_order_id": order_id,
                            "execution_id": str(command.get("execution_id") or ""),
                            "client_order_id": str(command.get("client_order_id") or ""),
                        },
                        sort_keys=True,
                    ),
                    "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
                }
            )

        self._mark_command_execution_status(
            command,
            "submitted",
            {
                "symbol": symbol,
                "side": side,
                "amount": str(qty),
                "price": str(px),
                "exchange_order_id": order_id,
                "exchange_order_status": status,
            },
        )
        self._emit_order_event(
            "ORDER_SUBMITTED",
            command,
            {
                "amount": str(qty),
                "price": str(px),
                "exchange_order_id": order_id,
                "exchange_order_status": status,
                "client_order_id": str(command.get("client_order_id") or ""),
            },
        )
        record_exchange_order_event(exchange=exchange, symbol=symbol, side=side, status="submitted")
        self.log.info(
            "Trading command applied: limit_order submitted symbol=%s side=%s amount=%s price=%s order_id=%s client_order_id=%s",
            symbol,
            side,
            qty,
            px,
            order_id,
            str(command.get("client_order_id") or ""),
        )
        self._write_command_status(stage="limit_order_submitted")
        return True

    async def setup(self):
        try:
            self._refresh_runtime_settings(force=True)
            await self._maybe_reload_exchange_credentials()
            mode = str(getattr(self.config, "MODE", "paper") or "paper").lower()
            self.simulation_fallback = mode != "live"
            self.lite_mode = self.simulation_fallback
            self.lite_reason = "paper_mode" if self.simulation_fallback else ""
            if mode == "live":
                api_key = str(getattr(self.config, "API_KEY", "") or "").strip()
                api_secret = str(getattr(self.config, "API_SECRET", "") or "").strip()
                if not api_key or not api_secret:
                    self.last_setup_error = "live mode requires API_KEY and API_SECRET"
                    self._write_status(
                        self._build_status_payload(
                            is_running=False,
                            setup_in_progress=False,
                            setup_stage="setup_failed",
                            setup_error=self.last_setup_error,
                        )
                    )
                    return False

            self.last_setup_error = ""
            self._write_status(
                self._build_status_payload(
                    is_running=False,
                    setup_in_progress=False,
                    setup_stage="ready",
                    setup_error="",
                )
            )
            return True
        except Exception as e:
            self.last_setup_error = str(e)
            self._write_status(
                self._build_status_payload(
                    is_running=False,
                    setup_in_progress=False,
                    setup_stage="setup_failed",
                    setup_error=self.last_setup_error,
                )
            )
            return False

    async def process_command(self, command: dict | str):
        if isinstance(command, dict):
            cmd = str(command.get("cmd") or "").strip().lower()
            reason = str(command.get("reason") or "").strip()
            command_payload = dict(command)
        else:
            cmd = str(command or "").strip().lower()
            reason = ""
            command_payload = {"cmd": cmd}
        if not cmd:
            return False

        if not self._claim_command_identity(command_payload):
            self.log.warning("Trading command duplicate ignored: %s", cmd)
            return True

        self._last_command = cmd
        if cmd in {"stop", "block"}:
            self._control_state = "blocked"
            self._control_reason = reason or cmd
            self.log.warning("Trading command applied: %s", cmd)
            self._write_command_status(stage="blocked")
            return True
        if cmd == "resume":
            self._control_state = "active"
            self._control_reason = ""
            self.log.info("Trading command applied: resume")
            self._write_command_status(stage="running")
            return True
        if cmd == "flatten":
            self._control_state = "blocked"
            self._control_reason = reason or "flatten"
            self.log.warning("Trading command applied: flatten (no-op in paper compatibility mode)")
            self._write_command_status(stage="flatten_requested")
            return True
        if cmd == "limit_order":
            return await self._process_limit_order_command(command_payload, reason)
        return False

    async def run_live(self):
        self._running = True
        try:
            while True:
                self._refresh_runtime_settings(force=False)
                await self._maybe_reload_exchange_credentials()
                await self._sync_open_orders_with_exchange(force=False)
                self._write_status(
                    self._build_status_payload(
                        is_running=True,
                        setup_in_progress=False,
                        setup_stage="running",
                        setup_error="",
                    )
                )
                await asyncio.sleep(self._status_interval_sec)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_setup_error = str(e)
            self._write_status(
                self._build_status_payload(
                    is_running=False,
                    setup_in_progress=False,
                    setup_stage="runtime_error",
                    setup_error=self.last_setup_error,
                )
            )
            raise
        finally:
            self._running = False
            self._write_status(
                self._build_status_payload(
                    is_running=False,
                    setup_in_progress=False,
                    setup_stage="stopped",
                    setup_error=self.last_setup_error,
                )
            )

    async def shutdown(self):
        self._running = False
        self._write_status(
            self._build_status_payload(
                is_running=False,
                setup_in_progress=False,
                setup_stage="shutdown",
                setup_error=self.last_setup_error,
            )
        )
        return True

