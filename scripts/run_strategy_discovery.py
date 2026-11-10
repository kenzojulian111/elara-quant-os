import argparse
import os

from trading_core.market.feature_engine import add_market_features
from trading_core.market.funding_loader import load_funding_csv
from trading_core.market.macro_loader import load_macro_csv
from trading_core.market.merge_pipeline import merge_market_sources
from trading_core.market.ohlcv_loader import load_ohlcv_csv
from trading_core.market.order_book_loader import load_order_book_csv
from trading_core.orchestration.discovery_pipeline import DiscoveryConfig, StrategyDiscoveryPipeline
from trading_core.strategy.generator import generate_strategies


def main():
    parser = argparse.ArgumentParser(description="Run strategy discovery for BTC.")
    parser.add_argument("--ohlcv", required=True, help="Path to OHLCV CSV.")
    parser.add_argument("--orderbook", default="", help="Path to order book CSV (optional).")
    parser.add_argument("--funding", default="", help="Path to funding CSV (optional).")
    parser.add_argument("--macro", default="", help="Path to macro CSV (optional).")
    parser.add_argument("--symbol", default="BTC", help="Symbol label.")
    parser.add_argument("--output", default="runtime/strategy_library.json", help="Output library path.")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade pct.")
    parser.add_argument("--max-dd", type=float, default=15.0, help="Max drawdown threshold.")
    parser.add_argument("--max-candidates", type=int, default=12, help="Max strategy candidates.")
    args = parser.parse_args()

    ohlcv = load_ohlcv_csv(args.ohlcv)
    orderbook = load_order_book_csv(args.orderbook) if args.orderbook else None
    funding = load_funding_csv(args.funding) if args.funding else None
    macro = load_macro_csv(args.macro) if args.macro else None

    merged = merge_market_sources(ohlcv, orderbook=orderbook, funding=funding, macro=macro)
    merged = add_market_features(merged)

    strategies = generate_strategies(max_candidates=args.max_candidates)
    config = DiscoveryConfig(
        max_candidates=args.max_candidates,
        max_drawdown_pct=args.max_dd,
        risk_per_trade_pct=args.risk,
        objective="min_drawdown",
    )
    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(os.getcwd(), output)
    pipeline = StrategyDiscoveryPipeline(
        df=merged,
        symbol=args.symbol,
        strategies=strategies,
        library_path=output,
        config=config,
    )
    library = pipeline.run()
    print(f"Saved {len(library.list_profiles())} strategies -> {output}")


if __name__ == "__main__":
    main()

