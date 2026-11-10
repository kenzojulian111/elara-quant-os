from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any


class AsyncRunner:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        # Backward-compatible accessor for callers that still expect a public loop attribute.
        return self._ensure_loop()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._thread and self._thread.is_alive():
                return self._loop

            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(target=_run, name="elara-async-runner", daemon=True)
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    def submit_coroutine(self, coro) -> concurrent.futures.Future:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def run_coroutine(self, coro, timeout: float | None = None) -> Any:
        return self.submit_coroutine(coro).result(timeout=timeout)

    def run_coroutine_fire_and_forget(self, coro) -> concurrent.futures.Future:
        return self.submit_coroutine(coro)

    def shutdown(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None
        if not loop:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        if thread:
            thread.join(timeout=1.0)


async_runner = AsyncRunner()

