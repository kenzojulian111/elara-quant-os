import argparse
import json
import os

from trading_core.market.feature_engine import add_market_features
from trading_core.market.funding_loader import load_funding_csv
from trading_core.market.macro_loader import load_macro_csv
from trading_core.market.merge_pipeline import merge_market_sources
from trading_core.market.ohlcv_loader import load_ohlcv_csv
from trading_core.market.order_book_loader import load_order_book_csv
from trading_core.simulation.scenario_runner import ScenarioSimulation
from trading_core.strategy.mean_reversion import MeanReversionStrategy
from trading_core.strategy.trend_following import TrendFollowingStrategy


def main():
    parser = argparse.ArgumentParser(description="Run scenario simulation pipeline for BTC.")
    parser.add_argument("--ohlcv", required=True, help="Path to OHLCV CSV.")
    parser.add_argument("--orderbook", default="", help="Path to order book CSV (optional).")
    parser.add_argument("--funding", default="", help="Path to funding CSV (optional).")
    parser.add_argument("--macro", default="", help="Path to macro CSV (optional).")
    parser.add_argument("--symbol", default="BTC", help="Symbol label.")
    parser.add_argument("--output", default="runtime/simulation_report.json", help="Output JSON report.")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade pct.")
    args = parser.parse_args()

    ohlcv = load_ohlcv_csv(args.ohlcv)
    orderbook = load_order_book_csv(args.orderbook) if args.orderbook else None
    funding = load_funding_csv(args.funding) if args.funding else None
    macro = load_macro_csv(args.macro) if args.macro else None

    merged = merge_market_sources(ohlcv, orderbook=orderbook, funding=funding, macro=macro)
    merged = add_market_features(merged)

    strategies = [
        TrendFollowingStrategy(),
        MeanReversionStrategy(),
    ]
    simulator = ScenarioSimulation(risk_per_trade_pct=args.risk)

    reports = []
    for strategy in strategies:
        report = simulator.run(strategy, merged, args.symbol)
        reports.append(report)

    ranking = _rank_strategies(reports)
    payload = {
        "symbol": args.symbol,
        "strategies": [_serialize_report(report) for report in reports],
        "ranking": ranking,
    }

    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(os.getcwd(), output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
    print(f"Saved report -> {output}")


def _serialize_report(report):
    return {
        "strategy_id": report.strategy_id,
        "scenarios": [
            {"scenario": item.scenario, "metrics": item.metrics}
            for item in report.reports
        ],
    }


def _rank_strategies(reports):
    scored = []
    for report in reports:
        max_drawdown = _worst_drawdown(report)
        avg_sharpe = _avg_metric(report, "sharpe")
        scored.append(
            {
                "strategy_id": report.strategy_id,
                "max_drawdown_pct": max_drawdown,
                "avg_sharpe": avg_sharpe,
            }
        )
    scored.sort(key=lambda item: (item["max_drawdown_pct"], -item["avg_sharpe"]))
    return scored


def _worst_drawdown(report):
    values = [float(item.metrics.get("max_drawdown_pct", 0.0)) for item in report.reports]
    return max(values) if values else 0.0


def _avg_metric(report, key: str) -> float:
    values = [float(item.metrics.get(key, 0.0)) for item in report.reports]
    if not values:
        return 0.0
    return sum(values) / float(len(values))


if __name__ == "__main__":
    main()

