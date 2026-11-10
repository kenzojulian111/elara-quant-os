#!/usr/bin/env python3
"""Thin CLI wrapper for local Elara bootstrap."""

from __future__ import annotations

import argparse

from dev_bootstrap.bootstrap_runner import DevBootstrapConfig, run_bootstrap
from dev_bootstrap.service_manager import DEFAULT_REDIS_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start Redis + ElaraTrader + ElaraCore locally.")
    parser.add_argument("--addr", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--core-port", type=int, default=5000)
    parser.add_argument("--trader-port", type=int, default=8768)
    parser.add_argument("--redis-version", default=DEFAULT_REDIS_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_bootstrap(
        DevBootstrapConfig(
            addr=args.addr,
            redis_port=args.redis_port,
            core_port=args.core_port,
            trader_port=args.trader_port,
            redis_version=args.redis_version,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

