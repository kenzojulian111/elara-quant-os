from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import health_client, service_manager


@dataclass(frozen=True)
class DevBootstrapConfig:
    addr: str = "127.0.0.1"
    redis_port: int = 6379
    core_port: int = 5000
    trader_port: int = 8768
    redis_version: str = service_manager.DEFAULT_REDIS_VERSION


@dataclass(frozen=True)
class BootstrapSummary:
    core_health: str = "unknown"
    redis_health: str = "unknown"
    db_health: str = "unknown"
    exchange_health: str = "unknown"
    trader_running: bool = False
    trader_simulation_fallback: bool = False
    trader_control_state: str = "unknown"
    logs_dir: str = ""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run_bootstrap(config: DevBootstrapConfig) -> BootstrapSummary:
    root = project_root()
    logs_dir = root / "runtime" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    python = service_manager.find_python(root)
    redis_dir = root.parent / "tools" / "redis"

    service_manager.start_redis(
        address=config.addr,
        port=config.redis_port,
        logs_dir=logs_dir,
        redis_dir=redis_dir,
        redis_version=config.redis_version,
    )
    service_manager.start_python_service(
        name="ElaraTrader",
        python=python,
        working_directory=root,
        args=[str(Path("apps") / "elara_trader" / "main.py")],
        address=config.addr,
        port=config.trader_port,
        stdout_path=logs_dir / "trader_stdout.log",
        stderr_path=logs_dir / "trader_stderr.log",
    )
    service_manager.start_python_service(
        name="ElaraCore",
        python=python,
        working_directory=root,
        args=[str(Path("apps") / "elara_core" / "main.py")],
        address=config.addr,
        port=config.core_port,
        stdout_path=logs_dir / "core_stdout.log",
        stderr_path=logs_dir / "core_stderr.log",
    )

    summary = BootstrapSummary(logs_dir=str(logs_dir))
    try:
        core = health_client.fetch_core_health(f"http://{config.addr}:{config.core_port}", timeout_s=5.0)
        summary = BootstrapSummary(
            core_health=core.overall_status,
            redis_health=core.check_status("redis"),
            db_health=core.check_status("db"),
            exchange_health=core.check_status("exchange"),
            trader_running=summary.trader_running,
            trader_simulation_fallback=summary.trader_simulation_fallback,
            trader_control_state=summary.trader_control_state,
            logs_dir=summary.logs_dir,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Health check failed: {exc}")

    try:
        trader = health_client.fetch_trader_status(f"http://{config.addr}:{config.trader_port}", timeout_s=5.0)
        summary = BootstrapSummary(
            core_health=summary.core_health,
            redis_health=summary.redis_health,
            db_health=summary.db_health,
            exchange_health=summary.exchange_health,
            trader_running=trader.is_running,
            trader_simulation_fallback=trader.simulation_fallback,
            trader_control_state=trader.control_state,
            logs_dir=summary.logs_dir,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Trader status check failed: {exc}")

    print("")
    print("Endpoints:")
    print(f"- Core:   http://{config.addr}:{config.core_port}")
    print(f"- Health: http://{config.addr}:{config.core_port}/api/health")
    print(f"- Trader: http://{config.addr}:{config.trader_port}/status")
    print(f"Logs: {logs_dir}")
    print(
        "Health: "
        f"redis={summary.redis_health} db={summary.db_health} exchange={summary.exchange_health}"
    )
    print(
        "Trader: "
        f"running={summary.trader_running} "
        f"sim_fallback={summary.trader_simulation_fallback} "
        f"control_state={summary.trader_control_state}"
    )
    return summary

