import json
import logging
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

from flask import has_app_context
from sqlalchemy import case

from config.settings import Config
from database.enums import TradeStatus
from database.models import (
    BlockedIP,
    EquityLog,
    RuntimeSetting,
    Trade,
    TradeUpdate,
    TrustedDevice,
    User,
    db,
)

log = logging.getLogger("elara.db.repositories")


class _BaseRepository:
    def __init__(self, manager):
        self.manager = manager

    def _ctx(self):
        return self.manager._ctx()

    def _commit(self) -> bool:
        return self.manager._commit_with_retry()


class UserRepository(_BaseRepository):
    def check_user(self, email: str, password: str) -> bool:
        with self._ctx():
            try:
                try:
                    self.manager._maybe_create_all()
                except Exception as e:
                    log.warning(f"check_user create_all warning: {e}")
                user = User.query.filter_by(email=email).first()
                return bool(user and user.check_password(password))
            except Exception as e:
                log.error(f"Auth DB Error: {e}")
                return False

    def ensure_bootstrap_user(self) -> bool:
        email = (Config.ADMIN_BOOTSTRAP_EMAIL or "").strip()
        password = (Config.ADMIN_BOOTSTRAP_PASSWORD or "").strip()
        if not email or not password:
            return False
        if not self.manager.app and not has_app_context():
            return False

        with self._ctx():
            try:
                try:
                    self.manager._maybe_create_all()
                except Exception as e:
                    log.warning(f"ensure_bootstrap_user create_all warning: {e}")
                if User.query.filter_by(email=email).first():
                    return False
                user = User(
                    email=email,
                    name=Config.ADMIN_BOOTSTRAP_NAME,
                    role=Config.ADMIN_BOOTSTRAP_ROLE,
                )
                user.set_password(password)
                db.session.add(user)
                if self._commit():
                    log.warning("Bootstrap admin user created from env config.")
                    return True
                log.error("Bootstrap user commit failed.")
                return False
            except Exception as e:
                log.error(f"Bootstrap user error: {e}")
                db.session.rollback()
                return False

    def get_user_role(self, email: str):
        if not email:
            return None
        with self._ctx():
            try:
                user = User.query.filter_by(email=email).first()
                return user.role if user else None
            except Exception as e:
                log.error(f"Role lookup error: {e}")
                return None

    def get_user_profile(self, email: str) -> Dict[str, Any]:
        if not email:
            return {}
        with self._ctx():
            try:
                user = User.query.filter_by(email=email).first()
                if not user:
                    return {}
                return {
                    "id": int(user.id),
                    "email": str(user.email or ""),
                    "name": str(user.name or ""),
                    "role": str(user.role or "user"),
                }
            except Exception as e:
                log.error(f"User profile lookup error: {e}")
                return {}

    def list_users(self, limit: int = 200):
        with self._ctx():
            try:
                rows = (
                    User.query
                    .order_by(User.id.asc())
                    .limit(max(1, int(limit)))
                    .all()
                )
                out = []
                for user in rows:
                    out.append(
                        {
                            "id": int(user.id),
                            "email": str(user.email or ""),
                            "name": str(user.name or ""),
                            "role": str(user.role or "user"),
                        }
                    )
                return out
            except Exception as e:
                log.error(f"list_users error: {e}")
                return []

    def create_user(self, email: str, password: str, name: str = "", role: str = "user"):
        email_val = str(email or "").strip().lower()
        if not email_val or not password:
            return {}
        role_val = str(role or "user").strip().lower()
        if role_val not in {"user", "admin", "superuser"}:
            role_val = "user"
        with self._ctx():
            try:
                exists = User.query.filter_by(email=email_val).first()
                if exists:
                    return {}
                user = User(
                    email=email_val,
                    name=str(name or "").strip(),
                    role=role_val,
                )
                user.set_password(password)
                db.session.add(user)
                if not self._commit():
                    return {}
                return {
                    "id": int(user.id),
                    "email": str(user.email or ""),
                    "name": str(user.name or ""),
                    "role": str(user.role or "user"),
                }
            except Exception as e:
                db.session.rollback()
                log.error(f"create_user error: {e}")
                return {}

    def update_user(self, user_id: int, *, role: str = None, name: str = None, password: str = None):
        with self._ctx():
            try:
                user = User.query.get(int(user_id))
                if not user:
                    return {}
                if role is not None:
                    role_val = str(role or "").strip().lower()
                    if role_val in {"user", "admin", "superuser"}:
                        user.role = role_val
                if name is not None:
                    user.name = str(name or "").strip()
                if password is not None and str(password).strip():
                    user.set_password(str(password))
                if not self._commit():
                    return {}
                return {
                    "id": int(user.id),
                    "email": str(user.email or ""),
                    "name": str(user.name or ""),
                    "role": str(user.role or "user"),
                }
            except Exception as e:
                db.session.rollback()
                log.error(f"update_user error: {e}")
                return {}


