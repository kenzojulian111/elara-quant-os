import argparse
import os

from trading_core.market.replay_engine import CSVReplayEngine
from trading_core.orchestration.research_pipeline import ResearchPipeline
from trading_core.strategy.mean_reversion import MeanReversionStrategy
from trading_core.strategy.trend_following import TrendFollowingStrategy


def main():
    parser = argparse.ArgumentParser(description="Run simulation-driven research pipeline.")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV (supports optional funding/order_book fields).")
    parser.add_argument("--symbol", default="BTC", help="Symbol label for the replay data.")
    parser.add_argument("--output", default="runtime/strategy_library.json", help="Output path for strategy library.")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade pct (0.01 = 1%).")
    args = parser.parse_args()

    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(os.getcwd(), output)

    replay = CSVReplayEngine(args.csv, args.symbol)
    strategies = [
        TrendFollowingStrategy(),
        MeanReversionStrategy(),
    ]

    pipeline = ResearchPipeline(
        replay_engine=replay,
        strategies=strategies,
        library_path=output,
        risk_per_trade_pct=args.risk,
    )
    library = pipeline.run()
    print(f"Saved {len(library.list_profiles())} strategies -> {output}")


if __name__ == "__main__":
    main()

