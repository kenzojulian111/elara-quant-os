import argparse
import os

from trading_core.library.strategy_library import StrategyLibrary
from trading_core.market.feature_engine import add_market_features
from trading_core.market.funding_loader import load_funding_csv
from trading_core.market.macro_loader import load_macro_csv
from trading_core.market.merge_pipeline import merge_market_sources
from trading_core.market.ohlcv_loader import load_ohlcv_csv
from trading_core.market.order_book_loader import load_order_book_csv
from trading_core.market.replay_engine import DataFrameReplayEngine
from trading_core.orchestration.portfolio_brain import PortfolioBrain
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline


def main():
    parser = argparse.ArgumentParser(description="Run shadow trading pipeline on historical data.")
    parser.add_argument("--ohlcv", required=True, help="Path to OHLCV CSV.")
    parser.add_argument("--orderbook", default="", help="Path to order book CSV (optional).")
    parser.add_argument("--funding", default="", help="Path to funding CSV (optional).")
    parser.add_argument("--macro", default="", help="Path to macro CSV (optional).")
    parser.add_argument("--library", required=True, help="Path to strategy library JSON.")
    parser.add_argument("--weights", default="", help="Optional weights JSON to bias allocations.")
    parser.add_argument("--symbol", default="BTC", help="Symbol label.")
    parser.add_argument("--ledger", default="runtime/shadow_ledger.jsonl", help="Ledger output path.")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade pct.")
    parser.add_argument("--max-risk", type=float, default=0.02, help="Max risk per trade pct.")
    parser.add_argument("--max-dd", type=float, default=15.0, help="Max drawdown pct.")
    args = parser.parse_args()

    ohlcv = load_ohlcv_csv(args.ohlcv)
    orderbook = load_order_book_csv(args.orderbook) if args.orderbook else None
    funding = load_funding_csv(args.funding) if args.funding else None
    macro = load_macro_csv(args.macro) if args.macro else None

    merged = merge_market_sources(ohlcv, orderbook=orderbook, funding=funding, macro=macro)
    merged = add_market_features(merged)

    library_path = _abs_path(args.library)
    ledger_path = _abs_path(args.ledger)
    weights_path = _abs_path(args.weights) if args.weights else ""

    library = StrategyLibrary.load(library_path)
    portfolio = PortfolioBrain(library, weight_store_path=weights_path or None)
    config = ShadowPipelineConfig(
        risk_per_trade_pct=args.risk,
        max_risk_per_trade_pct=args.max_risk,
        max_drawdown_pct=args.max_dd,
        ledger_path=ledger_path,
    )
    pipeline = ShadowTradingPipeline(portfolio, config=config)

    replay = DataFrameReplayEngine(merged, symbol=args.symbol)
    receipts = 0
    for state in replay:
        receipts += len(pipeline.process_state(state, current_drawdown_pct=0.0))

    print(f"Shadow pipeline logged {receipts} execution records -> {ledger_path}")


def _abs_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


if __name__ == "__main__":
    main()