class TradingMetricsRepository(_BaseRepository):
    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        try:
            if value is None:
                return Decimal(default)
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def get_trading_metrics(self) -> Dict[str, Any]:
        if not self.manager.app and not has_app_context():
            return self._empty_metrics()

        with self._ctx():
            try:
                break_even_eps = abs(Decimal(str(getattr(Config, "TRADING_BREAKEVEN_EPSILON", "1e-9"))))
                metrics_lookback_days = max(0, int(getattr(Config, "TRADING_METRICS_LOOKBACK_DAYS", 0) or 0))
                trade_update_q = db.session.query(
                    TradeUpdate.trade_id.label("trade_id"),
                    db.func.coalesce(
                        db.func.sum(db.func.coalesce(TradeUpdate.pnl, Decimal("0"))),
                        Decimal("0"),
                    ).label("trade_pnl"),
                )
                if metrics_lookback_days > 0:
                    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=metrics_lookback_days)
                    trade_update_q = trade_update_q.filter(TradeUpdate.timestamp >= cutoff)
                trade_pnl_subq = trade_update_q.group_by(TradeUpdate.trade_id).subquery()

                stats = db.session.query(
                    db.func.count(trade_pnl_subq.c.trade_id),
                    db.func.coalesce(db.func.sum(trade_pnl_subq.c.trade_pnl), Decimal("0")),
                    db.func.coalesce(
                        db.func.sum(case((trade_pnl_subq.c.trade_pnl > break_even_eps, 1), else_=0)),
                        0,
                    ),
                    db.func.coalesce(
                        db.func.sum(case((db.func.abs(trade_pnl_subq.c.trade_pnl) <= break_even_eps, 1), else_=0)),
                        0,
                    ),
                ).one()

                trade_count = int(stats[0] or 0)
                total = self._to_decimal(stats[1], Decimal("0"))
                wins = int(stats[2] or 0)
                break_even = int(stats[3] or 0)
                losses = max(0, trade_count - wins - break_even)
                win_rate = ((wins + break_even) / trade_count) if trade_count else 0.0

                equity_limit = max(
                    0,
                    int(getattr(Config, "TRADING_METRICS_EQUITY_HISTORY_LIMIT", 50) or 50),
                )
                equity_history = []
                if equity_limit > 0:
                    rows = (
                        db.session.query(EquityLog.timestamp, EquityLog.equity)
                        .order_by(EquityLog.timestamp.desc())
                        .limit(equity_limit)
                        .all()
                    )
                    rows = list(reversed(rows))
                    for ts, eq in rows:
                        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
                        equity_history.append({"ts": ts_iso, "equity": float(eq or 0)})

                raw_equity_latest = equity_history[-1]["equity"] if equity_history else None
                if raw_equity_latest is None:
                    try:
                        from skills.advanced.trading.config import TradingConfig

                        equity_latest = self._to_decimal(TradingConfig.INITIAL_CAPITAL, Decimal("0"))
                    except Exception as e:
                        log.warning(f"Failed reading TradingConfig.INITIAL_CAPITAL: {e}")
                        equity_latest = Decimal("0")
                else:
                    equity_latest = self._to_decimal(raw_equity_latest, Decimal("0"))

                size_expr = db.func.coalesce(Trade.current_size, Trade.initial_size, Decimal("0"))
                open_cost = (
                    db.session.query(
                        db.func.coalesce(
                            db.func.sum(db.func.coalesce(Trade.entry_price, Decimal("0")) * size_expr),
                            Decimal("0"),
                        )
                    )
                    .filter(Trade.status == TradeStatus.OPEN.value)
                    .scalar()
                )
                open_cost_dec = self._to_decimal(open_cost, Decimal("0"))
                cash_estimate = equity_latest - open_cost_dec

                pnl_history = []
                if equity_history:
                    base = self._to_decimal(equity_history[0]["equity"], Decimal("0"))
                    pnl_history = [
                        self._to_decimal(e["equity"], Decimal("0")) - base
                        for e in equity_history
                    ]

                return {
                    "total_net_pnl": total,
                    "win_rate": win_rate,
                    "trade_count": trade_count,
                    "wins": wins,
                    "break_even": break_even,
                    "losses": losses,
                    "equity": equity_latest,
                    "cash": cash_estimate,
                    "equity_history": equity_history,
                    "pnl_history": pnl_history,
                }
            except Exception as e:
                log.error(f"DB Error (get_trading_metrics): {e}")
                return self._empty_metrics()

    @staticmethod
    def _empty_metrics() -> Dict[str, Any]:
        return {
            "total_net_pnl": Decimal("0"),
            "win_rate": 0.0,
            "trade_count": 0,
            "wins": 0,
            "break_even": 0,
            "losses": 0,
            "equity": Decimal("0"),
            "cash": Decimal("0"),
            "equity_history": [],
            "pnl_history": [],
        }


