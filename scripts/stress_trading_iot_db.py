import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from utils.health import snapshot as health_snapshot


def _configure_test_env(db_path: Path):
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"
    Config.SQLITE_BUSY_TIMEOUT = 5
    engine_opts = dict(getattr(Config, "SQLALCHEMY_ENGINE_OPTIONS", {}) or {})
    connect_args = dict(engine_opts.get("connect_args", {}) or {})
    connect_args["timeout"] = Config.SQLITE_BUSY_TIMEOUT
    connect_args["check_same_thread"] = False
    engine_opts["connect_args"] = connect_args
    Config.SQLALCHEMY_ENGINE_OPTIONS = engine_opts


def _create_db_app(db_path: Path):
    _configure_test_env(db_path)
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = Config.SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = Config.SQLALCHEMY_ENGINE_OPTIONS
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
    return app


def _write_trading_command(command_path: Path, cmd: str):
    payload = {"cmd": cmd, "ts": time.time()}
    with open(command_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _spam_trading(db_manager, command_path: Path, loops: int, stats: dict):
    cmds = ["stop", "flatten", "block", "resume"]
    for i in range(loops):
        cmd = random.choice(cmds)
        try:
            _write_trading_command(command_path, cmd)
            db_manager.log_command(
                source="stress_trading",
                intent=f"trading_{cmd}",
                text=f"trading command {cmd} #{i}",
                entities=[{"type": "cmd", "value": cmd}],
                user_id="stress_trading",
            )
            with stats["lock"]:
                stats["trading_ok"] += 1
        except Exception as e:
            with stats["lock"]:
                stats["trading_fail"] += 1
                stats["errors"].append(f"trading:{e}")


def _spam_device(db_manager, loops: int, stats: dict):
    actions = ["on", "off"]
    for i in range(loops):
        action = random.choice(actions)
        try:
            db_manager.log_command(
                source="stress_device",
                intent="turn_on_device" if action == "on" else "turn_off_device",
                text=f"lampu kamar {action} #{i}",
                entities=[
                    {"type": "device", "value": "lampu"},
                    {"type": "room", "value": "kamar"},
                    {"type": "action", "value": action},
                ],
                user_id="stress_device",
            )
            with stats["lock"]:
                stats["device_ok"] += 1
        except Exception as e:
            with stats["lock"]:
                stats["device_fail"] += 1
                stats["errors"].append(f"device:{e}")


def _spam_db_writes(db_manager, loops: int, stats: dict):
    for i in range(loops):
        try:
            db_manager.create_task(title=f"stress_task_{i}", priority="low")
            db_manager.log_command(
                source="stress_db",
                intent="db_write",
                text=f"db write #{i}",
                entities=[{"type": "seq", "value": i}],
                user_id="stress_db",
            )
            with stats["lock"]:
                stats["db_ok"] += 1
        except Exception as e:
            with stats["lock"]:
                stats["db_fail"] += 1
                stats["errors"].append(f"db:{e}")


def main():
    parser = argparse.ArgumentParser(description="Stress test trading/device concurrency over SQLite.")
    parser.add_argument("--trading-workers", type=int, default=6)
    parser.add_argument("--device-workers", type=int, default=6)
    parser.add_argument("--db-workers", type=int, default=4)
    parser.add_argument("--loops", type=int, default=120)
    args = parser.parse_args()

    runtime_dir = ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "stress_trading_iot.db"
    command_path = runtime_dir / "stress_trading_command.json"
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            db_path = runtime_dir / f"stress_trading_iot_{int(time.time())}.db"
    if command_path.exists():
        try:
            command_path.unlink()
        except PermissionError:
            command_path = runtime_dir / f"stress_trading_command_{int(time.time())}.json"

    app = _create_db_app(db_path)
    db_manager = DatabaseManager(app=app)

    stats = {
        "lock": threading.Lock(),
        "trading_ok": 0,
        "trading_fail": 0,
        "device_ok": 0,
        "device_fail": 0,
        "db_ok": 0,
        "db_fail": 0,
        "errors": [],
    }

    trading_workers = max(1, int(args.trading_workers))
    device_workers = max(1, int(args.device_workers))
    db_workers = max(1, int(args.db_workers))
    loops = max(1, int(args.loops))

    threads = []
    for _ in range(trading_workers):
        threads.append(threading.Thread(target=_spam_trading, args=(db_manager, command_path, loops, stats), daemon=True))
    for _ in range(device_workers):
        threads.append(threading.Thread(target=_spam_device, args=(db_manager, loops, stats), daemon=True))
    for _ in range(db_workers):
        threads.append(threading.Thread(target=_spam_db_writes, args=(db_manager, loops, stats), daemon=True))

    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = time.time() - started

    health = health_snapshot()
    db_lock_count = int(health.get("db_lock_count") or 0)
    db_lock_rate = float(health.get("db_lock_rate_per_min") or 0.0)
    db_write_lock_contention_count = int(health.get("db_write_lock_contention_count") or 0)
    db_write_lock_contention_rate = float(health.get("db_write_lock_contention_rate_per_min") or 0.0)

    print("STRESS TEST RESULT (Trading + Lamp + DB)")
    print(f"DurationSec={duration:.2f}")
    print(f"TradingOK={stats['trading_ok']} TradingFail={stats['trading_fail']}")
    print(f"DeviceOK={stats['device_ok']} DeviceFail={stats['device_fail']}")
    print(f"DBOpsOK={stats['db_ok']} DBOpsFail={stats['db_fail']}")
    print(f"DBLockCount={db_lock_count}")
    print(f"DBLockRatePerMin={db_lock_rate:.3f}")
    print(f"DBWriteLockContentionCount={db_write_lock_contention_count}")
    print(f"DBWriteLockContentionRatePerMin={db_write_lock_contention_rate:.3f}")
    print(f"SQLitePath={db_path}")
    print(f"TradingCommandPath={command_path}")
    print(f"MIGRATE_DB={1 if db_lock_count > 0 else 0}")
    if db_lock_count > 0:
        print("RECOMMENDATION=Switch to PostgreSQL/MySQL for 24/7 concurrency.")
    else:
        print("RECOMMENDATION=SQLite still stable for this load profile.")
    if stats["errors"]:
        print(f"SampleErrors={stats['errors'][:5]}")


if __name__ == "__main__":
    main()

