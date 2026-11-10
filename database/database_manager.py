import logging
import json
import hashlib
import secrets
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from flask import has_app_context
from sqlalchemy import inspect as sa_inspect, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from database.models import (
    db,
    CommandLog,
    ExecutionJournal,
    Trade,
    TradeUpdate,
    EquityLog,
    Task,
    FocusSession,
    User,
    UserActivityLog,
    UserSession,
    AuditChainEntry,
    MarketAlertRule,
    MarketAlertEvent,
    UserSecurityProfile,
    PasswordResetToken,
    EmailVerificationToken,
)
from database.enums import ExchangeOrderStatus, TaskStatus, TradeStatus
from database.repositories import (
    RuntimeSettingsRepository,
    SecurityRepository,
    TradingMetricsRepository,
    UserRepository,
)
from config.settings import Config

log = logging.getLogger("elara.db_manager")
_DB_WRITE_LOCK = threading.RLock()
_DB_LOCK_LOG_STATE_LOCK = threading.Lock()
_DB_LOCK_LOG_STATE = {
    "last_ts": 0.0,
    "suppressed": 0,
}

class DatabaseManager:
    """
    Kelas tunggal untuk menangani semua operasi CRUD (Create, Read, Update, Delete).
    Menggunakan context manager db.session dari Flask-SQLAlchemy.
    """
    _headless_shared_app = None
    _headless_shared_lock = threading.Lock()

    def __init__(self, app=None):
        self.app = app
        self._headless_bootstrap_attempted = False
        self._last_command_log_prune_ts = 0.0
        self.users = UserRepository(self)
        self.trading_metrics = TradingMetricsRepository(self)
        self.security = SecurityRepository(self)
        self.runtime_settings = RuntimeSettingsRepository(self)

    def shutdown(self):
        try:
            if self.app:
                with self.app.app_context():
                    db.session.remove()
                    db.engine.dispose()
                    return
            if has_app_context():
                db.session.remove()
                db.engine.dispose()
                return
        except Exception as e:
            log.warning(f"DB shutdown warning: {e}")

    def _ensure_headless_app(self):
        """
        Siapkan app context internal saat berjalan headless (mis. trading daemon).
        Aman dipanggil berulang.
        """
        if self.app:
            return True
        if has_app_context():
            return True
        if self._headless_bootstrap_attempted and not self.app:
            return False

        self._headless_bootstrap_attempted = True
        try:
            with DatabaseManager._headless_shared_lock:
                if DatabaseManager._headless_shared_app is not None:
                    self.app = DatabaseManager._headless_shared_app
                    return True

                from flask import Flask

                tmp_app = Flask(__name__)
                tmp_app.config.from_object(Config)

                # init_app aman per-instance Flask; hindari double-init pada app yang sama.
                if "sqlalchemy" not in tmp_app.extensions:
                    db.init_app(tmp_app)

                with tmp_app.app_context():
                    self._maybe_create_all()

                DatabaseManager._headless_shared_app = tmp_app
                self.app = tmp_app
                return True
        except Exception as e:
            log.error(f"Headless DB bootstrap failed: {e}")
            return False

    def ensure_tables(self):
        """
        Guarantee database tables exist even when running headless (no web Flask app).
        Safe to call multiple times.
        """
        try:
            if self.app:
                with self.app.app_context():
                    self._maybe_create_all(force=True)
                return True
            return self._ensure_headless_app()
        except Exception as e:
            log.error(f"ensure_tables failed: {e}")
            return False

    def _auto_create_enabled(self) -> bool:
        return bool(getattr(Config, "DB_AUTO_CREATE_TABLES", True))

    def _ensure_legacy_schema_compat(self):
        try:
            inspector = sa_inspect(db.engine)
            table_names = set(inspector.get_table_names())
            if "users" not in table_names:
                return
            user_columns = {str(col.get("name") or "") for col in inspector.get_columns("users")}
            if "created_at" not in user_columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN created_at TIMESTAMP"))
                    conn.execute(text("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
                log.warning("Schema heal applied: added legacy column users.created_at.")
        except Exception as e:
            log.warning(f"Legacy schema compatibility check failed: {e}")

    def _maybe_create_all(self, force: bool = False):
        if force or self._auto_create_enabled():
            db.create_all()
            self._ensure_legacy_schema_compat()

    @contextmanager
    def _ctx(self):
        if has_app_context():
            yield
            return

        if not self.app:
            self._ensure_headless_app()

        if self.app:
            with self.app.app_context():
                yield
            return

        # Last resort: tetap yield agar caller tetap bisa handle error dengan graceful.
        yield

    @contextmanager
    def trade_reconcile_lock(self):
        with _DB_WRITE_LOCK:
            yield

    @contextmanager
    def transaction(self):
        with self._ctx():
            with db.session.begin():
                yield

    def _parse_datetime(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                try:
                    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
        return None

    def _is_sqlite_backend(self) -> bool:
        uri = str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "") or "").strip().lower()
        return uri.startswith("sqlite")

    @staticmethod
    def _is_lock_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "database is locked" in msg or "database is busy" in msg or "locked" in msg

    @staticmethod
    def _is_deadlock_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        markers = (
            "deadlock detected",
            "could not serialize access",
            "serialization failure",
            "lock wait timeout exceeded",
            "could not obtain lock on row",
            "lock not available",
            "40p01",
            "40001",
            "55p03",
            "1213",
            "1205",
        )
        return any(marker in msg for marker in markers)

    @staticmethod
    def _emit_throttled_sqlite_lock_log(message: str, *, level=logging.WARNING, cooldown_sec: float = 30.0):
        try:
            cooldown = max(0.0, float(cooldown_sec))
        except Exception:
            cooldown = 30.0
        now = time.time()
        suppressed = 0
        should_emit = True
        with _DB_LOCK_LOG_STATE_LOCK:
            last_ts = float(_DB_LOCK_LOG_STATE.get("last_ts", 0.0) or 0.0)
            if cooldown > 0 and (now - last_ts) < cooldown:
                _DB_LOCK_LOG_STATE["suppressed"] = int(_DB_LOCK_LOG_STATE.get("suppressed", 0) or 0) + 1
                should_emit = False
            else:
                suppressed = int(_DB_LOCK_LOG_STATE.get("suppressed", 0) or 0)
                _DB_LOCK_LOG_STATE["suppressed"] = 0
                _DB_LOCK_LOG_STATE["last_ts"] = now

        if should_emit:
            if suppressed > 0:
                message = f"{message} (suppressed {suppressed} similar lock log(s))"
            log.log(level, message)

    @staticmethod
    def _record_db_write_lock_contention_metric():
        try:
            from utils.health import record_db_write_lock_contention
            record_db_write_lock_contention()
        except Exception:
            pass

    def _query_trade_for_update(self, trade_id: int, *, nowait: bool = False):
        query = db.session.query(Trade).filter_by(id=trade_id)
        # SQLite ignores FOR UPDATE semantics. Keep plain lock behavior there.
        if self._is_sqlite_backend():
            return query.with_for_update()
        try:
            if nowait:
                return query.with_for_update(nowait=True)
            return query.with_for_update()
        except TypeError:
            # Some SQLAlchemy/dialect combos do not accept nowait kwarg.
            return query.with_for_update()

    def _run_transaction_with_retry(self, fn, *, max_attempts=None):
        attempts_raw = max_attempts if max_attempts is not None else getattr(Config, "DB_TX_RETRY_MAX_ATTEMPTS", 3)
        try:
            attempts = int(attempts_raw)
        except Exception:
            attempts = 3
        attempts = max(1, attempts)
        try:
            backoff = float(getattr(Config, "DB_TX_RETRY_BACKOFF_SEC", 0.05))
        except Exception:
            backoff = 0.05
        backoff = max(0.01, backoff)
        with self._ctx():
            for attempt in range(1, attempts + 1):
                try:
                    with db.session.begin():
                        return fn()
                except Exception as e:
                    db.session.rollback()
                    if self._is_deadlock_error(e) and attempt < attempts:
                        delay = min(1.5, backoff * (2 ** (attempt - 1)))
                        log.warning(
                            "DB transaction deadlock/serialization retry (%s/%s) in %.2fs: %s",
                            attempt,
                            attempts,
                            delay,
                            e,
                        )
                        time.sleep(delay)
                        continue
                    raise
        return None

    def _commit_with_retry(self, max_attempts=None, backoff=None):
        """
        Commit helper:
        - Retry hanya untuk lock contention SQLite.
        - Untuk non-SQLite, fail fast agar caller tahu ada write-path issue nyata.
        """
        if max_attempts is None:
            max_attempts = getattr(Config, "DB_COMMIT_RETRY_MAX_ATTEMPTS", 3)
        try:
            max_attempts = int(max_attempts)
        except Exception:
            max_attempts = 3
        max_attempts = max(1, int(max_attempts))
        try:
            cfg_backoff = float(getattr(Config, "DB_COMMIT_RETRY_BACKOFF_SEC", 0.2))
        except Exception:
            cfg_backoff = 0.2
        cfg_backoff = max(0.0, cfg_backoff)
        try:
            cfg_backoff_max = float(getattr(Config, "DB_COMMIT_RETRY_MAX_BACKOFF_SEC", 2.0))
        except Exception:
            cfg_backoff_max = 2.0
        cfg_backoff_max = max(cfg_backoff, cfg_backoff_max)
        is_sqlite = self._is_sqlite_backend()
        lock_acquired = False
        try:
            lock_wait_sec = float(getattr(Config, "DB_COMMIT_LOCK_TIMEOUT_SEC", 0.0))
        except Exception:
            lock_wait_sec = 0.0
        lock_wait_sec = max(0.0, lock_wait_sec)
        try:
            lock_log_cooldown_sec = float(getattr(Config, "DB_COMMIT_LOCK_LOG_COOLDOWN_SEC", 30.0))
        except Exception:
            lock_log_cooldown_sec = 30.0
        lock_log_cooldown_sec = max(0.0, lock_log_cooldown_sec)

        def _calc_delay(attempt_num: int) -> float:
            if backoff is not None:
                try:
                    return max(0.0, float(backoff))
                except Exception:
                    return cfg_backoff
            return min(cfg_backoff_max, cfg_backoff * (2 ** max(0, attempt_num - 1)))

        if is_sqlite:
            for acquire_attempt in range(1, max_attempts + 1):
                try:
                    if lock_wait_sec > 0:
                        lock_acquired = _DB_WRITE_LOCK.acquire(timeout=lock_wait_sec)
                    else:
                        lock_acquired = _DB_WRITE_LOCK.acquire(blocking=False)
                except Exception as e:
                    log.error(f"SQLite commit lock acquisition failed: {e}")
                    return False

                if lock_acquired:
                    break

                self._record_db_write_lock_contention_metric()
                if acquire_attempt < max_attempts:
                    delay = _calc_delay(acquire_attempt)
                    self._emit_throttled_sqlite_lock_log(
                        "SQLite write lock busy before commit "
                        f"(attempt {acquire_attempt}/{max_attempts}, "
                        f"lock_timeout={lock_wait_sec:.2f}s). "
                        f"Retrying in {delay:.2f}s...",
                        level=logging.WARNING,
                        cooldown_sec=lock_log_cooldown_sec,
                    )
                    if delay > 0:
                        time.sleep(delay)
                    continue

                self._emit_throttled_sqlite_lock_log(
                    "SQLite commit skipped because write lock is busy "
                    f"(attempts={max_attempts}, lock_timeout={lock_wait_sec:.2f}s).",
                    level=logging.ERROR,
                    cooldown_sec=lock_log_cooldown_sec,
                )
                return False
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    db.session.commit()
                    return True
                except Exception as e:
                    db.session.rollback()
                    lock_error = self._is_lock_error(e)
                    can_retry = lock_error and is_sqlite and attempt < max_attempts
                    if can_retry:
                        delay = _calc_delay(attempt)
                        self._emit_throttled_sqlite_lock_log(
                            "SQLite commit locked "
                            f"(attempt {attempt}/{max_attempts}). "
                            f"Retrying in {delay:.2f}s...",
                            level=logging.WARNING,
                            cooldown_sec=lock_log_cooldown_sec,
                        )
                        if delay > 0:
                            time.sleep(delay)
                        continue

                    if lock_error and is_sqlite:
                        self._emit_throttled_sqlite_lock_log(
                            f"SQLite commit locked after {attempt} attempt(s): {e}",
                            level=logging.ERROR,
                            cooldown_sec=lock_log_cooldown_sec,
                        )
                    else:
                        log.error("DB commit error: %s", e)
                    return False
            return False
        finally:
            if is_sqlite and lock_acquired:
                try:
                    _DB_WRITE_LOCK.release()
                except Exception:
                    pass

    def _prune_command_logs_if_needed(self):
        max_rows = int(getattr(Config, "COMMAND_LOG_MAX_ROWS", 200000) or 0)
        if max_rows <= 0:
            return
        interval_sec = max(1, int(getattr(Config, "COMMAND_LOG_PRUNE_INTERVAL_SEC", 60)))
        now = time.time()
        if (now - self._last_command_log_prune_ts) < interval_sec:
            return
        self._last_command_log_prune_ts = now

        total_rows = int(db.session.query(db.func.count(CommandLog.id)).scalar() or 0)
        if total_rows <= max_rows:
            return

        overflow = total_rows - max_rows
        prune_batch = max(1, int(getattr(Config, "COMMAND_LOG_PRUNE_BATCH", 1000)))
        delete_count = min(overflow, prune_batch)
        ids = [
            row[0]
            for row in db.session.query(CommandLog.id)
            .order_by(CommandLog.timestamp.asc(), CommandLog.id.asc())
            .limit(delete_count)
            .all()
        ]
        if not ids:
            return
        db.session.query(CommandLog).filter(CommandLog.id.in_(ids)).delete(synchronize_session=False)

    def check_user(self, email, password):
        return self.users.check_user(email=email, password=password)

    def ensure_bootstrap_user(self):
        return self.users.ensure_bootstrap_user()

    def get_user_profile(self, user_id):
        return self.users.get_user_profile(email=str(user_id or ""))

    def get_user_role(self, email: str):
        return self.users.get_user_role(email=email)

    def list_users(self, limit: int = 200):
        return self.users.list_users(limit=limit)

    def create_user(self, email: str, password: str, name: str = "", role: str = "user"):
        return self.users.create_user(email=email, password=password, name=name, role=role)

    def update_user(self, user_id: int, *, role: str = None, name: str = None, password: str = None):
        return self.users.update_user(user_id=user_id, role=role, name=name, password=password)

    def update_user_name(self, email: str, name: str) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return False
                user = User.query.filter_by(email=email_text).first()
                if not user:
                    return False
                user.name = str(name or "").strip()[:80]
                return bool(self._commit_with_retry())
            except Exception:
                db.session.rollback()
                return False

    def set_user_password(self, email: str, new_password: str) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                password_text = str(new_password or "")
                if not email_text or not password_text:
                    return False
                user = User.query.filter_by(email=email_text).first()
                if not user:
                    return False
                user.set_password(password_text)
                return bool(self._commit_with_retry())
            except Exception:
                db.session.rollback()
                return False

    def _ensure_user_security_profile(self, email: str, *, default_verified: bool = True) -> UserSecurityProfile | None:
        email_text = str(email or "").strip().lower()[:120]
        if not email_text:
            return None
        row = UserSecurityProfile.query.filter_by(email=email_text).first()
        if row:
            return row
        row = UserSecurityProfile(
            email=email_text,
            email_verified=bool(default_verified),
            mfa_enabled=False,
            mfa_secret=None,
        )
        db.session.add(row)
        return row

    def get_user_security_profile(self, email: str) -> dict:
        if not self.app and not has_app_context():
            return {}
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return {}
                row = UserSecurityProfile.query.filter_by(email=email_text).first()
                if not row:
                    return {
                        "email": email_text,
                        "email_verified": True,
                        "mfa_enabled": False,
                        "mfa_secret_set": False,
                    }
                return {
                    "email": email_text,
                    "email_verified": bool(row.email_verified),
                    "mfa_enabled": bool(row.mfa_enabled and bool(str(row.mfa_secret or "").strip())),
                    "mfa_secret_set": bool(str(row.mfa_secret or "").strip()),
                }
            except Exception as e:
                log.warning(f"get_user_security_profile failed: {e}")
                return {}

    def is_email_verified(self, email: str) -> bool:
        profile = self.get_user_security_profile(email)
        if not profile:
            return True
        return bool(profile.get("email_verified", True))

    def set_email_verified(self, email: str, verified: bool) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return False
                row = self._ensure_user_security_profile(email_text, default_verified=bool(verified))
                if not row:
                    return False
                row.email_verified = bool(verified)
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"set_email_verified failed: {e}")
                db.session.rollback()
                return False

    def set_user_mfa_secret(self, email: str, secret: str, *, enabled: bool = False) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                secret_text = str(secret or "").strip()[:80]
                if not email_text or not secret_text:
                    return False
                row = self._ensure_user_security_profile(email_text, default_verified=True)
                if not row:
                    return False
                row.mfa_secret = secret_text
                row.mfa_enabled = bool(enabled)
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"set_user_mfa_secret failed: {e}")
                db.session.rollback()
                return False

    def enable_user_mfa(self, email: str) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return False
                row = self._ensure_user_security_profile(email_text, default_verified=True)
                if not row or not str(row.mfa_secret or "").strip():
                    return False
                row.mfa_enabled = True
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"enable_user_mfa failed: {e}")
                db.session.rollback()
                return False

    def disable_user_mfa(self, email: str) -> bool:
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return False
                row = UserSecurityProfile.query.filter_by(email=email_text).first()
                if not row:
                    return False
                row.mfa_enabled = False
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"disable_user_mfa failed: {e}")
                db.session.rollback()
                return False

    def get_user_mfa_secret(self, email: str) -> str:
        if not self.app and not has_app_context():
            return ""
        with self._ctx():
            try:
                email_text = str(email or "").strip().lower()[:120]
                if not email_text:
                    return ""
                row = UserSecurityProfile.query.filter_by(email=email_text).first()
                return str((row.mfa_secret if row else "") or "").strip()
            except Exception:
                return ""

    def is_user_mfa_enabled(self, email: str) -> bool:
        profile = self.get_user_security_profile(email)
        return bool(profile.get("mfa_enabled", False))

    def _issue_token_for_email(self, model_cls, *, email: str, ttl_minutes: int) -> str:
        email_text = str(email or "").strip().lower()[:120]
        if not email_text:
            return ""
        user = User.query.filter_by(email=email_text).first()
        if not user:
            return ""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=max(1, int(ttl_minutes or 1)))
        try:
            model_cls.query.filter(
                model_cls.email == email_text,
                model_cls.used_at.is_(None),
            ).update({"used_at": now}, synchronize_session=False)
        except Exception:
            pass
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._sha256_text(raw_token)
        row = model_cls(
            email=email_text,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
        )
        db.session.add(row)
        if not self._commit_with_retry():
            return ""
        return raw_token

    def _consume_email_token(self, model_cls, token: str) -> str:
        token_hash = self._sha256_text(str(token or "").strip())
        if not token_hash:
            return ""
        now = datetime.now(timezone.utc)
        row = (
            model_cls.query.filter(
                model_cls.token_hash == token_hash,
                model_cls.used_at.is_(None),
                model_cls.expires_at >= now,
            )
            .order_by(model_cls.id.desc())
            .first()
        )
        if not row:
            return ""
        row.used_at = now
        if not self._commit_with_retry():
            return ""
        return str(row.email or "").strip().lower()

    def issue_password_reset_token(self, email: str, ttl_minutes: int = 30) -> str:
        if not self.app and not has_app_context():
            return ""
        with self._ctx():
            try:
                return self._issue_token_for_email(PasswordResetToken, email=email, ttl_minutes=ttl_minutes)
            except Exception as e:
                log.warning(f"issue_password_reset_token failed: {e}")
                db.session.rollback()
                return ""

    def reset_password_with_token_and_get_email(self, token: str, new_password: str) -> str:
        if not self.app and not has_app_context():
            return ""
        with self._ctx():
            try:
                email = self._consume_email_token(PasswordResetToken, token)
                if not email:
                    return ""
                user = User.query.filter_by(email=email).first()
                if not user:
                    return ""
                user.set_password(str(new_password or ""))
                if not self._commit_with_retry():
                    return ""
                return email
            except Exception as e:
                log.warning(f"reset_password_with_token failed: {e}")
                db.session.rollback()
                return ""

    def reset_password_with_token(self, token: str, new_password: str) -> bool:
        try:
            return bool(self.reset_password_with_token_and_get_email(token, new_password))
        except Exception:
            return False

    def issue_email_verification_token(self, email: str, ttl_minutes: int = 1440) -> str:
        if not self.app and not has_app_context():
            return ""
        with self._ctx():
            try:
                return self._issue_token_for_email(EmailVerificationToken, email=email, ttl_minutes=ttl_minutes)
            except Exception as e:
                log.warning(f"issue_email_verification_token failed: {e}")
                db.session.rollback()
                return ""

    def verify_email_with_token(self, token: str) -> str:
        if not self.app and not has_app_context():
            return ""
        with self._ctx():
            try:
                email = self._consume_email_token(EmailVerificationToken, token)
                if not email:
                    return ""
                row = self._ensure_user_security_profile(email, default_verified=True)
                if not row:
                    return ""
                row.email_verified = True
                if not self._commit_with_retry():
                    return ""
                return email
            except Exception as e:
                log.warning(f"verify_email_with_token failed: {e}")
                db.session.rollback()
                return ""

    def log_command(self, source, intent, text, entities=None, user_id="default"):
        with self._ctx():
            try:
                max_text_chars = max(64, int(getattr(Config, "COMMAND_LOG_MAX_TEXT_CHARS", 4000) or 4000))
                raw_text = str(text or "")
                if len(raw_text) > max_text_chars:
                    trim = len(raw_text) - max_text_chars
                    raw_text = raw_text[:max_text_chars] + f"... [truncated {trim} chars]"
                entry = CommandLog(
                    source=source,
                    user_id=str(user_id),
                    intent=intent,
                    raw_text=raw_text,
                    entities_json=json.dumps(entities or [])
                )
                db.session.add(entry)
                self._prune_command_logs_if_needed()
                self._commit_with_retry()
            except Exception as e:
                log.warning(f"Gagal logging command: {e}")
                db.session.rollback()

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _json_canonical(payload: dict) -> str:
        try:
            return json.dumps(payload or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except Exception:
            return "{}"

    def _append_activity_audit_chain(self, activity_row: UserActivityLog):
        chain_key = "user_activity"
        last = (
            AuditChainEntry.query.filter(AuditChainEntry.chain_key == chain_key)
            .order_by(AuditChainEntry.id.desc())
            .first()
        )
        prev_hash = str((last.entry_hash if last else "") or "").strip() or "GENESIS"
        payload = {
            "activity_id": int(activity_row.id),
            "timestamp": activity_row.timestamp.isoformat() if activity_row.timestamp else None,
            "username": str(activity_row.username or ""),
            "action": str(activity_row.action or ""),
            "path": str(activity_row.path or ""),
            "method": str(activity_row.method or ""),
            "status_code": int(activity_row.status_code) if activity_row.status_code is not None else None,
            "details": str(activity_row.details or ""),
        }
        payload_hash = self._sha256_text(self._json_canonical(payload))
        entry_hash = self._sha256_text(f"{prev_hash}|{payload_hash}|{chain_key}")
        db.session.add(
            AuditChainEntry(
                chain_key=chain_key,
                payload_hash=payload_hash,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                ref_id=int(activity_row.id),
                meta=str(activity_row.action or "")[:120],
            )
        )

    @staticmethod
    def _serialize_user_session_row(row: UserSession) -> dict:
        return {
            "id": int(row.id),
            "username": str(row.username or ""),
            "jti": str(row.jti or ""),
            "issued_at": row.issued_at.isoformat() if row.issued_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "ip_address": str(row.ip_address or ""),
            "user_agent": str(row.user_agent or ""),
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            "is_revoked": bool(row.is_revoked),
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }

    def register_user_session(
        self,
        *,
        username: str,
        jti: str,
        exp_ts: int | None = None,
        ip_address: str = "",
        user_agent: str = "",
    ) -> bool:
        with self._ctx():
            try:
                actor = str(username or "").strip()[:120] or "anonymous"
                jti_text = str(jti or "").strip()[:64]
                if not jti_text:
                    return False
                now = datetime.now(timezone.utc)
                expires_at = None
                if exp_ts is not None:
                    try:
                        expires_at = datetime.fromtimestamp(int(exp_ts), tz=timezone.utc)
                    except Exception:
                        expires_at = None
                row = UserSession.query.filter_by(jti=jti_text).first()
                if not row:
                    row = UserSession(jti=jti_text)
                    db.session.add(row)
                row.username = actor
                row.issued_at = row.issued_at or now
                row.expires_at = expires_at
                row.ip_address = str(ip_address or "").strip()[:64]
                row.user_agent = str(user_agent or "").strip()[:255]
                row.last_seen = now
                row.is_revoked = False
                row.revoked_at = None
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"register_user_session failed: {e}")
                db.session.rollback()
                return False

    def touch_user_session(self, *, jti: str, ip_address: str = "", user_agent: str = "") -> bool:
        with self._ctx():
            try:
                jti_text = str(jti or "").strip()[:64]
                if not jti_text:
                    return False
                row = UserSession.query.filter_by(jti=jti_text).first()
                if not row:
                    return False
                row.last_seen = datetime.now(timezone.utc)
                if ip_address:
                    row.ip_address = str(ip_address).strip()[:64]
                if user_agent:
                    row.user_agent = str(user_agent).strip()[:255]
                return bool(self._commit_with_retry())
            except Exception:
                db.session.rollback()
                return False

    def is_user_session_revoked(self, jti: str) -> bool:
        if not self.app and not has_app_context():
            return False
        with self._ctx():
            try:
                row = UserSession.query.filter_by(jti=str(jti or "").strip()[:64]).first()
                return bool(row and row.is_revoked)
            except Exception:
                return False

    def query_user_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        username: str = "",
        active_only: bool = True,
    ) -> dict:
        empty = {"items": [], "total": 0, "limit": 0, "offset": 0, "has_more": False}
        if not self.app and not has_app_context():
            return empty
        with self._ctx():
            try:
                lim = max(1, min(int(limit), 500))
            except Exception:
                lim = 100
            try:
                off = max(0, int(offset))
            except Exception:
                off = 0
            try:
                q = UserSession.query
                username_filter = str(username or "").strip()
                if username_filter:
                    q = q.filter(UserSession.username.ilike(f"%{username_filter}%"))
                if active_only:
                    q = q.filter(UserSession.is_revoked.is_(False))
                total = int(q.count() or 0)
                rows = (
                    q.order_by(UserSession.last_seen.desc(), UserSession.id.desc())
                    .offset(off)
                    .limit(lim)
                    .all()
                )
                items = [self._serialize_user_session_row(row) for row in rows]
                return {
                    "items": items,
                    "total": total,
                    "limit": lim,
                    "offset": off,
                    "has_more": (off + len(items)) < total,
                }
            except Exception as e:
                log.warning(f"query_user_sessions failed: {e}")
                return {"items": [], "total": 0, "limit": lim, "offset": off, "has_more": False}

    def revoke_user_sessions(self, *, username: str) -> list[dict]:
        with self._ctx():
            try:
                actor = str(username or "").strip()[:120]
                if not actor:
                    return []
                actor_lower = actor.lower()
                now = datetime.now(timezone.utc)
                rows = (
                    UserSession.query.filter(
                        db.func.lower(UserSession.username) == actor_lower,
                        UserSession.is_revoked.is_(False),
                    )
                    .order_by(UserSession.id.desc())
                    .all()
                )
                revoked = []
                for row in rows:
                    row.is_revoked = True
                    row.revoked_at = now
                    exp_ts = int(row.expires_at.timestamp()) if row.expires_at else 0
                    revoked.append(
                        {
                            "jti": str(row.jti or ""),
                            "exp_ts": exp_ts,
                            "session_id": int(row.id),
                            "username": actor,
                        }
                    )
                if rows:
                    self._commit_with_retry()
                return revoked
            except Exception as e:
                log.warning(f"revoke_user_sessions failed: {e}")
                db.session.rollback()
                return []

    def revoke_user_session_jti(self, *, jti: str) -> bool:
        with self._ctx():
            try:
                jti_text = str(jti or "").strip()[:64]
                if not jti_text:
                    return False
                row = UserSession.query.filter_by(jti=jti_text).first()
                if not row:
                    return False
                row.is_revoked = True
                row.revoked_at = datetime.now(timezone.utc)
                return bool(self._commit_with_retry())
            except Exception:
                db.session.rollback()
                return False

    def verify_activity_audit_chain(self, *, limit: int = 5000) -> dict:
        out = {
            "ok": True,
            "checked": 0,
            "broken_at_id": None,
            "message": "ok",
            "latest_hash": "",
        }
        if not self.app and not has_app_context():
            out["message"] = "no_app_context"
            return out
        with self._ctx():
            try:
                lim = max(1, min(int(limit), 20000))
            except Exception:
                lim = 5000
            try:
                rows = (
                    AuditChainEntry.query.filter(AuditChainEntry.chain_key == "user_activity")
                    .order_by(AuditChainEntry.id.asc())
                    .limit(lim)
                    .all()
                )
                prev = "GENESIS"
                checked = 0
                for row in rows:
                    expected = self._sha256_text(f"{prev}|{row.payload_hash}|user_activity")
                    if str(row.entry_hash or "") != expected:
                        return {
                            "ok": False,
                            "checked": checked,
                            "broken_at_id": int(row.id),
                            "message": "hash_mismatch",
                            "latest_hash": str(prev or ""),
                        }
                    prev = str(row.entry_hash or "")
                    checked += 1
                out["checked"] = checked
                out["latest_hash"] = str(prev if checked > 0 else "")
                return out
            except Exception as e:
                out["ok"] = False
                out["message"] = f"verify_failed:{e}"
                return out

    def log_user_activity(
        self,
        *,
        username: str,
        action: str,
        path: str = "",
        method: str = "",
        status_code: int | None = None,
        details: str = "",
    ) -> bool:
        with self._ctx():
            try:
                actor = str(username or "").strip()[:120] or "anonymous"
                action_text = str(action or "").strip()[:120]
                if not action_text:
                    return False
                path_text = str(path or "").strip()[:180]
                method_text = str(method or "").strip().upper()[:10]
                detail_text = str(details or "").strip()[:255]

                code = None
                if status_code is not None:
                    try:
                        code = int(status_code)
                    except Exception:
                        code = None

                entry = UserActivityLog(
                    username=actor,
                    action=action_text,
                    path=path_text,
                    method=method_text,
                    status_code=code,
                    details=detail_text,
                )
                db.session.add(entry)
                try:
                    db.session.flush()
                    self._append_activity_audit_chain(entry)
                except Exception as chain_err:
                    log.warning(f"activity audit chain append failed: {chain_err}")
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"log_user_activity failed: {e}")
                db.session.rollback()
                return False

    @staticmethod
    def _serialize_user_activity_row(row: UserActivityLog) -> dict:
        return {
            "id": int(row.id),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "username": str(row.username or ""),
            "action": str(row.action or ""),
            "path": str(row.path or ""),
            "method": str(row.method or ""),
            "status_code": int(row.status_code) if row.status_code is not None else None,
            "details": str(row.details or ""),
        }

    def query_user_activities(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        username: str = "",
        action: str = "",
        status_code: int | None = None,
        status_group: str = "",
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> dict:
        if not self.app and not has_app_context():
            return {"items": [], "total": 0, "limit": 0, "offset": 0, "has_more": False}
        with self._ctx():
            try:
                lim = max(1, min(int(limit), 1000))
            except Exception:
                lim = 50
            try:
                off = max(0, int(offset))
            except Exception:
                off = 0

            username_filter = str(username or "").strip()
            action_filter = str(action or "").strip()
            status_group_filter = str(status_group or "").strip().lower()
            from_dt = self._parse_datetime(date_from) if date_from else None
            to_dt = self._parse_datetime(date_to) if date_to else None

            try:
                query = UserActivityLog.query
                if username_filter:
                    query = query.filter(UserActivityLog.username.ilike(f"%{username_filter}%"))
                if action_filter:
                    query = query.filter(UserActivityLog.action.ilike(f"%{action_filter}%"))
                if status_code is not None:
                    try:
                        query = query.filter(UserActivityLog.status_code == int(status_code))
                    except Exception:
                        pass
                if status_group_filter in {"2xx", "3xx", "4xx", "5xx"}:
                    base = int(status_group_filter[0]) * 100
                    query = query.filter(
                        UserActivityLog.status_code >= base,
                        UserActivityLog.status_code < (base + 100),
                    )
                if from_dt is not None:
                    query = query.filter(UserActivityLog.timestamp >= from_dt)
                if to_dt is not None:
                    query = query.filter(UserActivityLog.timestamp <= to_dt)

                total = int(query.count() or 0)
                rows = (
                    query.order_by(UserActivityLog.timestamp.desc(), UserActivityLog.id.desc())
                    .offset(off)
                    .limit(lim)
                    .all()
                )
                items = [self._serialize_user_activity_row(row) for row in rows]
                return {
                    "items": items,
                    "total": total,
                    "limit": lim,
                    "offset": off,
                    "has_more": (off + len(items)) < total,
                }
            except Exception as e:
                log.warning(f"query_user_activities failed: {e}")
                return {"items": [], "total": 0, "limit": lim, "offset": off, "has_more": False}

    def summarize_user_activities(
        self,
        *,
        username: str = "",
        action: str = "",
        status_code: int | None = None,
        status_group: str = "",
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
        top_n: int = 5,
    ) -> dict:
        empty = {
            "total_rows": 0,
            "status_mix": {"2xx": 0, "4xx": 0, "5xx": 0, "other": 0},
            "risk_mix": {"critical": 0, "warning": 0, "sensitive": 0},
            "top_users": [],
            "top_actions": [],
            "window": {"from": None, "to": None},
            "trend": {"unit": "hour", "points": [], "partial": False, "source_rows": 0},
            "anomalies": [],
            "anomaly_window": {"from": None, "to": None, "minutes": 0},
        }
        if not self.app and not has_app_context():
            return empty
        with self._ctx():
            try:
                top_limit = max(1, min(int(top_n), 12))
            except Exception:
                top_limit = 5

            username_filter = str(username or "").strip()
            action_filter = str(action or "").strip()
            status_group_filter = str(status_group or "").strip().lower()
            from_dt = self._parse_datetime(date_from) if date_from else None
            to_dt = self._parse_datetime(date_to) if date_to else None

            try:
                query = UserActivityLog.query
                if username_filter:
                    query = query.filter(UserActivityLog.username.ilike(f"%{username_filter}%"))
                if action_filter:
                    query = query.filter(UserActivityLog.action.ilike(f"%{action_filter}%"))
                if status_code is not None:
                    try:
                        query = query.filter(UserActivityLog.status_code == int(status_code))
                    except Exception:
                        pass
                if status_group_filter in {"2xx", "3xx", "4xx", "5xx"}:
                    base = int(status_group_filter[0]) * 100
                    query = query.filter(
                        UserActivityLog.status_code >= base,
                        UserActivityLog.status_code < (base + 100),
                    )
                if from_dt is not None:
                    query = query.filter(UserActivityLog.timestamp >= from_dt)
                if to_dt is not None:
                    query = query.filter(UserActivityLog.timestamp <= to_dt)

                total_rows = int(query.count() or 0)
                status_2xx = int(
                    query.filter(UserActivityLog.status_code >= 200, UserActivityLog.status_code < 300).count() or 0
                )
                status_4xx = int(
                    query.filter(UserActivityLog.status_code >= 400, UserActivityLog.status_code < 500).count() or 0
                )
                status_5xx = int(
                    query.filter(UserActivityLog.status_code >= 500, UserActivityLog.status_code < 600).count() or 0
                )
                status_other = max(0, total_rows - status_2xx - status_4xx - status_5xx)

                warning_rows = status_4xx
                critical_rows = int(
                    query.filter(
                        or_(
                            UserActivityLog.status_code >= 500,
                            UserActivityLog.action.ilike("%failed%"),
                            UserActivityLog.action.ilike("%rejected%"),
                        )
                    ).count()
                    or 0
                )
                sensitive_rows = int(
                    query.filter(
                        or_(
                            UserActivityLog.action.ilike("%panic%"),
                            UserActivityLog.action.ilike("%selfops%"),
                            UserActivityLog.action.ilike("%admin_create_user%"),
                            UserActivityLog.action.ilike("%admin_update_user%"),
                            UserActivityLog.action.ilike("%trading_command%"),
                        )
                    ).count()
                    or 0
                )

                count_expr = db.func.count(UserActivityLog.id)
                top_user_rows = (
                    query.with_entities(UserActivityLog.username, count_expr.label("count"))
                    .group_by(UserActivityLog.username)
                    .order_by(count_expr.desc(), UserActivityLog.username.asc())
                    .limit(top_limit)
                    .all()
                )
                top_action_rows = (
                    query.with_entities(UserActivityLog.action, count_expr.label("count"))
                    .group_by(UserActivityLog.action)
                    .order_by(count_expr.desc(), UserActivityLog.action.asc())
                    .limit(top_limit)
                    .all()
                )

                top_users = [
                    {
                        "username": str((row[0] or "").strip() or "anonymous"),
                        "count": int(row[1] or 0),
                    }
                    for row in top_user_rows
                ]
                top_actions = [
                    {
                        "action": str((row[0] or "").strip() or "-"),
                        "count": int(row[1] or 0),
                    }
                    for row in top_action_rows
                ]

                trend_scan_limit = 50000
                trend_rows = (
                    query.with_entities(UserActivityLog.timestamp)
                    .order_by(UserActivityLog.timestamp.desc(), UserActivityLog.id.desc())
                    .limit(trend_scan_limit)
                    .all()
                )
                trend_partial = total_rows > len(trend_rows)
                trend_source_rows = len(trend_rows)

                trend_timestamps = []
                for row in trend_rows:
                    ts = None
                    try:
                        ts = row[0]
                    except Exception:
                        ts = getattr(row, "timestamp", None)
                    if not isinstance(ts, datetime):
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                    trend_timestamps.append(ts)

                trend_unit = "hour"
                if from_dt and to_dt:
                    span_sec = abs((to_dt - from_dt).total_seconds())
                    if span_sec > (14 * 24 * 60 * 60):
                        trend_unit = "day"
                elif len(trend_timestamps) >= 2:
                    span_sec = abs((max(trend_timestamps) - min(trend_timestamps)).total_seconds())
                    if span_sec > (14 * 24 * 60 * 60):
                        trend_unit = "day"

                def _bucket_floor(ts_value: datetime, unit: str) -> datetime:
                    if unit == "day":
                        return ts_value.replace(hour=0, minute=0, second=0, microsecond=0)
                    return ts_value.replace(minute=0, second=0, microsecond=0)

                step = timedelta(days=1) if trend_unit == "day" else timedelta(hours=1)
                bucket_counts = {}
                for ts in trend_timestamps:
                    bucket = _bucket_floor(ts, trend_unit)
                    bucket_counts[bucket] = int(bucket_counts.get(bucket, 0) + 1)

                trend_points = []
                trend_display_from = None
                trend_display_to = None
                if bucket_counts:
                    start_bucket = _bucket_floor(from_dt, trend_unit) if from_dt else min(bucket_counts.keys())
                    end_bucket = _bucket_floor(to_dt, trend_unit) if to_dt else max(bucket_counts.keys())
                    if end_bucket < start_bucket:
                        start_bucket, end_bucket = end_bucket, start_bucket

                    max_points = 120
                    points_count = int(((end_bucket - start_bucket).total_seconds() / step.total_seconds()) + 1)
                    if points_count > max_points:
                        start_bucket = end_bucket - (step * (max_points - 1))
                        points_count = max_points

                    cursor = start_bucket
                    for _ in range(max(0, points_count)):
                        trend_points.append(
                            {
                                "bucket": cursor.isoformat(),
                                "count": int(bucket_counts.get(cursor, 0)),
                            }
                        )
                        cursor += step

                    if trend_points:
                        trend_display_from = trend_points[0]["bucket"]
                        trend_display_to = trend_points[-1]["bucket"]

                anomaly_window_min = max(1, int(getattr(Config, "WEB_ACTIVITY_ANOMALY_WINDOW_MIN", 5) or 5))
                anomaly_fail_threshold = max(3, int(getattr(Config, "WEB_ACTIVITY_ANOMALY_FAIL_THRESHOLD", 8) or 8))
                anomaly_sensitive_threshold = max(
                    2,
                    int(getattr(Config, "WEB_ACTIVITY_ANOMALY_SENSITIVE_SPAM_THRESHOLD", 6) or 6),
                )
                anomaly_5xx_threshold = max(3, int(getattr(Config, "WEB_ACTIVITY_ANOMALY_5XX_SPIKE_THRESHOLD", 10) or 10))

                now = datetime.now(timezone.utc)
                anomaly_from = from_dt if from_dt else (now - timedelta(minutes=anomaly_window_min))
                anomaly_to = to_dt if to_dt else now
                if anomaly_to < anomaly_from:
                    anomaly_from, anomaly_to = anomaly_to, anomaly_from

                anomaly_scan_limit = 4000
                anomaly_rows = (
                    query.filter(UserActivityLog.timestamp >= anomaly_from, UserActivityLog.timestamp <= anomaly_to)
                    .with_entities(
                        UserActivityLog.username,
                        UserActivityLog.action,
                        UserActivityLog.status_code,
                        UserActivityLog.method,
                        UserActivityLog.path,
                    )
                    .order_by(UserActivityLog.timestamp.desc(), UserActivityLog.id.desc())
                    .limit(anomaly_scan_limit)
                    .all()
                )

                failed_by_user = {}
                sensitive_by_user = {}
                sensitive_endpoint_by_user = {}
                total_5xx = 0
                for row in anomaly_rows:
                    actor = str((row[0] or "").strip() or "anonymous")
                    action_text = str((row[1] or "")).lower()
                    status = int(row[2] or 0) if row[2] is not None else 0
                    method_text = str((row[3] or "")).upper()
                    path_text = str((row[4] or ""))

                    is_sensitive = (
                        "panic" in action_text
                        or "selfops" in action_text
                        or "admin_create_user" in action_text
                        or "admin_update_user" in action_text
                        or "trading_command" in action_text
                    )
                    is_failed = ("failed" in action_text) or ("rejected" in action_text) or status >= 400

                    if status >= 500:
                        total_5xx += 1
                    if is_failed:
                        failed_by_user[actor] = int(failed_by_user.get(actor, 0) + 1)
                    if is_sensitive:
                        sensitive_by_user[actor] = int(sensitive_by_user.get(actor, 0) + 1)
                        key = (actor, f"{method_text} {path_text}".strip())
                        sensitive_endpoint_by_user[key] = int(sensitive_endpoint_by_user.get(key, 0) + 1)

                anomalies = []
                for actor, count in sorted(failed_by_user.items(), key=lambda item: item[1], reverse=True):
                    if count < anomaly_fail_threshold:
                        continue
                    anomalies.append(
                        {
                            "type": "failed_rejected_burst",
                            "severity": "critical" if count >= (anomaly_fail_threshold * 2) else "warning",
                            "username": actor,
                            "count": int(count),
                            "title": "Burst Failed/Rejected",
                            "message": f"{actor} has {count} failed/rejected requests in window",
                        }
                    )
                for key, count in sorted(sensitive_endpoint_by_user.items(), key=lambda item: item[1], reverse=True):
                    if count < anomaly_sensitive_threshold:
                        continue
                    actor, endpoint = key
                    anomalies.append(
                        {
                            "type": "sensitive_endpoint_spam",
                            "severity": "critical",
                            "username": actor,
                            "count": int(count),
                            "title": "Sensitive Endpoint Spam",
                            "message": f"{actor} hit {endpoint or '-'} {count} times in window",
                        }
                    )
                if total_5xx >= anomaly_5xx_threshold:
                    anomalies.append(
                        {
                            "type": "server_error_spike",
                            "severity": "critical",
                            "username": "",
                            "count": int(total_5xx),
                            "title": "5xx Spike",
                            "message": f"Detected {total_5xx} server errors in anomaly window",
                        }
                    )

                severity_rank = {"critical": 3, "warning": 2, "info": 1}
                anomalies = sorted(
                    anomalies,
                    key=lambda item: (severity_rank.get(str(item.get("severity", "")).lower(), 0), int(item.get("count") or 0)),
                    reverse=True,
                )[:12]

                return {
                    "total_rows": total_rows,
                    "status_mix": {"2xx": status_2xx, "4xx": status_4xx, "5xx": status_5xx, "other": status_other},
                    "risk_mix": {"critical": critical_rows, "warning": warning_rows, "sensitive": sensitive_rows},
                    "top_users": top_users,
                    "top_actions": top_actions,
                    "window": {
                        "from": from_dt.isoformat() if from_dt else None,
                        "to": to_dt.isoformat() if to_dt else None,
                    },
                    "trend": {
                        "unit": trend_unit,
                        "points": trend_points,
                        "partial": bool(trend_partial),
                        "source_rows": int(trend_source_rows),
                        "from": trend_display_from,
                        "to": trend_display_to,
                    },
                    "anomalies": anomalies,
                    "anomaly_window": {
                        "from": anomaly_from.isoformat() if anomaly_from else None,
                        "to": anomaly_to.isoformat() if anomaly_to else None,
                        "minutes": anomaly_window_min,
                    },
                }
            except Exception as e:
                log.warning(f"summarize_user_activities failed: {e}")
                return empty

    def get_recent_user_activities(self, limit: int = 50):
        result = self.query_user_activities(limit=limit, offset=0)
        return list(result.get("items") or [])

    @staticmethod
    def _serialize_market_alert_rule_row(row: MarketAlertRule) -> dict:
        return {
            "id": int(row.id),
            "owner_username": str(row.owner_username or ""),
            "scope": str(row.scope or "user"),
            "name": str(row.name or ""),
            "market_kind": str(row.market_kind or "crypto"),
            "symbol": str(row.symbol or ""),
            "condition": str(row.condition or "cross_above"),
            "target_price": float(row.target_price or 0),
            "cooldown_sec": int(row.cooldown_sec or 300),
            "notify_channel": str(row.notify_channel or "portal"),
            "is_active": bool(row.is_active),
            "last_triggered_at": row.last_triggered_at.isoformat() if row.last_triggered_at else None,
            "last_trigger_price": float(row.last_trigger_price or 0) if row.last_trigger_price is not None else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_market_alert_event_row(row: MarketAlertEvent) -> dict:
        return {
            "id": int(row.id),
            "rule_id": int(row.rule_id) if row.rule_id is not None else None,
            "username": str(row.username or ""),
            "market_kind": str(row.market_kind or ""),
            "symbol": str(row.symbol or ""),
            "condition": str(row.condition or ""),
            "target_price": float(row.target_price or 0),
            "trigger_price": float(row.trigger_price or 0),
            "notify_channel": str(row.notify_channel or "portal"),
            "delivered": bool(row.delivered),
            "message": str(row.message or ""),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        }

    def create_market_alert_rule(
        self,
        *,
        owner_username: str,
        name: str,
        market_kind: str,
        symbol: str,
        condition: str,
        target_price,
        cooldown_sec: int = 300,
        notify_channel: str = "portal",
        scope: str = "user",
        is_active: bool = True,
    ) -> dict | None:
        with self._ctx():
            try:
                owner = str(owner_username or "").strip().lower()[:120] or "anonymous"
                rule_name = str(name or "").strip()[:80] or "Price Alert"
                kind = str(market_kind or "").strip().lower()
                cond = str(condition or "").strip().lower()
                sym = str(symbol or "").strip().upper()[:32]
                chan = str(notify_channel or "").strip().lower()
                scope_text = str(scope or "").strip().lower()
                try:
                    target_val = float(target_price)
                except Exception:
                    target_val = 0.0
                try:
                    cooldown = int(cooldown_sec)
                except Exception:
                    cooldown = 300

                if kind not in {"stock", "crypto"}:
                    return None
                if cond not in {"cross_above", "cross_below"}:
                    return None
                if chan not in {"portal", "telegram", "both"}:
                    chan = "portal"
                if scope_text not in {"user", "global"}:
                    scope_text = "user"
                if not sym or target_val <= 0:
                    return None

                row = MarketAlertRule(
                    owner_username=owner,
                    scope=scope_text,
                    name=rule_name,
                    market_kind=kind,
                    symbol=sym,
                    condition=cond,
                    target_price=Decimal(str(target_val)),
                    cooldown_sec=max(30, min(cooldown, 7 * 24 * 60 * 60)),
                    notify_channel=chan,
                    is_active=bool(is_active),
                )
                db.session.add(row)
                if not self._commit_with_retry():
                    return None
                return self._serialize_market_alert_rule_row(row)
            except Exception as e:
                log.warning(f"create_market_alert_rule failed: {e}")
                db.session.rollback()
                return None

    def update_market_alert_rule(
        self,
        rule_id: int,
        *,
        actor_username: str,
        actor_is_admin: bool = False,
        updates: dict | None = None,
    ) -> dict | None:
        with self._ctx():
            try:
                rid = int(rule_id)
                actor = str(actor_username or "").strip().lower()
                row = MarketAlertRule.query.filter_by(id=rid).first()
                if not row:
                    return None
                owner = str(row.owner_username or "").strip().lower()
                if not actor_is_admin and (not actor or owner != actor):
                    return None
                data = updates if isinstance(updates, dict) else {}

                if "name" in data:
                    row.name = str(data.get("name") or "").strip()[:80] or row.name
                if "symbol" in data:
                    sym = str(data.get("symbol") or "").strip().upper()[:32]
                    if sym:
                        row.symbol = sym
                if "market_kind" in data:
                    kind = str(data.get("market_kind") or "").strip().lower()
                    if kind in {"stock", "crypto"}:
                        row.market_kind = kind
                if "condition" in data:
                    cond = str(data.get("condition") or "").strip().lower()
                    if cond in {"cross_above", "cross_below"}:
                        row.condition = cond
                if "target_price" in data:
                    try:
                        target_val = float(data.get("target_price"))
                    except Exception:
                        target_val = 0.0
                    if target_val > 0:
                        row.target_price = Decimal(str(target_val))
                if "cooldown_sec" in data:
                    try:
                        cooldown = int(data.get("cooldown_sec"))
                    except Exception:
                        cooldown = int(row.cooldown_sec or 300)
                    row.cooldown_sec = max(30, min(cooldown, 7 * 24 * 60 * 60))
                if "notify_channel" in data:
                    chan = str(data.get("notify_channel") or "").strip().lower()
                    if chan in {"portal", "telegram", "both"}:
                        row.notify_channel = chan
                if "is_active" in data:
                    row.is_active = bool(data.get("is_active"))
                if actor_is_admin and "scope" in data:
                    scope_text = str(data.get("scope") or "").strip().lower()
                    if scope_text in {"user", "global"}:
                        row.scope = scope_text
                if actor_is_admin and "owner_username" in data:
                    owner_new = str(data.get("owner_username") or "").strip().lower()[:120]
                    if owner_new:
                        row.owner_username = owner_new
                if not self._commit_with_retry():
                    return None
                return self._serialize_market_alert_rule_row(row)
            except Exception as e:
                log.warning(f"update_market_alert_rule failed: {e}")
                db.session.rollback()
                return None

    def delete_market_alert_rule(self, rule_id: int, *, actor_username: str, actor_is_admin: bool = False) -> bool:
        with self._ctx():
            try:
                rid = int(rule_id)
                actor = str(actor_username or "").strip().lower()
                row = MarketAlertRule.query.filter_by(id=rid).first()
                if not row:
                    return False
                owner = str(row.owner_username or "").strip().lower()
                if not actor_is_admin and (not actor or owner != actor):
                    return False
                db.session.delete(row)
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"delete_market_alert_rule failed: {e}")
                db.session.rollback()
                return False

    def query_market_alert_rules(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        owner_username: str = "",
        include_global: bool = True,
        admin_view: bool = False,
        active_only: bool = False,
    ) -> dict:
        out = {"items": [], "total": 0, "limit": 0, "offset": 0, "has_more": False}
        if not self.app and not has_app_context():
            return out
        with self._ctx():
            try:
                lim = max(1, min(int(limit), 500))
                off = max(0, int(offset))
            except Exception:
                lim = 200
                off = 0
            owner = str(owner_username or "").strip().lower()
            try:
                query = MarketAlertRule.query
                if active_only:
                    query = query.filter(MarketAlertRule.is_active.is_(True))
                if admin_view:
                    if owner:
                        query = query.filter(db.func.lower(MarketAlertRule.owner_username) == owner)
                else:
                    if owner:
                        if include_global:
                            query = query.filter(
                                or_(
                                    db.func.lower(MarketAlertRule.owner_username) == owner,
                                    MarketAlertRule.scope == "global",
                                )
                            )
                        else:
                            query = query.filter(db.func.lower(MarketAlertRule.owner_username) == owner)
                    else:
                        query = query.filter(MarketAlertRule.scope == "global")

                total = int(query.count() or 0)
                rows = (
                    query.order_by(MarketAlertRule.updated_at.desc(), MarketAlertRule.id.desc())
                    .offset(off)
                    .limit(lim)
                    .all()
                )
                items = [self._serialize_market_alert_rule_row(row) for row in rows]
                return {
                    "items": items,
                    "total": total,
                    "limit": lim,
                    "offset": off,
                    "has_more": (off + len(items)) < total,
                }
            except Exception as e:
                log.warning(f"query_market_alert_rules failed: {e}")
                return {"items": [], "total": 0, "limit": lim, "offset": off, "has_more": False}

    def get_active_market_alert_rules(self, *, limit: int = 500) -> list[dict]:
        result = self.query_market_alert_rules(
            limit=max(1, min(int(limit), 1000)),
            offset=0,
            owner_username="",
            include_global=True,
            admin_view=True,
            active_only=True,
        )
        return list(result.get("items") or [])

    def touch_market_alert_rule_trigger(self, rule_id: int, *, trigger_price, triggered_at: datetime | None = None) -> bool:
        with self._ctx():
            try:
                rid = int(rule_id)
                try:
                    price_val = float(trigger_price)
                except Exception:
                    price_val = 0.0
                if price_val <= 0:
                    return False
                row = MarketAlertRule.query.filter_by(id=rid).first()
                if not row:
                    return False
                row.last_triggered_at = triggered_at or datetime.now(timezone.utc)
                row.last_trigger_price = Decimal(str(price_val))
                return bool(self._commit_with_retry())
            except Exception as e:
                log.warning(f"touch_market_alert_rule_trigger failed: {e}")
                db.session.rollback()
                return False

    def record_market_alert_event(
        self,
        *,
        rule_id: int | None,
        username: str,
        market_kind: str,
        symbol: str,
        condition: str,
        target_price,
        trigger_price,
        notify_channel: str = "portal",
        delivered: bool = False,
        message: str = "",
    ) -> dict | None:
        with self._ctx():
            try:
                actor = str(username or "").strip().lower()[:120] or "anonymous"
                kind = str(market_kind or "").strip().lower()
                sym = str(symbol or "").strip().upper()[:32]
                cond = str(condition or "").strip().lower()
                chan = str(notify_channel or "").strip().lower()
                msg = str(message or "").strip()[:255]
                try:
                    target_val = float(target_price)
                    trigger_val = float(trigger_price)
                except Exception:
                    return None
                if kind not in {"stock", "crypto"}:
                    return None
                if cond not in {"cross_above", "cross_below"}:
                    return None
                if chan not in {"portal", "telegram", "both"}:
                    chan = "portal"
                if not sym or target_val <= 0 or trigger_val <= 0:
                    return None

                row = MarketAlertEvent(
                    rule_id=int(rule_id) if rule_id is not None else None,
                    username=actor,
                    market_kind=kind,
                    symbol=sym,
                    condition=cond,
                    target_price=Decimal(str(target_val)),
                    trigger_price=Decimal(str(trigger_val)),
                    notify_channel=chan,
                    delivered=bool(delivered),
                    message=msg,
                )
                db.session.add(row)
                if not self._commit_with_retry():
                    return None
                return self._serialize_market_alert_event_row(row)
            except Exception as e:
                log.warning(f"record_market_alert_event failed: {e}")
                db.session.rollback()
                return None

    def query_market_alert_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        owner_username: str = "",
        include_global: bool = True,
        admin_view: bool = False,
    ) -> dict:
        out = {"items": [], "total": 0, "limit": 0, "offset": 0, "has_more": False}
        if not self.app and not has_app_context():
            return out
        with self._ctx():
            try:
                lim = max(1, min(int(limit), 500))
                off = max(0, int(offset))
            except Exception:
                lim = 50
                off = 0
            owner = str(owner_username or "").strip().lower()
            try:
                query = MarketAlertEvent.query
                if not admin_view:
                    if owner:
                        if include_global:
                            query = query.filter(
                                or_(
                                    db.func.lower(MarketAlertEvent.username) == owner,
                                    MarketAlertEvent.username == "global",
                                )
                            )
                        else:
                            query = query.filter(db.func.lower(MarketAlertEvent.username) == owner)
                    else:
                        query = query.filter(MarketAlertEvent.username == "global")
                elif owner:
                    query = query.filter(db.func.lower(MarketAlertEvent.username) == owner)

                total = int(query.count() or 0)
                rows = (
                    query.order_by(MarketAlertEvent.timestamp.desc(), MarketAlertEvent.id.desc())
                    .offset(off)
                    .limit(lim)
                    .all()
                )
                items = [self._serialize_market_alert_event_row(row) for row in rows]
                return {
                    "items": items,
                    "total": total,
                    "limit": lim,
                    "offset": off,
                    "has_more": (off + len(items)) < total,
                }
            except Exception as e:
                log.warning(f"query_market_alert_events failed: {e}")
                return {"items": [], "total": 0, "limit": lim, "offset": off, "has_more": False}

    def create_new_trade(self, trade_data):
        with self._ctx():
            try:
                if 'partial_tp_taken_json' in trade_data and isinstance(trade_data['partial_tp_taken_json'], list):
                     trade_data['partial_tp_taken_json'] = json.dumps(trade_data['partial_tp_taken_json'])

                allowed_keys = {c.name for c in Trade.__table__.columns}
                trade_data = {k: v for k, v in trade_data.items() if k in allowed_keys}
        
                new_trade = Trade(**trade_data)
                db.session.add(new_trade)
                if self._commit_with_retry():
                    return new_trade.id
        
            except Exception as e:
                log.error(f"DB Error (create_trade): {e}")
                db.session.rollback()
                return None

    def update_trade(self, trade_id, updates):
        with self._ctx():
            try:
                def _tx():
                    trade = self._query_trade_for_update(trade_id, nowait=False).first()
                    if not trade:
                        return False
                    for key, value in updates.items():
                        if hasattr(trade, key):
                            setattr(trade, key, value)
                    return True

                return bool(self._run_transaction_with_retry(_tx))
            except Exception as e:
                log.error(f"DB Error (update_trade): {e}")
                db.session.rollback()
                return False

    def log_trade_update(self, update_data):
        with self._ctx():
            try:
                update = TradeUpdate(**update_data)
                db.session.add(update)
                self._commit_with_retry()
            except Exception as e:
                log.error(f"DB Error (log_trade_update): {e}")
                db.session.rollback()

    def claim_trade_execution(
        self,
        execution_id: str,
        *,
        command_id: str = "",
        decision_id: str = "",
        client_order_id: str = "",
        cmd: str = "",
        payload: dict | None = None,
    ) -> bool:
        execution_id = str(execution_id or "").strip()
        client_order_id = str(client_order_id or "").strip()
        if not execution_id and not client_order_id:
            return True
        if not execution_id:
            execution_id = f"client_order_id:{client_order_id}"

        payload_json = json.dumps(payload or {}, sort_keys=True, default=str)
        with self._ctx():
            try:
                def _tx():
                    filters = [ExecutionJournal.execution_id == execution_id]
                    if client_order_id:
                        filters.append(ExecutionJournal.client_order_id == client_order_id)
                    existing = ExecutionJournal.query.filter(or_(*filters)).first()
                    if existing:
                        return False
                    db.session.add(
                        ExecutionJournal(
                            execution_id=execution_id,
                            command_id=str(command_id or "").strip() or None,
                            decision_id=str(decision_id or "").strip() or None,
                            client_order_id=client_order_id or None,
                            cmd=str(cmd or "").strip()[:32],
                            status="claimed",
                            payload_json=payload_json,
                        )
                    )
                    return True

                return bool(self._run_transaction_with_retry(_tx))
            except IntegrityError:
                db.session.rollback()
                return False
            except Exception as e:
                log.error(f"DB Error (claim_trade_execution): {e}")
                db.session.rollback()
                return False

    def mark_trade_execution_status(
        self,
        execution_id: str,
        status: str,
        *,
        payload: dict | None = None,
    ) -> bool:
        execution_id = str(execution_id or "").strip()
        status = str(status or "").strip()[:32]
        if not execution_id or not status:
            return False
        payload_json = json.dumps(payload or {}, sort_keys=True, default=str)
        with self._ctx():
            try:
                row = ExecutionJournal.query.filter_by(execution_id=execution_id).first()
                if not row:
                    return False
                row.status = status
                row.payload_json = payload_json
                row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                return bool(self._commit_with_retry())
            except Exception as e:
                log.error(f"DB Error (mark_trade_execution_status): {e}")
                db.session.rollback()
                return False

    def log_equity(self, timestamp, equity_value):
        with self._ctx():
            try:
                entry = EquityLog(timestamp=timestamp, equity=equity_value)
                db.session.add(entry)
                self._commit_with_retry()
            except Exception as e:
                log.error(f"DB Error (log_equity): {e}")
                db.session.rollback()

    def load_open_positions(self):
        return self.get_open_positions()

    def get_all_closed_trades_with_pnl(self):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                trades = Trade.query.filter_by(status=TradeStatus.CLOSED.value).all()
                return [t.to_dict() for t in trades]
            except Exception as e:
                log.error(f"DB Error (get_closed_trades): {e}")
                return []

    def get_recent_trades(self, limit: int = 20):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                rows = (
                    Trade.query
                    .order_by(Trade.entry_time.desc())
                    .limit(max(1, int(limit)))
                    .all()
                )
                out = []
                for row in rows:
                    out.append(
                        {
                            "trade_id": int(row.id),
                            "symbol": str(row.symbol or ""),
                            "strategy": str(row.strategy or ""),
                            "direction": str(row.direction or ""),
                            "status": str(row.status or ""),
                            "entry_time": row.entry_time.isoformat() if row.entry_time else None,
                            "entry_price": format(row.entry_price or 0, "f"),
                            "current_size": format(row.current_size or 0, "f"),
                            "pnl": format(row.pnl or 0, "f"),
                            "entry_reason": str(row.entry_reason or ""),
                        }
                    )
                return out
            except Exception as e:
                log.error(f"DB Error (get_recent_trades): {e}")
                return []

    def get_equity_curve_from_db(self):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                logs = EquityLog.query.order_by(EquityLog.timestamp).all()
                return [l.to_dict() for l in logs]
            except Exception as e:
                log.error(f"DB Error (get_equity_curve): {e}")
                return []

    def get_all_trade_updates(self):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                updates = TradeUpdate.query.order_by(TradeUpdate.timestamp).all()
                return [u.to_dict() for u in updates]
            except Exception as e:
                log.error(f"DB Error (get_trade_updates): {e}")
                return []

    def get_equity_history(self, limit=50):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                logs = EquityLog.query.order_by(EquityLog.timestamp.desc()).limit(limit).all()
                logs = list(reversed(logs))
                return [{"ts": l.timestamp.isoformat(), "equity": float(l.equity or 0)} for l in logs]
            except Exception as e:
                log.error(f"DB Error (get_equity_history): {e}")
                return []

    def get_trading_metrics(self):
        return self.trading_metrics.get_trading_metrics()

    def get_failed_logins(self, hours=24):
        return []

    def get_open_positions(self):
        if not self.app and not has_app_context():
            return []
        with self._ctx():
            try:
                trades = Trade.query.filter_by(status=TradeStatus.OPEN.value).all()
                results = []
                for t in trades:
                    data = t.to_dict()
                    data["trade_id"] = t.id
                    try:
                        data["partial_tp_taken"] = json.loads(data.get("partial_tp_taken_json") or "[]")
                    except Exception as e:
                        log.warning(f"Failed decode partial_tp_taken_json for trade {t.id}: {e}")
                        data["partial_tp_taken"] = []
                    if data.get("current_size") is None and data.get("initial_size") is not None:
                        data["current_size"] = data["initial_size"]
                    results.append(data)
                return results
            except Exception as e:
                log.error(f"DB Error (get_positions): {e}")
                return []

    def get_focus_sessions(self, limit=10):
        with self._ctx():
            try:
                sessions = FocusSession.query.order_by(FocusSession.start_time.desc()).limit(limit).all()
                return [s.to_dict() for s in sessions]
            except Exception as e:
                log.error(f"DB Error (get_focus): {e}")
                return []

    def get_focus_sessions_with_tasks(self, start_value, end_value):
        with self._ctx():
            try:
                start = self._parse_datetime(start_value)
                end = self._parse_datetime(end_value)
                if not start or not end:
                    return []
                sessions = (
                    FocusSession.query
                    .options(joinedload(FocusSession.task))
                    .filter(FocusSession.start_time.between(start, end))
                    .all()
                )
                results = []
                for s in sessions:
                    results.append({
                        "start_time": s.start_time.isoformat(),
                        "duration_minutes": s.duration_minutes or 0,
                        "task_title": s.task.title if s.task else None
                    })
                return results
            except Exception as e:
                log.error(f"DB Error (get_focus_with_tasks): {e}")
                return []

    def create_task(self, title, priority="medium"):
        with self._ctx():
            try:
                task = Task(title=title, priority=priority, status=TaskStatus.PENDING.value)
                db.session.add(task)
                if self._commit_with_retry():
                    return task.id
            except Exception as e:
                db.session.rollback()
                log.error(f"create_task failed: {e}")
                return None

    def atomic_trade_update(
        self,
        trade_id: int,
        *,
        trade_updates: dict,
        event: dict,
    ) -> bool:
        with self._ctx():
            try:
                use_nowait = bool(getattr(Config, "DB_TX_FOR_UPDATE_NOWAIT", True)) and not self._is_sqlite_backend()
                def _tx():
                    trade = self._query_trade_for_update(trade_id, nowait=use_nowait).first()
                    if not trade:
                        return False
                    for key, value in (trade_updates or {}).items():
                        if hasattr(trade, key):
                            setattr(trade, key, value)
                    update_row = TradeUpdate(**(event or {}))
                    if not update_row.timestamp:
                        update_row.timestamp = datetime.now(timezone.utc)
                    db.session.add(update_row)
                    return True

                return bool(self._run_transaction_with_retry(_tx))
            except Exception as e:
                db.session.rollback()
                log.error(f"atomic_trade_update failed: {e}")
                return False

    def block_ip(self, ip, reason="Suspicious Activity"):
        self.security.block_ip(ip=ip, reason=reason)

    def is_ip_blocked(self, ip):
        return self.security.is_ip_blocked(ip=ip)

    def get_trusted_devices(self):
        return self.security.get_trusted_devices()

    def add_trusted_device(self, ip, name):
        self.security.add_trusted_device(ip=ip, name=name)

    def get_blocked_ips(self):
        return self.security.get_blocked_ips()

    def unblock_ip(self, ip):
        self.security.unblock_ip(ip=ip)

    def set_runtime_setting(self, key: str, value, scope: str = "trading", updated_by: str = "system"):
        return self.runtime_settings.set_runtime_setting(
            key=key,
            value=value,
            scope=scope,
            updated_by=updated_by,
        )

    def get_runtime_settings(self, scope: str = "trading"):
        return self.runtime_settings.get_runtime_settings(scope=scope)

    def get_runtime_setting(self, key: str, default=None, scope: str = "trading"):
        key_norm = str(key or "").strip().lower()
        if not key_norm:
            return default
        settings = self.get_runtime_settings(scope=scope)
        return settings.get(key_norm, default)

    def get_trades_with_open_exchange_orders(self, limit: int = 200):
        open_states = {
            ExchangeOrderStatus.OPEN.value,
            ExchangeOrderStatus.NEW.value,
            ExchangeOrderStatus.PARTIALLY_FILLED.value,
            ExchangeOrderStatus.PENDING.value,
        }
        with self._ctx():
            try:
                rows = (
                    Trade.query
                    .filter(Trade.exchange_order_id.isnot(None))
                    .filter(Trade.exchange_order_status.isnot(None))
                    .limit(max(1, int(limit)))
                    .all()
                )
                out = []
                for row in rows:
                    status = str(row.exchange_order_status or "").strip().lower()
                    if status in open_states:
                        item = row.to_dict()
                        item["trade_id"] = row.id
                        out.append(item)
                return out
            except Exception as e:
                log.error(f"get_trades_with_open_exchange_orders failed: {e}")
                return []

