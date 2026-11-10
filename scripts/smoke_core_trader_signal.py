import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections import deque

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core import app_bootstrap
from core.events import parse_trade_command_message
from skills.advanced.trading import daemon_runner
from utils.common import async_runner


class FakePubSub:
    def __init__(self, redis_client):
        self._redis = redis_client
        self._channels = []
        self._queue = deque()
        self._closed = False

    def subscribe(self, channel):
        if self._closed:
            return
        self._channels.append(channel)
        self._redis._register_subscriber(channel, self)

    def get_message(self, timeout=1.0):
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        while not self._closed and time.time() <= deadline:
            if self._queue:
                return self._queue.popleft()
            time.sleep(0.01)
        return None

    def _push(self, channel, payload):
        if self._closed:
            return
        self._queue.append({"type": "message", "channel": channel, "data": payload})

    def close(self):
        self._closed = True
        for channel in list(self._channels):
            self._redis._unregister_subscriber(channel, self)
        self._channels = []


class FakeRedis:
    def __init__(self):
        self._subs = {}
        self._lists = {}
        self._kv = {}
        self._lock = threading.Lock()

    def _register_subscriber(self, channel, subscriber):
        with self._lock:
            self._subs.setdefault(channel, []).append(subscriber)

    def _unregister_subscriber(self, channel, subscriber):
        with self._lock:
            subs = self._subs.get(channel, [])
            if subscriber in subs:
                subs.remove(subscriber)
            if not subs and channel in self._subs:
                del self._subs[channel]

    def publish(self, channel, payload):
        with self._lock:
            subs = list(self._subs.get(channel, []))
        for sub in subs:
            sub._push(channel, payload)
        return len(subs)

    def pubsub(self, ignore_subscribe_messages=True):
        return FakePubSub(self)

    def lpush(self, key, value):
        with self._lock:
            self._lists.setdefault(key, [])
            self._lists[key].insert(0, value)

    def rpop(self, key):
        with self._lock:
            data = self._lists.get(key, [])
            if not data:
                return None
            return data.pop()

    def lrange(self, key, start, end):
        with self._lock:
            data = list(self._lists.get(key, []))
        if end == -1:
            return data[start:]
        return data[start : end + 1]

    def delete(self, key):
        with self._lock:
            self._lists.pop(key, None)
            self._kv.pop(key, None)

    def set(self, key, value):
        with self._lock:
            self._kv[key] = value

    def get(self, key):
        with self._lock:
            return self._kv.get(key)


async def _run_online_flow(fake_redis, command_path, redis_prefix, channel):
    stop_event = asyncio.Event()
    bridge_task = asyncio.create_task(
        daemon_runner._trade_signal_bridge(
        stop_event=stop_event,
        redis_client=fake_redis,
        redis_prefix=redis_prefix,
        command_path=command_path,
    ))
    await asyncio.sleep(0.2)

    callback = app_bootstrap._build_life_signal_callback()
    if callback is None:
        raise RuntimeError("Life signal callback was not created.")

    callback(
        {
            "allowed": False,
            "reason": "smoke-test-stop",
            "user_id": "smoke_user",
            "ts": time.time(),
        }
    )

    deadline = time.time() + 3.0
    cmds = []
    while time.time() < deadline and not cmds:
        cmds = daemon_runner._pop_commands(command_path, fake_redis, redis_prefix)
        await asyncio.sleep(0.05)

    stop_event.set()
    try:
        await asyncio.wait_for(bridge_task, timeout=2.0)
    except asyncio.TimeoutError:
        bridge_task.cancel()
        try:
            await bridge_task
        except asyncio.CancelledError:
            pass

    if "stop" not in cmds:
        raise RuntimeError(f"Trader did not receive expected 'stop' command via Redis channel={channel}.")


def _run_offline_flow(command_path):
    callback = app_bootstrap._build_life_signal_callback()
    if callback is None:
        raise RuntimeError("Offline callback should still exist (file fallback).")

    if os.path.exists(command_path):
        os.remove(command_path)

    callback(
        {
            "allowed": True,
            "reason": "smoke-test-resume",
            "user_id": "smoke_user",
            "ts": time.time(),
        }
    )

    deadline = time.time() + 2.0
    while time.time() < deadline and not os.path.exists(command_path):
        time.sleep(0.05)

    if not os.path.exists(command_path):
        raise RuntimeError("Offline fallback did not write trading command file.")

    with open(command_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    parsed = parse_trade_command_message(payload)
    if not parsed or str(parsed.get("cmd") or "").lower().strip() != "resume":
        raise RuntimeError("Offline fallback wrote unexpected command payload.")


def main():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    channel = "elara:test:trade_signal"
    redis_prefix = "elara:test:"

    old_channel_core = getattr(app_bootstrap.Config, "TRADING_SIGNAL_CHANNEL", channel)
    old_command_core = getattr(app_bootstrap.Config, "TRADING_COMMAND_PATH", "")
    old_channel_trader = getattr(daemon_runner.Config, "TRADING_SIGNAL_CHANNEL", channel)

    old_get_redis_core = app_bootstrap.get_redis
    old_get_redis_trader = daemon_runner.get_redis

    with tempfile.TemporaryDirectory(prefix="elara-smoke-") as tmpdir:
        command_path = os.path.join(tmpdir, "trading_command.json")

        setattr(app_bootstrap.Config, "TRADING_SIGNAL_CHANNEL", channel)
        setattr(app_bootstrap.Config, "TRADING_COMMAND_PATH", command_path)
        setattr(daemon_runner.Config, "TRADING_SIGNAL_CHANNEL", channel)

        fake_redis = FakeRedis()
        app_bootstrap.get_redis = lambda: fake_redis
        daemon_runner.get_redis = lambda: fake_redis

        try:
            asyncio.run(_run_online_flow(fake_redis, command_path, redis_prefix, channel))
            print("[PASS] online signal flow: Core publish -> Trader receive via Redis")

            app_bootstrap.get_redis = lambda: None
            daemon_runner.get_redis = lambda: None
            _run_offline_flow(command_path)
            print("[PASS] offline signal flow: Core fallback file command is written")
        finally:
            app_bootstrap.get_redis = old_get_redis_core
            daemon_runner.get_redis = old_get_redis_trader
            setattr(app_bootstrap.Config, "TRADING_SIGNAL_CHANNEL", old_channel_core)
            setattr(app_bootstrap.Config, "TRADING_COMMAND_PATH", old_command_core)
            setattr(daemon_runner.Config, "TRADING_SIGNAL_CHANNEL", old_channel_trader)
            try:
                async_runner.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    main()

