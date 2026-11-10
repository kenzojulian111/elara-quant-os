#!/usr/bin/env python3
"""Unified entry point for Elara (Core, Trader, Webhook, or Full Local Stack)."""

from __future__ import annotations

import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Elara Unified Entry Point")
    parser.add_argument(
        "--role",
        choices=("core", "trader", "webhook", "all"),
        default=os.getenv("APP_ROLE", "core").strip().lower() or "core",
        help="Runtime role to launch (default: core, or 'all' to start both Trader and Core).",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="Bind host address")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")), help="Core/Webhook port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args, unknown = build_parser().parse_known_args(argv)
    role = args.role

    if role == "all":
        from dev_bootstrap.bootstrap_runner import DevBootstrapConfig, run_bootstrap

        run_bootstrap(DevBootstrapConfig(addr=args.host, core_port=args.port))
        return 0

    if role == "trader":
        from core.runtime_role import apply_role_defaults
        from core.trader_bootstrap import main as run_trader

        apply_role_defaults("trader")
        run_trader()
        return 0

    if role == "webhook":
        import uvicorn

        uvicorn.run("apps.webhook_fastapi.main:app", host=args.host, port=args.port, reload=False)
        return 0

    from core.runtime_role import apply_role_defaults
    from core.app_bootstrap import main as run_core

    apply_role_defaults("core")
    run_core()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
