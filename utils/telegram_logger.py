import logging
import time
import hashlib
import asyncio
import contextlib
import sys
from typing import Optional

import httpx

from config.settings import Config
from utils.log_redaction import SensitiveDataFilter

log = logging.getLogger("elara.telegram_logger")


def _level_from_name(name: str) -> int:
    try:
        return getattr(logging, (name or "").upper())
    except (AttributeError, TypeError) as e:
        log.warning(f"Invalid TELEGRAM_LOG_LEVEL '{name}', fallback to WARNING: {e}")
        return logging.WARNING


class AsyncTelegramLogHandler(logging.Handler):
    """
    Non-blocking logging handler that forwards records to a Telegram chat.
    Level & format dapat diubah lewat env:
      TELEGRAM_LOG_LEVEL (default WARNING)
      TELEGRAM_LOG_FORMAT (default "%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    """

    def __init__(self, token: str, chat_id: int, level: int, fmt: str):
        super().__init__(level)
        self.token = token
        self.chat_id = chat_id
        self._queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue(maxsize=200)
        self._throttle_state = {}
        self._throttle_window_sec = int(getattr(Config, "TELEGRAM_LOG_THROTTLE_WINDOW_SEC", 60))
        self._throttle_burst = int(getattr(Config, "TELEGRAM_LOG_THROTTLE_BURST", 5))
        self._bucket_rate_per_sec = max(0.1, float(getattr(Config, "TELEGRAM_LOG_BUCKET_RATE_PER_SEC", 1.0)))
        self._bucket_capacity = max(1.0, float(getattr(Config, "TELEGRAM_LOG_BUCKET_CAPACITY", 4.0)))
        self._bucket_tokens = float(self._bucket_capacity)
        self._bucket_last_refill = time.monotonic()
        self._batch_window_sec = max(0.0, float(getattr(Config, "TELEGRAM_LOG_BATCH_WINDOW_SEC", 0.35)))
        self._batch_max_messages = max(1, int(getattr(Config, "TELEGRAM_LOG_BATCH_MAX_MESSAGES", 8)))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._supervisor_task: Optional[asyncio.Task] = None
        self._warned_no_loop = False
        self._closed = False
        self._send_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_fail_threshold = max(
            1,
            int(getattr(Config, "TELEGRAM_LOG_CIRCUIT_BREAKER_THRESHOLD", 5)),
        )
        self._circuit_cooldown_sec = max(
            10,
            int(getattr(Config, "TELEGRAM_LOG_CIRCUIT_BREAKER_COOLDOWN_SEC", 120)),
        )
        self._worker_restart_count = 0
        self._internal_logger = logging.getLogger("elara.telegram_logger.internal")
        if not self._internal_logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            self._internal_logger.addHandler(handler)
        self._internal_logger.propagate = False
        self._internal_logger.setLevel(logging.WARNING)
        self.addFilter(SensitiveDataFilter())
        self.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    def _internal_warn(self, message: str):
        try:
            self._internal_logger.warning(message)
        except Exception:
            pass

    def _ensure_worker(self, loop: asyncio.AbstractEventLoop):
        if self._worker_task and not self._worker_task.done():
            if self._supervisor_task is None or self._supervisor_task.done():
                self._supervisor_task = loop.create_task(self._supervisor_loop())
            return
        if self._worker_task and self._worker_task.done() and not self._closed:
            self._internal_warn("Telegram log worker stopped. Spawning replacement worker.")
        self._loop = loop
        self._worker_task = loop.create_task(self._loop_worker())
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = loop.create_task(self._supervisor_loop())

    async def _supervisor_loop(self):
        while not self._closed:
            try:
                await asyncio.sleep(max(1, int(getattr(Config, "TELEGRAM_LOG_SUPERVISOR_INTERVAL_SEC", 5))))
                if self._closed:
                    return
                if self._worker_task is None or self._worker_task.done():
                    if self._loop and not self._loop.is_closed():
                        self._internal_warn("Telegram logger supervisor restarting worker.")
                        self._worker_task = self._loop.create_task(self._loop_worker())
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._internal_warn(f"Telegram logger supervisor error: {e}")

    def _split_message(self, msg: str):
        size = 3900
        text = str(msg or "")
        if not text:
            return []
        return [text[i:i + size] for i in range(0, len(text), size)]

    def _throttle_decision(self, msg: str):
        key = hashlib.sha1(str(msg or "").strip().encode("utf-8", errors="ignore")).hexdigest()
        now = time.time()
        state = self._throttle_state.get(key)
        if not state:
            self._throttle_state[key] = {"window_start": now, "count": 1, "suppressed": 0}
            return True, None

        elapsed = now - float(state.get("window_start", now))
        if elapsed > self._throttle_window_sec:
            suppressed = int(state.get("suppressed", 0))
            self._throttle_state[key] = {"window_start": now, "count": 1, "suppressed": 0}
            if suppressed > 0:
                return True, f"[THROTTLED] message repeated {suppressed}x in previous window."
            return True, None

        state["count"] = int(state.get("count", 0)) + 1
        if state["count"] <= self._throttle_burst:
            return True, None
        state["suppressed"] = int(state.get("suppressed", 0)) + 1
        if state["count"] == (self._throttle_burst + 1):
            return True, (
                f"[THROTTLE ACTIVE] same error > {self._throttle_burst}x in "
                f"{self._throttle_window_sec}s; repeats suppressed."
            )
        return False, None

    def _enqueue_non_blocking(self, msg: str):
        if self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if not self._warned_no_loop:
                self._warned_no_loop = True
                self._internal_warn(
                    "No running event loop for AsyncTelegramLogHandler. "
                    "Telegram log message dropped."
                )
            return
        self._ensure_worker(loop)
        if self._loop is not loop:
            self._internal_warn("AsyncTelegramLogHandler called from different loop; dropping message.")
            return
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._internal_warn("Telegram async queue full. Dropping log record.")

    def _refill_bucket(self):
        now = time.monotonic()
        elapsed = max(0.0, now - float(self._bucket_last_refill))
        self._bucket_last_refill = now
        if elapsed <= 0:
            return
        self._bucket_tokens = min(
            self._bucket_capacity,
            float(self._bucket_tokens) + (elapsed * self._bucket_rate_per_sec),
        )

    async def _wait_for_bucket_slot(self):
        while not self._closed:
            self._refill_bucket()
            if self._bucket_tokens >= 1.0:
                self._bucket_tokens -= 1.0
                return
            shortfall = max(0.0, 1.0 - float(self._bucket_tokens))
            wait_sec = max(0.01, min(1.0, shortfall / self._bucket_rate_per_sec))
            await asyncio.sleep(wait_sec)

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            allow_original, notice = self._throttle_decision(msg)
            outbound = []
            if notice:
                outbound.extend(self._split_message(notice))
            if allow_original:
                outbound.extend(self._split_message(msg))
            for part in outbound:
                self._enqueue_non_blocking(part)
        except Exception:
            self.handleError(record)

    async def _loop_worker(self):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        timeout = httpx.Timeout(5.0, connect=5.0)
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        while not self._closed:
            try:
                async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
                    while True:
                        msg = await self._queue.get()
                        if msg is None:
                            self._queue.task_done()
                            return
                        batch = [msg]
                        batch_count = 1
                        should_stop_after_batch = False
                        while len(batch) < self._batch_max_messages:
                            try:
                                nxt = await asyncio.wait_for(self._queue.get(), timeout=self._batch_window_sec)
                            except asyncio.TimeoutError:
                                break
                            if nxt is None:
                                self._queue.task_done()
                                should_stop_after_batch = True
                                break
                            batch.append(nxt)
                            batch_count += 1
                        merged = "\n".join(str(x or "") for x in batch if str(x or "").strip())
                        outbound_parts = self._split_message(merged)
                        if not outbound_parts:
                            for _ in range(batch_count):
                                self._queue.task_done()
                            if should_stop_after_batch:
                                return
                            continue
                        now = time.time()
                        if now < self._circuit_open_until:
                            for _ in range(batch_count):
                                self._queue.task_done()
                            if should_stop_after_batch:
                                return
                            continue
                        for part in outbound_parts:
                            try:
                                await self._wait_for_bucket_slot()
                                response = await client.post(
                                    url,
                                    json={"chat_id": self.chat_id, "text": part},
                                )
                                response.raise_for_status()
                                self._send_failures = 0
                                self._worker_restart_count = 0
                            except Exception as e:
                                self._send_failures += 1
                                if self._send_failures >= self._circuit_fail_threshold:
                                    self._circuit_open_until = time.time() + self._circuit_cooldown_sec
                                    self._send_failures = 0
                                    self._internal_warn(
                                        "Telegram logger circuit breaker opened for "
                                        f"{self._circuit_cooldown_sec}s after repeated failure: {e}"
                                    )
                                    break
                                self._internal_warn(f"Failed sending Telegram log message: {e}")
                        for _ in range(batch_count):
                            self._queue.task_done()
                        if should_stop_after_batch:
                            return
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._worker_restart_count += 1
                wait_sec = min(30.0, float(2 ** min(self._worker_restart_count, 5)))
                self._internal_warn(
                    "Telegram logger worker crashed; scheduling restart "
                    f"#{self._worker_restart_count} in {wait_sec:.1f}s: {e}"
                )
                try:
                    await asyncio.sleep(wait_sec)
                except asyncio.CancelledError:
                    return

    def close(self):
        if self._closed:
            super().close()
            return
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self._worker_task and self._worker_task.done():
            self._worker_task = None
        if self._supervisor_task and not self._supervisor_task.done():
            self._supervisor_task.cancel()
        super().close()


def setup_telegram_logging() -> Optional[AsyncTelegramLogHandler]:
    """
    Attach Telegram handler to root logger jika kredensial tersedia.
    Return handler atau None.
    """
    token = Config.BOT_TOKEN
    chat_id = getattr(Config, "TG_ALLOWED_USER", 0)
    if not token or not chat_id:
        logging.getLogger("elara.telegram_logger").info(
            "Telegram logging idle (BOT_TOKEN/TG_ALLOWED_USER kosong)."
        )
        return None

    level = _level_from_name(getattr(Config, "TELEGRAM_LOG_LEVEL", "WARNING"))
    fmt = getattr(Config, "TELEGRAM_LOG_FORMAT", "%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    handler = AsyncTelegramLogHandler(token, chat_id, level=level, fmt=fmt)
    logging.getLogger().addHandler(handler)
    return handler

