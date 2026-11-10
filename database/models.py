from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


db = SQLAlchemy(session_options={"expire_on_commit": False})


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False, default="")
    name = db.Column(db.String(80), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="user")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(str(password or ""))

    def check_password(self, password: str) -> bool:
        return check_password_hash(str(self.password_hash or ""), str(password or ""))


class UserSecurityProfile(db.Model):
    __tablename__ = "user_security_profiles"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=True)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    mfa_secret = db.Column(db.String(80), nullable=True)
    mfa_backup_codes = db.Column(db.Text, nullable=False, default="[]")
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class EmailVerificationToken(db.Model):
    __tablename__ = "email_verification_tokens"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class CommandLog(db.Model):
    __tablename__ = "command_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    user_id = db.Column(db.String(120), nullable=True)
    source = db.Column(db.String(50), nullable=True)
    raw_text = db.Column(db.Text, nullable=True)
    intent = db.Column(db.String(120), nullable=True, index=True)
    entities_json = db.Column(db.Text, nullable=True)


class UserActivityLog(db.Model):
    __tablename__ = "user_activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow)
    username = db.Column(db.String(120), nullable=False, index=True)
    action = db.Column(db.String(120), nullable=False)
    path = db.Column(db.String(180), nullable=True)
    method = db.Column(db.String(10), nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(255), nullable=True)


class UserSession(db.Model):
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), nullable=False, index=True)
    jti = db.Column(db.String(64), unique=True, nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    is_revoked = db.Column(db.Boolean, nullable=False, default=False, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True)


class AuditChainEntry(db.Model):
    __tablename__ = "audit_chain_entries"

    id = db.Column(db.Integer, primary_key=True)
    chain_key = db.Column(db.String(64), nullable=False, default="user_activity", index=True)
    payload_hash = db.Column(db.String(64), nullable=False)
    prev_hash = db.Column(db.String(64), nullable=False)
    entry_hash = db.Column(db.String(64), nullable=False)
    ref_id = db.Column(db.Integer, nullable=True)
    meta = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class MarketAlertRule(db.Model):
    __tablename__ = "market_alert_rules"

    id = db.Column(db.Integer, primary_key=True)
    owner_username = db.Column(db.String(120), nullable=False, index=True)
    scope = db.Column(db.String(16), nullable=False, default="user")
    name = db.Column(db.String(80), nullable=False)
    market_kind = db.Column(db.String(16), nullable=False)
    symbol = db.Column(db.String(32), nullable=False, index=True)
    condition = db.Column(db.String(24), nullable=False)
    target_price = db.Column(db.Numeric(28, 10), nullable=False)
    cooldown_sec = db.Column(db.Integer, nullable=False, default=300)
    notify_channel = db.Column(db.String(24), nullable=False, default="portal")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_triggered_at = db.Column(db.DateTime, nullable=True)
    last_trigger_price = db.Column(db.Numeric(28, 10), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class MarketAlertEvent(db.Model):
    __tablename__ = "market_alert_events"

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("market_alert_rules.id"), nullable=True)
    username = db.Column(db.String(120), nullable=False, index=True)
    market_kind = db.Column(db.String(16), nullable=False)
    symbol = db.Column(db.String(32), nullable=False, index=True)
    condition = db.Column(db.String(24), nullable=False)
    target_price = db.Column(db.Numeric(28, 10), nullable=False)
    trigger_price = db.Column(db.Numeric(28, 10), nullable=False)
    notify_channel = db.Column(db.String(24), nullable=False, default="portal")
    delivered = db.Column(db.Boolean, nullable=False, default=False)
    message = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow)


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False, default="default", index=True)
    title = db.Column(db.String(200), nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="medium")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class FocusSession(db.Model):
    __tablename__ = "focus_sessions"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Numeric(12, 4), nullable=True)
    notes = db.Column(db.Text, nullable=True)