class SecurityRepository(_BaseRepository):
    def block_ip(self, ip: str, reason: str = "Suspicious Activity"):
        ip_text = str(ip or "").strip()
        if not ip_text:
            return
        with self._ctx():
            try:
                entry = BlockedIP.query.filter_by(ip_address=ip_text).first()
                if not entry:
                    entry = BlockedIP(ip_address=ip_text, reason=reason)
                    try:
                        entry.is_deleted = False
                        entry.deleted_at = None
                    except Exception:
                        pass
                    db.session.add(entry)
                else:
                    entry.reason = reason
                    try:
                        entry.is_deleted = False
                        entry.deleted_at = None
                    except Exception:
                        pass
                self._commit()
                log.info(f"IP Blocked: {ip_text}")
            except Exception as e:
                db.session.rollback()
                log.error(f"block_ip failed for {ip_text}: {e}")

    def is_ip_blocked(self, ip: str) -> bool:
        with self._ctx():
            try:
                return (
                    BlockedIP.query
                    .filter_by(ip_address=ip, is_deleted=False)
                    .first()
                    is not None
                )
            except Exception:
                # Backward compatibility for DBs without soft-delete columns.
                return BlockedIP.query.filter_by(ip_address=ip).first() is not None

    def get_trusted_devices(self):
        with self._ctx():
            return {d.ip_address: d.name for d in TrustedDevice.query.all()}

    def add_trusted_device(self, ip: str, name: str):
        ip_text = str(ip or "").strip()
        name_text = str(name or "").strip() or "trusted-device"
        if not ip_text:
            return
        with self._ctx():
            try:
                dev = TrustedDevice.query.filter_by(ip_address=ip_text).first()
                if not dev:
                    dev = TrustedDevice(ip_address=ip_text, name=name_text)
                    db.session.add(dev)
                else:
                    dev.name = name_text
                self._commit()
            except Exception as e:
                db.session.rollback()
                log.error(f"add_trusted_device failed for {ip_text}: {e}")

    def get_blocked_ips(self):
        with self._ctx():
            try:
                rows = (
                    BlockedIP.query
                    .filter_by(is_deleted=False)
                    .order_by(BlockedIP.blocked_at.desc())
                    .all()
                )
            except Exception:
                rows = BlockedIP.query.order_by(BlockedIP.blocked_at.desc()).all()
            return [{"ip_address": b.ip_address, "reason": b.reason} for b in rows]

    def unblock_ip(self, ip: str):
        ip_text = str(ip or "").strip()
        if not ip_text:
            return
        with self._ctx():
            try:
                b = BlockedIP.query.filter_by(ip_address=ip_text).first()
                if b:
                    try:
                        b.is_deleted = True
                        b.deleted_at = datetime.now(timezone.utc)
                    except Exception:
                        db.session.delete(b)
                    self._commit()
            except Exception as e:
                db.session.rollback()
                log.error(f"unblock_ip failed for {ip_text}: {e}")


class RuntimeSettingsRepository(_BaseRepository):
    def set_runtime_setting(self, key: str, value, scope: str = "trading", updated_by: str = "system"):
        key_norm = str(key or "").strip().lower()
        scope_norm = str(scope or "trading").strip().lower()
        if not key_norm:
            return False
        try:
            if isinstance(value, bool):
                value_type = "bool"
            elif isinstance(value, int):
                value_type = "int"
            elif isinstance(value, float):
                value_type = "float"
            elif isinstance(value, Decimal):
                value_type = "str"
                value = format(value, "f")
            elif isinstance(value, (dict, list)):
                value_type = "json"
            else:
                value_type = "str"
            value_raw = json.dumps(value) if value_type == "json" else str(value)
        except Exception:
            value_type = "str"
            value_raw = str(value)

        with self._ctx():
            try:
                row = RuntimeSetting.query.filter_by(scope=scope_norm, key=key_norm).first()
                if not row:
                    row = RuntimeSetting(scope=scope_norm, key=key_norm)
                    db.session.add(row)
                row.value = value_raw
                row.value_type = value_type
                row.updated_by = str(updated_by or "system")
                return self._commit()
            except Exception as e:
                db.session.rollback()
                log.error(f"set_runtime_setting failed: {e}")
                return False

    def get_runtime_settings(self, scope: str = "trading"):
        scope_norm = str(scope or "trading").strip().lower()
        out = {}
        with self._ctx():
            try:
                rows = RuntimeSetting.query.filter_by(scope=scope_norm).all()
                for row in rows:
                    raw = row.value
                    vtype = str(row.value_type or "str").lower()
                    parsed = raw
                    try:
                        if vtype == "bool":
                            parsed = str(raw).strip().lower() in {"1", "true", "yes", "on"}
                        elif vtype == "int":
                            parsed = int(raw)
                        elif vtype == "float":
                            parsed = float(raw)
                        elif vtype == "json":
                            parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                    out[row.key] = parsed
                return out
            except Exception as e:
                log.error(f"get_runtime_settings failed: {e}")
                return {}

