import json
import logging
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from apscheduler.triggers.cron import CronTrigger
try:
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
except Exception:
    SQLAlchemyJobStore = None
try:
    from apscheduler.jobstores.redis import RedisJobStore
except Exception:
    RedisJobStore = None
try:
    from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
except Exception:
    EVENT_JOB_MISSED = EVENT_JOB_EXECUTED = EVENT_JOB_ERROR = None

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _HAS_ASYNCIO_SCHEDULER = True
except Exception:
    AsyncIOScheduler = None
    _HAS_ASYNCIO_SCHEDULER = False

from config.settings import Config
from core.events import build_core_heartbeat_event
from utils.common import async_runner
from utils.redis_helper import get_redis, redis_safe_call

log = logging.getLogger("elara.scheduler")


class SchedulerService:
    def __init__(self, elara_skill, backup_service=None):
        self.elara = elara_skill
        self.backup_service = backup_service
        self._running = False
        self._persistent_jobstore_enabled = False
        self.scheduler = self._build_scheduler()
        self.redis = get_redis()
        self.core_heartbeat_key = str(getattr(Config, "CORE_HEARTBEAT_KEY", "") or "").strip()
        self.core_heartbeat_path = str(getattr(Config, "CORE_HEARTBEAT_PATH", "") or "").strip()
        self._heartbeat_file_fallback = bool(getattr(Config, "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED", True))
        self._job_lock_prefix = str(
            getattr(Config, "SCHEDULER_JOB_LOCK_PREFIX", f"{getattr(Config, 'REDIS_PREFIX', 'elara:')}scheduler:lock:")
            or f"{getattr(Config, 'REDIS_PREFIX', 'elara:')}scheduler:lock:"
        ).strip()
        self._job_lock_ttl_sec = max(30, int(getattr(Config, "SCHEDULER_JOB_LOCK_TTL_SEC", 600)))

    def _refresh_redis(self):
        if self.redis:
            return self.redis
        self.redis = get_redis()
        return self.redis

    def _build_scheduler(self, *, allow_persistence: bool = True):
        if not _HAS_ASYNCIO_SCHEDULER or not AsyncIOScheduler:
            log.warning("AsyncIOScheduler unavailable. SchedulerService disabled to keep core thread-free.")
            return None
        scheduler_kwargs = {"event_loop": async_runner.loop}
        self._persistent_jobstore_enabled = False
        redis_jobstore_url = str(getattr(Config, "SCHEDULER_JOBSTORE_REDIS_URL", "") or "").strip()
        jobstore_url = str(getattr(Config, "SCHEDULER_JOBSTORE_URL", "") or "").strip()
        if allow_persistence and RedisJobStore and redis_jobstore_url:
            try:
                parsed = urlparse(redis_jobstore_url)
                scheduler_kwargs["jobstores"] = {
                    "default": RedisJobStore(
                        host=str(parsed.hostname or "127.0.0.1"),
                        port=int(parsed.port or 6379),
                        db=int(str(parsed.path or "/0").strip("/").split("/", 1)[0] or 0),
                        password=parsed.password,
                        jobs_key=str(getattr(Config, "SCHEDULER_REDIS_JOBS_KEY", "elara:scheduler:jobs")),
                        run_times_key=str(getattr(Config, "SCHEDULER_REDIS_RUN_TIMES_KEY", "elara:scheduler:run_times")),
                    )
                }
                self._persistent_jobstore_enabled = True
                log.info("Scheduler Redis job store enabled.")
            except Exception as e:
                log.warning(f"Failed enabling Redis scheduler job store ({redis_jobstore_url}): {e}")
        elif allow_persistence and SQLAlchemyJobStore and jobstore_url:
            try:
                if jobstore_url.startswith("sqlite:///"):
                    sqlite_path = jobstore_url.replace("sqlite:///", "", 1)
                    sqlite_dir = os.path.dirname(sqlite_path)
                    if sqlite_dir:
                        os.makedirs(sqlite_dir, exist_ok=True)
                scheduler_kwargs["jobstores"] = {"default": SQLAlchemyJobStore(url=jobstore_url)}
                self._persistent_jobstore_enabled = True
                log.info(f"Scheduler persistent job store enabled: {jobstore_url}")
            except Exception as e:
                log.warning(f"Failed enabling scheduler job store ({jobstore_url}): {e}")
        elif allow_persistence and not SQLAlchemyJobStore and not RedisJobStore:
            log.warning("Scheduler jobstore dependency unavailable; using in-memory jobs.")
        else:
            if allow_persistence:
                log.warning("SCHEDULER_JOBSTORE_URL empty; scheduler will use in-memory jobs.")
            else:
                log.warning("Scheduler persistence disabled; using in-memory jobs.")
        return AsyncIOScheduler(**scheduler_kwargs)

    def _attach_listeners(self):
        if EVENT_JOB_MISSED:
            self.scheduler.add_listener(
                self._job_event_listener,
                EVENT_JOB_MISSED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
            )

    def _register_jobs(self):
        self.scheduler.add_job(
            self.run_intent,
            CronTrigger(hour=7, minute=0),
            args=["get_daily_briefing", "system_auto", "morning_briefing"],
            id="morning_briefing",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self.run_intent,
            CronTrigger(hour=12, minute=0),
            args=["trading_status", "system_auto", "midday_trading_check"],
            id="midday_trading_check",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._heartbeat_log,
            "interval",
            minutes=30,
            id="heartbeat",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=max(30, int(getattr(Config, "SCHEDULER_MISFIRE_GRACE_SEC", 90))),
        )

        self.scheduler.add_job(
            self._publish_core_heartbeat,
            "interval",
            seconds=max(2, int(getattr(Config, "CORE_HEARTBEAT_INTERVAL_SEC", 5))),
            id="core_heartbeat_publish",
            replace_existing=True,
        )

        if self.backup_service and Config.BACKUP_ENABLED:
            self.scheduler.add_job(
                self.backup_service.trigger_backup,
                CronTrigger(hour=Config.BACKUP_HOUR, minute=Config.BACKUP_MINUTE),
                id="daily_backup",
                replace_existing=True,
            )

    def _restart_in_memory_scheduler(self, reason: Exception):
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception as e:
                log.warning(f"Scheduler shutdown before fallback failed: {e}")
        log.warning(
            "Scheduler persistent job store incompatible with runtime callables (%s). "
            "Falling back to in-memory jobs.",
            reason,
        )
        self.scheduler = self._build_scheduler(allow_persistence=False)
        if not self.scheduler:
            raise RuntimeError("SchedulerService could not rebuild scheduler in fallback mode.")
        self.scheduler.start()
        self._running = True
        self._attach_listeners()
        self._register_jobs()

    def _heartbeat_log(self):
        log.info("System heartbeat: Elara is alive.")

    def _job_event_listener(self, event):
        if not EVENT_JOB_MISSED:
            return
        now = datetime.now(timezone.utc)
        threshold_sec = max(1, int(getattr(Config, "SCHEDULER_LATE_JOB_WARN_SEC", 15)))
        scheduled = getattr(event, "scheduled_run_time", None)
        if scheduled is None:
            return
        try:
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            delay_sec = max(0.0, (now - scheduled).total_seconds())
        except Exception:
            delay_sec = 0.0
        if delay_sec >= threshold_sec:
            log.warning(
                "Scheduler delayed job detected: job_id=%s delay=%.2fs",
                getattr(event, "job_id", "unknown"),
                delay_sec,
            )

    def _publish_core_heartbeat(self):
        payload = build_core_heartbeat_event(pid=os.getpid())
        redis_client = self._refresh_redis()
        if redis_client and self.core_heartbeat_key:
            ok = redis_safe_call(
                redis_client,
                "set",
                self.core_heartbeat_key,
                json.dumps(payload),
                default=False,
                purpose="scheduler_heartbeat",
            )
            if not ok:
                self.redis = None
                log.debug("Core heartbeat redis publish failed.")
        if self._heartbeat_file_fallback and self.core_heartbeat_path:
            try:
                os.makedirs(os.path.dirname(self.core_heartbeat_path), exist_ok=True)
                with open(self.core_heartbeat_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            except Exception as e:
                log.debug(f"Core heartbeat file publish failed: {e}")

    def _acquire_job_lock(self, job_id: str):
        redis_client = self._refresh_redis()
        if not redis_client:
            return None, None
        key = f"{self._job_lock_prefix}{job_id}"
        token = secrets.token_hex(8)
        ok = redis_safe_call(
            redis_client,
            "set",
            key,
            token,
            nx=True,
            ex=self._job_lock_ttl_sec,
            default=False,
            purpose="scheduler_lock_acquire",
        )
        if ok:
            return key, token
        self.redis = None
        log.debug(f"Scheduler Redis lock acquire failed ({job_id}).")
        return None, None

    def _release_job_lock(self, lock_key: str, token: str):
        redis_client = self._refresh_redis()
        if not redis_client or not lock_key or not token:
            return
        current = redis_safe_call(
            redis_client,
            "get",
            lock_key,
            default=None,
            purpose="scheduler_lock_release_get",
        )
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="ignore")
        if str(current or "") == str(token):
            redis_safe_call(
                redis_client,
                "delete",
                lock_key,
                default=0,
                purpose="scheduler_lock_release_del",
            )

    def start(self):
        if not self.scheduler:
            required = set(getattr(Config, "REQUIRED_COMPONENTS", []) or [])
            if "scheduler" in required or bool(getattr(Config, "FAIL_FAST_STARTUP", False)):
                raise RuntimeError("SchedulerService requires AsyncIOScheduler but dependency is unavailable.")
            return
        if self.scheduler.running:
            return

        self.scheduler.start()
        self._running = True
        log.info("Scheduler system started.")
        self._publish_core_heartbeat()
        self._attach_listeners()
        try:
            self._register_jobs()
        except TypeError as e:
            if self._persistent_jobstore_enabled and "pickle" in str(e).lower():
                self._restart_in_memory_scheduler(e)
                return
            raise

    def stop(self):
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception as e:
                log.warning(f"Scheduler stop failed: {e}")
        self.scheduler = self._build_scheduler()
        self._running = False

    def is_healthy(self):
        if not self._running:
            return True
        if not self.scheduler:
            return False
        return self.scheduler.running

    def run_intent(self, intent_text, user_id, job_id=None):
        task_name = str(job_id or intent_text or "scheduled_task")
        lock_key = None
        lock_token = None
        if job_id:
            lock_key, lock_token = self._acquire_job_lock(str(job_id))
            if not lock_key:
                log.warning(f"Scheduler skipped overlapping job: {job_id}")
                return

        log.info(f"Running scheduled task: {intent_text}")
        future = async_runner.run_coroutine_fire_and_forget(
            self.elara.handle_command(intent_text, user_id=user_id)
        )
        if lock_key and not future:
            self._release_job_lock(lock_key, lock_token)
            return
        if not lock_key:
            return

        def _unlock(_):
            self._release_job_lock(lock_key, lock_token)

        try:
            future.add_done_callback(_unlock)
        except Exception:
            self._release_job_lock(lock_key, lock_token)
            log.debug(f"Scheduler lock callback attach failed for {task_name}; lock released directly.")