class Trade(db.Model):
    __tablename__ = "trades"

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(32), nullable=False, index=True)
    strategy = db.Column(db.String(50), nullable=True)
    direction = db.Column(db.String(10), nullable=False, default="LONG")
    status = db.Column(db.String(20), nullable=False, default="OPEN", index=True)
    entry_time = db.Column(db.DateTime, nullable=True, default=_utcnow, index=True)
    exit_time = db.Column(db.DateTime, nullable=True)
    entry_price = db.Column(db.Numeric(28, 10), nullable=True)
    exit_price = db.Column(db.Numeric(28, 10), nullable=True)
    entry_reason = db.Column(db.String(255), nullable=True)
    sl = db.Column(db.Numeric(28, 10), nullable=True)
    tp = db.Column(db.Numeric(28, 10), nullable=True)
    trailing_stop_loss = db.Column(db.Numeric(28, 10), nullable=True)
    final_take_profit = db.Column(db.Numeric(28, 10), nullable=True)
    atr_at_entry = db.Column(db.Numeric(28, 10), nullable=True)
    breakeven_triggered = db.Column(db.Boolean, nullable=False, default=False)
    initial_size = db.Column(db.Numeric(28, 10), nullable=True)
    current_size = db.Column(db.Numeric(28, 10), nullable=True)
    pnl = db.Column(db.Numeric(28, 10), nullable=True, default=0)
    partial_tp_taken_json = db.Column(db.Text, nullable=False, default="[]")
    exchange_order_id = db.Column(db.String(128), nullable=True, index=True)
    exchange_order_status = db.Column(db.String(32), nullable=True, index=True)
    exchange_order_last_sync = db.Column(db.DateTime, nullable=True)


class TradeUpdate(db.Model):
    __tablename__ = "trade_updates"

    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.Integer, db.ForeignKey("trades.id"), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    event_type = db.Column(db.String(50), nullable=True)
    message = db.Column(db.String(255), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    pnl = db.Column(db.Numeric(28, 10), nullable=True)
    size_closed = db.Column(db.Numeric(28, 10), nullable=True)
    close_price = db.Column(db.Numeric(28, 10), nullable=True)


class ExecutionJournal(db.Model):
    __tablename__ = "execution_journal"

    id = db.Column(db.Integer, primary_key=True)
    execution_id = db.Column(db.String(128), nullable=False)
    command_id = db.Column(db.String(128), nullable=True)
    decision_id = db.Column(db.String(128), nullable=True)
    client_order_id = db.Column(db.String(128), nullable=True)
    cmd = db.Column(db.String(32), nullable=False, default="")
    status = db.Column(db.String(32), nullable=False, default="claimed")
    payload_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        db.UniqueConstraint("execution_id", name="uq_execution_journal_execution_id"),
        db.UniqueConstraint("client_order_id", name="uq_execution_journal_client_order_id"),
        db.Index("ix_execution_journal_command_id", "command_id"),
        db.Index("ix_execution_journal_decision_id", "decision_id"),
        db.Index("ix_execution_journal_status_created", "status", "created_at"),
    )


class EquityLog(db.Model):
    __tablename__ = "equity_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    equity = db.Column(db.Numeric(28, 10), nullable=False)
    cash = db.Column(db.Numeric(28, 10), nullable=False, default=0)


class BlockedIP(db.Model):
    __tablename__ = "blocked_ips"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), unique=True, nullable=False, index=True)
    reason = db.Column(db.String(200), nullable=True)
    blocked_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)


class TrustedDevice(db.Model):
    __tablename__ = "trusted_devices"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class RuntimeSetting(db.Model):
    __tablename__ = "runtime_settings"

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(64), nullable=False, default="trading", index=True)
    key = db.Column(db.String(128), nullable=False)
    value = db.Column(db.Text, nullable=False, default="")
    value_type = db.Column(db.String(24), nullable=False, default="str")
    updated_by = db.Column(db.String(120), nullable=False, default="system")
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow, index=True)

    __table_args__ = (db.UniqueConstraint("scope", "key", name="uq_runtime_scope_key"),)

