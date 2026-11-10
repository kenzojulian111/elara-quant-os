from dataclasses import dataclass
import time
import logging
import uuid
import threading
import secrets
from typing import Optional, Dict, Callable

from config.settings import Config
from utils.redis_helper import get_redis, redis_safe_call

class _NoopLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

log = logging.getLogger("elara.life")


@dataclass
class UserContext:
    sleep_hours: float = 0.0
    energy: int = 0
    focus_minutes: int = 0
    stress: int = 0
    last_update: float = 0.0


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""


class LifeManager:
    def __init__(
        self,
        storage=None,
        default_user: str = "default",
        min_sleep_hours: float = 5.0,
        max_stress: int = 7,
        min_energy: int = 0,
        min_focus_minutes: int = 120,
        context_max_age_sec: int = 86400,
        signal_callback: Optional[Callable[[dict], None]] = None,
    ):
        self._storage = storage
        self._cache: Dict[str, UserContext] = {}
        self._lock = threading.RLock()
        self._default_user = default_user
        self.min_sleep_hours = float(min_sleep_hours)
        self.max_stress = int(max_stress)
        self.min_energy = int(min_energy)
        self.min_focus_minutes = int(min_focus_minutes)
        self.context_max_age_sec = int(context_max_age_sec)
        self._signal_callback = signal_callback
        self._signal_state: Dict[str, Optional[bool]] = {}
        self._redis = get_redis()
        self._dist_lock_enabled = bool(getattr(storage, "redis_client", None) or self._redis)
        self._dist_lock_ttl_sec = max(3, int(getattr(Config, "LIFE_REDIS_LOCK_TTL_SEC", 10)))
        self._dist_lock_wait_sec = max(0.0, float(getattr(Config, "LIFE_REDIS_LOCK_WAIT_SEC", 1.5)))
        self._load(default_user)

    def _redis_lock_key(self, user_id: str) -> str:
        prefix = "elara:"
        try:
            prefix = str(getattr(Config, "STATE_REDIS_PREFIX", "elara:") or "elara:")
        except Exception:
            pass
        return f"{prefix}life:lock:{user_id}"

    def _acquire_dist_lock(self, user_id: str):
        redis_client = getattr(self._storage, "redis_client", None) or self._redis
        if not redis_client:
            redis_client = get_redis()
            if redis_client:
                self._redis = redis_client
                self._dist_lock_enabled = True
        if not redis_client:
            return _NoopLock()

        token = uuid.uuid4().hex
        lock_key = self._redis_lock_key(user_id)
        deadline = time.time() + self._dist_lock_wait_sec
        while time.time() < deadline:
            try:
                if redis_safe_call(
                    redis_client,
                    "set",
                    lock_key,
                    token,
                    nx=True,
                    ex=self._dist_lock_ttl_sec,
                    default=False,
                    purpose="life_lock_acquire",
                ):
                    class _OwnedLock:
                        def __enter__(self_inner):
                            return self_inner

                        def __exit__(self_inner, exc_type, exc_val, exc_tb):
                            try:
                                current = redis_safe_call(
                                    redis_client,
                                    "get",
                                    lock_key,
                                    default=None,
                                    purpose="life_lock_release_get",
                                )
                                current_text = (
                                    current.decode("utf-8", errors="ignore")
                                    if isinstance(current, (bytes, bytearray))
                                    else str(current or "")
                                )
                                if secrets.compare_digest(current_text, token):
                                    redis_safe_call(
                                        redis_client,
                                        "delete",
                                        lock_key,
                                        default=0,
                                        purpose="life_lock_release_del",
                                    )
                            except Exception as e:
                                log.debug(f"Life lock release warning: {e}")
                            return False

                    return _OwnedLock()
            except Exception as e:
                log.debug(f"Life lock acquire warning: {e}")
                break
            time.sleep(0.05)
        return _NoopLock()

    def _load(self, user_id: str):
        ctx = None
        with self._lock:
            if self._storage and hasattr(self._storage, "get_state"):
                try:
                    data = self._storage.get_state("life_context", user_id=user_id)
                    if isinstance(data, dict):
                        ctx = UserContext(**data)
                except Exception as e:
                    log.warning(f"Failed loading life context for user '{user_id}': {e}")
                    ctx = None
            if not ctx:
                ctx = UserContext()
            self._cache[user_id] = ctx

    def _save(self, user_id: str):
        with self._lock:
            if self._storage and hasattr(self._storage, "set_state"):
                try:
                    ctx = self._cache.get(user_id)
                    if ctx:
                        self._storage.set_state("life_context", ctx.__dict__, user_id=user_id)
                except Exception as e:
                    log.error(f"Failed saving life context for user '{user_id}': {e}")

    def _context_stale(self, ctx: UserContext) -> bool:
        if self.context_max_age_sec <= 0:
            return False
        if ctx.last_update <= 0:
            return False
        age = time.time() - float(ctx.last_update)
        return age > self.context_max_age_sec

    def _emit_signal(self, user_id: str, decision: PermissionDecision):
        blocked = not decision.allowed
        prev_blocked = self._signal_state.get(user_id)
        if prev_blocked is not None and prev_blocked == blocked:
            return
        self._signal_state[user_id] = blocked
        if not self._signal_callback:
            return
        payload = {
            "user_id": user_id,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "ts": time.time(),
        }
        try:
            self._signal_callback(payload)
        except Exception as e:
            log.warning(f"Life signal callback failed for '{user_id}': {e}")

    def get_context(self, user_id: str = "default") -> UserContext:
        with self._lock:
            if user_id not in self._cache:
                self._load(user_id)
            return self._cache[user_id]

    @staticmethod
    def _coerce_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _coerce_int(value, default: int = 0) -> int:
        try:
            return int(float(value))
        except Exception:
            return int(default)

    def update(
        self,
        sleep_hours: Optional[float] = None,
        energy: Optional[int] = None,
        focus_minutes: Optional[int] = None,
        stress: Optional[int] = None,
        user_id: str = "default",
    ) -> UserContext:
        with self._acquire_dist_lock(user_id):
            with self._lock:
                ctx = self.get_context(user_id)
                if sleep_hours is not None:
                    ctx.sleep_hours = max(0.0, self._coerce_float(sleep_hours, ctx.sleep_hours))
                if energy is not None:
                    energy_num = self._coerce_int(energy, ctx.energy)
                    ctx.energy = int(max(0, min(100, energy_num)))
                if focus_minutes is not None:
                    focus_num = self._coerce_int(focus_minutes, ctx.focus_minutes)
                    ctx.focus_minutes = int(max(0, focus_num))
                if stress is not None:
                    stress_num = self._coerce_int(stress, ctx.stress)
                    ctx.stress = int(max(0, min(10, stress_num)))
                ctx.last_update = time.time()
                self._save(user_id)
        decision = self.evaluate_trading(user_id)
        self._emit_signal(user_id, decision)
        return self.get_context(user_id)

    def evaluate_trading(self, user_id: str = "default") -> PermissionDecision:
        ctx = self.get_context(user_id)
        if ctx.last_update <= 0:
            return PermissionDecision(False, "Status belum diupdate.")
        if self._context_stale(ctx):
            return PermissionDecision(False, "Status user sudah stale, update ulang kondisi.")
        if ctx.sleep_hours < self.min_sleep_hours:
            return PermissionDecision(False, f"Tidur kurang dari {self.min_sleep_hours:.1f} jam.")
        if self.min_energy > 0 and ctx.energy < self.min_energy:
            return PermissionDecision(False, f"Energi di bawah batas minimal ({self.min_energy}).")
        if ctx.stress > self.max_stress:
            return PermissionDecision(False, f"Stres terlalu tinggi (>{self.max_stress}).")
        return PermissionDecision(True, "Kondisi cukup stabil untuk trading.")

    def evaluate_entertainment(self, user_id: str = "default") -> PermissionDecision:
        ctx = self.get_context(user_id)
        if ctx.last_update <= 0:
            return PermissionDecision(False, "Status belum diupdate.")
        if self._context_stale(ctx):
            return PermissionDecision(False, "Status user sudah stale, update ulang kondisi.")
        if ctx.focus_minutes < self.min_focus_minutes:
            return PermissionDecision(False, f"Fokus belum mencapai {self.min_focus_minutes} menit.")
        return PermissionDecision(True, "Fokus sudah cukup, boleh hiburan.")

    def get_mode(self, user_id: str = "default") -> str:
        trading = self.evaluate_trading(user_id)
        if not trading.allowed:
            return "Waspada"
        return "Produktif"

