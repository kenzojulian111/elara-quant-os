from __future__ import annotations

import argparse

from trading_core.market.replay_engine import CSVReplayEngine
from trading_core.orchestration.paper_pipeline import PaperPipelineConfig
from trading_core.orchestration.paper_runner import PaperTradingRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Elara's governed paper-trading MVP against CSV market data.")
    parser.add_argument("csv", help="CSV with timestamp, open, high, low, close, volume columns")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--ledger", default="runtime/paper_ledger.jsonl")
    parser.add_argument("--state", default="runtime/paper_execution_state.json")
    parser.add_argument("--max-market-gap-sec", type=float, default=900.0)
    args = parser.parse_args()

    runner = PaperTradingRunner.with_trend_strategy(
        config=PaperPipelineConfig(ledger_path=args.ledger, paper_state_path=args.state),
        max_market_gap_sec=args.max_market_gap_sec,
    )
    report = runner.run(CSVReplayEngine(args.csv, args.symbol))
    print(
        {
            "processed_states": report.processed_states,
            "executions": len(report.receipts),
            "rejected_states": report.rejected_states,
            "status": "ok" if report.ok else "market_data_rejected",
        }
    )
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
