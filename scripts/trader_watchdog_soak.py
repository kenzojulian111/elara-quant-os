import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from utils.redis_helper import get_redis


def _heartbeat_payload():
    return {
        "ts": time.time(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "source": "watchdog_soak_runner",
    }


def _write_heartbeat(redis_client, heartbeat_key, heartbeat_path):
    payload = _heartbeat_payload()

    if heartbeat_path:
        os.makedirs(os.path.dirname(heartbeat_path) or ".", exist_ok=True)
        with open(heartbeat_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    if redis_client and heartbeat_key:
        redis_client.set(heartbeat_key, json.dumps(payload))


def _enqueue_stop(redis_client, redis_prefix, command_path):
    payload = {
        "cmd": "stop",
        "priority": 1,
        "source": "watchdog_soak_runner",
        "reason": "soak_complete",
        "ts": time.time(),
    }
    if redis_client:
        try:
            redis_client.lpush(redis_prefix + "trading_command", json.dumps(payload))
            return
        except Exception:
            pass

    os.makedirs(os.path.dirname(command_path) or ".", exist_ok=True)
    with open(command_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _spawn_daemon(args):
    env = os.environ.copy()
    env["TRADING_MODE"] = args.mode
    env["TRADER_REQUIRE_CORE_HEARTBEAT"] = "1"
    env["TRADER_CORE_HEARTBEAT_STALE_SEC"] = str(args.stale_sec)
    env["TRADER_CORE_HEARTBEAT_CHECK_SEC"] = str(args.check_sec)
    env["TRADER_CORE_HEARTBEAT_GRACE_SEC"] = str(args.grace_sec)
    env["DB_AUTO_CREATE_TABLES"] = "1"
    env["DATABASE_URL"] = f"sqlite:///{args.db_path}"
    env["EXCHANGE_CONNECT_RETRIES"] = "1"
    env["EXCHANGE_FALLBACKS"] = ""
    env["EXCHANGE_TIMEOUT_MS"] = str(max(2000, int(args.exchange_timeout_ms)))
    env["MULTIPAIR_ENABLED"] = "0"
    env["TRADING_SYMBOL"] = args.symbol
    if args.sandbox:
        env["PAPER_USE_SANDBOX"] = "1"

    cmd = [sys.executable, "-m", "skills.advanced.trading.daemon_runner"]
    return subprocess.Popen(cmd, cwd=ROOT, env=env)


def main():
    parser = argparse.ArgumentParser(description="Run trader daemon soak with core-heartbeat watchdog.")
    parser.add_argument("--duration-sec", type=int, default=86400, help="Heartbeat active duration.")
    parser.add_argument("--mode", type=str, default="paper", choices=["paper", "live"], help="Trader mode.")
    parser.add_argument("--sandbox", action="store_true", help="Enable exchange sandbox when supported.")
    parser.add_argument("--heartbeat-interval-sec", type=int, default=max(1, int(getattr(Config, "CORE_HEARTBEAT_INTERVAL_SEC", 5))))
    parser.add_argument("--stale-sec", type=int, default=max(5, int(getattr(Config, "TRADER_CORE_HEARTBEAT_STALE_SEC", 30))))
    parser.add_argument("--check-sec", type=int, default=max(2, int(getattr(Config, "TRADER_CORE_HEARTBEAT_CHECK_SEC", 5))))
    parser.add_argument("--grace-sec", type=int, default=max(5, int(getattr(Config, "TRADER_CORE_HEARTBEAT_GRACE_SEC", 120))))
    parser.add_argument("--symbol", type=str, default="BTC/USDT", help="Trading symbol used during soak.")
    parser.add_argument("--exchange-timeout-ms", type=int, default=4000, help="Exchange HTTP timeout per call.")
    parser.add_argument(
        "--db-path",
        type=str,
        default=os.path.join(ROOT, "runtime", "watchdog_soak.db"),
        help="SQLite path for isolated soak DB.",
    )
    parser.add_argument("--verify-stale-stop", action="store_true", help="After soak duration, stop heartbeat and verify daemon auto-stops.")
    args = parser.parse_args()
    args.db_path = os.path.abspath(args.db_path)

    redis_client = get_redis()
    redis_prefix = str(getattr(Config, "REDIS_PREFIX", "elara:") or "elara:")
    heartbeat_key = str(getattr(Config, "CORE_HEARTBEAT_KEY", redis_prefix + "core_heartbeat") or "")
    heartbeat_path = str(
        getattr(
            Config,
            "CORE_HEARTBEAT_PATH",
            os.path.join(ROOT, "runtime", "core_heartbeat.json"),
        )
        or ""
    )
    command_path = str(getattr(Config, "TRADING_COMMAND_PATH", os.path.join(ROOT, "runtime", "trading_command.json")) or "")

    proc = _spawn_daemon(args)
    print(f"[soak] trader daemon started pid={proc.pid}, mode={args.mode}, duration={args.duration_sec}s")
    print(f"[soak] heartbeat key={heartbeat_key or '-'} path={heartbeat_path or '-'} interval={args.heartbeat_interval_sec}s")
    print(f"[soak] using isolated DB: {args.db_path}")

    start = time.time()
    while (time.time() - start) < args.duration_sec:
        if proc.poll() is not None:
            raise RuntimeError(f"Trader daemon exited early with code={proc.returncode}")
        _write_heartbeat(redis_client, heartbeat_key, heartbeat_path)
        time.sleep(max(1, args.heartbeat_interval_sec))

    if args.verify_stale_stop:
        print("[soak] stopping heartbeat to verify watchdog stale shutdown...")
        wait_limit = max(60, args.grace_sec + args.stale_sec + 60)
        deadline = time.time() + wait_limit
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"[soak] watchdog verification success: daemon exited code={proc.returncode}")
                return
            time.sleep(1)
        _enqueue_stop(redis_client, redis_prefix, command_path)
        raise RuntimeError("Watchdog stale-stop verification failed (daemon still alive after stale window).")

    _enqueue_stop(redis_client, redis_prefix, command_path)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.terminate()
    print("[soak] completed and stop command sent.")


if __name__ == "__main__":
    main()

