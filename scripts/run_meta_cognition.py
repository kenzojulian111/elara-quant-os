import argparse
import os

from trading_core.library.strategy_library import StrategyLibrary
from trading_core.meta_cognition.engine import MetaCognitionConfig, MetaCognitionEngine


def main():
    parser = argparse.ArgumentParser(description="Run meta-cognition weight adjustment.")
    parser.add_argument("--library", required=True, help="Path to strategy_library.json.")
    parser.add_argument("--weights", default="runtime/strategy_weights.json", help="Output weights path.")
    parser.add_argument("--ledger", default="runtime/meta_ledger.jsonl", help="Ledger output path.")
    parser.add_argument("--drawdown", type=float, default=12.0, help="Drawdown limit pct for penalty.")
    parser.add_argument("--sharpe", type=float, default=1.0, help="Sharpe threshold for boost.")
    parser.add_argument("--winrate", type=float, default=0.55, help="Win-rate threshold for boost.")
    parser.add_argument("--penalty", type=float, default=0.6, help="Penalty multiplier.")
    parser.add_argument("--boost", type=float, default=1.15, help="Boost multiplier.")
    parser.add_argument("--verbose", action="store_true", help="Print adjustments detail.")
    args = parser.parse_args()

    library_path = _abs_path(args.library)
    weights_path = _abs_path(args.weights)
    ledger_path = _abs_path(args.ledger)

    library = StrategyLibrary.load(library_path)
    config = MetaCognitionConfig(
        drawdown_limit_pct=args.drawdown,
        sharpe_boost_threshold=args.sharpe,
        win_rate_boost_threshold=args.winrate,
        penalty_multiplier=args.penalty,
        boost_multiplier=args.boost,
        weight_store_path=weights_path,
        ledger_path=ledger_path,
    )
    engine = MetaCognitionEngine(config)
    report = engine.run(library)

    print(f"Meta-cognition updated {len(report.adjustments)} strategies -> {weights_path}")
    if args.verbose:
        for adj in report.adjustments:
            print(f"{adj.strategy_id}: x{adj.multiplier:.2f} ({adj.reason})")


def _abs_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


if __name__ == "__main__":
    main()

