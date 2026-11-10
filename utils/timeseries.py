import logging
import time
import asyncio
import threading
import contextlib
from typing import Dict, Optional
from urllib.parse import quote_plus


class TimeSeriesSink:
    def write_point(self, measurement: str, fields: Dict[str, float], tags: Optional[Dict[str, str]] = None):
        raise NotImplementedError

    def close(self):
        return None


def _escape_tag(value: str) -> str:
    return str(value or "").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


class InfluxDBSink(TimeSeriesSink):
    """Minimal InfluxDB v2 line-protocol writer."""

    def __init__(self, url: str, token: str, org: str, bucket: str, logger: logging.Logger):
        self.url = str(url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.org = str(org or "").strip()
        self.bucket = str(bucket or "").strip()
        self.logger = logger
        self._client = None

        if not (self.url and self.token and self.org and self.bucket):
            raise ValueError("TSDB config incomplete for InfluxDB sink.")

        try:
            import httpx

            self._client = httpx.Client(timeout=5.0)
        except Exception as e:
            raise RuntimeError(f"httpx unavailable for InfluxDB sink: {e}") from e

    def _line(self, measurement: str, fields: Dict[str, float], tags: Optional[Dict[str, str]]) -> str:
        m = _escape_tag(measurement)
        tag_str = ""
        if tags:
            parts = []
            for key, value in tags.items():
                k = _escape_tag(key)
                v = _escape_tag(value)
                if k and v:
                    parts.append(f"{k}={v}")
            if parts:
                tag_str = "," + ",".join(parts)

        field_parts = []
        for key, value in (fields or {}).items():
            try:
                num = float(value)
            except Exception:
                continue
            field_parts.append(f"{_escape_tag(key)}={num}")
        if not field_parts:
            raise ValueError("TSDB point has no numeric fields.")
        timestamp_ns = int(time.time() * 1_000_000_000)
        return f"{m}{tag_str} {','.join(field_parts)} {timestamp_ns}"

    def write_point(self, measurement: str, fields: Dict[str, float], tags: Optional[Dict[str, str]] = None):
        if not self._client:
            return
        line = self._line(measurement, fields, tags)
        path = (
            f"{self.url}/api/v2/write"
            f"?org={quote_plus(self.org)}&bucket={quote_plus(self.bucket)}&precision=ns"
        )
        headers = {"Authorization": f"Token {self.token}"}
        try:
            resp = self._client.post(path, content=line.encode("utf-8"), headers=headers)
            if resp.status_code >= 300:
                self.logger.warning("TSDB write rejected (%s): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            self.logger.warning("TSDB write failed: %s", e)

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass


class AsyncQueueTimeSeriesSink(TimeSeriesSink):
    """
    Non-blocking wrapper for TimeSeriesSink.
    Enqueues points and flushes from a dedicated asyncio loop in background thread.
    """

    def __init__(self, sink: TimeSeriesSink, logger: logging.Logger, max_queue: int = 2000):
        self._sink = sink
        self._logger = logger
        self._max_queue = max(100, int(max_queue))
        self._closed = False
        self._loop = None
        self._thread = None
        self._queue = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="ElaraTSDBWriter")
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue(maxsize=self._max_queue)
        self._ready.set()
        worker = self._loop.create_task(self._worker())
        try:
            self._loop.run_forever()
        finally:
            worker.cancel()
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(worker)
            with contextlib.suppress(Exception):
                self._sink.close()
            self._loop.close()

    async def _worker(self):
        while True:
            if not self._queue:
                return
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            measurement, fields, tags = item
            try:
                await asyncio.to_thread(self._sink.write_point, measurement, fields, tags)
            except Exception as e:
                self._logger.warning("TSDB background write failed: %s", e)
            finally:
                self._queue.task_done()

    def write_point(self, measurement: str, fields: Dict[str, float], tags: Optional[Dict[str, str]] = None):
        if self._closed:
            return
        if not self._loop or not self._queue:
            return

        def _enqueue() -> None:
            if self._queue is None:
                return
            try:
                self._queue.put_nowait((measurement, fields, tags))
            except asyncio.QueueFull:
                self._logger.warning("TSDB queue full. Dropping metrics point.")

        try:
            self._loop.call_soon_threadsafe(_enqueue)
        except Exception as e:
            self._logger.warning("TSDB queue dispatch failed: %s", e)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._loop and self._queue:
            # capture loop and queue to avoid races where attributes become None
            loop = self._loop
            q = self._queue

            def _stop():
                if q is not None:
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(None)
                # use captured loop
                try:
                    loop.call_later(0.2, loop.stop)
                except Exception:
                    # fallback: stop immediately
                    with contextlib.suppress(Exception):
                        loop.stop()

            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(_stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


def build_timeseries_sink(config, logger: logging.Logger) -> Optional[TimeSeriesSink]:
    enabled = bool(getattr(config, "TSDB_ENABLED", False))
    if not enabled:
        return None
    backend = str(getattr(config, "TSDB_BACKEND", "influx") or "influx").strip().lower()
    if backend in {"none", ""}:
        return None
    if backend == "influx":
        try:
            base_sink = InfluxDBSink(
                url=str(getattr(config, "TSDB_URL", "") or ""),
                token=str(getattr(config, "TSDB_TOKEN", "") or ""),
                org=str(getattr(config, "TSDB_ORG", "") or ""),
                bucket=str(getattr(config, "TSDB_BUCKET", "") or ""),
                logger=logger,
            )
            async_enabled = bool(getattr(config, "TSDB_ASYNC_WRITER_ENABLED", True))
            if async_enabled:
                queue_size = int(getattr(config, "TSDB_QUEUE_MAXSIZE", 2000))
                return AsyncQueueTimeSeriesSink(base_sink, logger=logger, max_queue=queue_size)
            return base_sink
        except Exception as e:
            logger.warning("TSDB sink disabled: %s", e)
            return None
    logger.warning("Unknown TSDB backend '%s'; sink disabled.", backend)
    return None

