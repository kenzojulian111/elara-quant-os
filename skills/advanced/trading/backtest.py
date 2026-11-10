import argparse
import os
import copy
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from decimal import Decimal
from typing import Dict, List

import pandas as pd
import numpy as np

from skills.advanced.trading.config import TradingConfig
from skills.advanced.trading.strategies import SwingStrategy, ScalpingStrategy
from skills.advanced.trading.analyzers import MarketRegimeAnalyzer
from skills.advanced.trading.Portfoliomanager import PortfolioManager
from skills.advanced.trading.utils import setup_logger


class DummyDB:
    """In-memory DB interface for PortfolioManager in backtest."""
    def __init__(self):
        self.trades: Dict[str, Dict] = {}
        self.trade_updates: List[Dict] = []
        self.equity_logs: List[Dict] = []
        self._counter = 0

    def load_open_positions(self):
        return [dict(v, trade_id=k) for k, v in self.trades.items() if v.get("status", "OPEN") == "OPEN"]

    def log_equity(self, ts, eq):
        self.equity_logs.append({"timestamp": ts, "equity": float(eq)})

    def create_new_trade(self, data):
        self._counter += 1
        trade_id = f"bt_{self._counter}"
        payload = dict(data)
        payload.setdefault("status", "OPEN")
        self.trades[trade_id] = payload
        return trade_id

    def update_trade(self, trade_id, data):
        trade = self.trades.get(trade_id)
        if not trade:
            return False
        trade.update(data or {})
        return True

    def log_trade_update(self, data):
        self.trade_updates.append(dict(data or {}))

    def get_all_closed_trades_with_pnl(self):
        closed = []
        for trade_id, trade in self.trades.items():
            if trade.get("status") != "CLOSED":
                continue
            pnl_total = 0.0
            for upd in self.trade_updates:
                if upd.get("trade_id") != trade_id:
                    continue
                try:
                    pnl_total += float(upd.get("pnl") or 0)
                except Exception:
                    continue
            closed.append({"trade_id": trade_id, "final_pnl": pnl_total})
        return closed


def load_df_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    # Expect columns: timestamp/open/high/low/close/volume
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df[["open", "high", "low", "close", "volume"]].dropna()


def load_df_yfinance(symbol: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        tickers = list(dict.fromkeys(df.columns.get_level_values(-1)))
        if len(tickers) == 1:
            df.columns = [str(col[0]).lower() for col in df.columns]
        else:
            try:
                df = df.xs(symbol, axis=1, level=-1, drop_level=True)
            except Exception:
                df = df.xs(tickers[0], axis=1, level=-1, drop_level=True)
            df.columns = [str(c).lower() for c in df.columns]
    else:
        df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def calc_indicators(df: pd.DataFrame, params: dict, config: TradingConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema_short"] = out["close"].ewm(span=params.get("ema_short", 12), adjust=False).mean()
    out["ema_long"] = out["close"].ewm(span=params.get("ema_long", 26), adjust=False).mean()
    out["ema_trend"] = out["close"].ewm(span=getattr(config, "EMA_TREND_PERIOD", 200), adjust=False).mean()

    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(window=params.get("rsi", 14)).mean()
    loss = (-delta.clip(upper=0)).rolling(window=params.get("rsi", 14)).mean()
    rs = gain / loss.replace(0, pd.NA)
    out["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    ema_fast = out["close"].ewm(span=params.get("macd_fast", 12), adjust=False).mean()
    ema_slow = out["close"].ewm(span=params.get("macd_slow", 26), adjust=False).mean()
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd"].ewm(span=params.get("macd_signal", 9), adjust=False).mean()

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(params.get("atr", 14)).mean().fillna(0)
    out["atr_pct"] = ((out["atr"] / out["close"]) * 100).fillna(0)
    out["adx"] = (out["close"].pct_change().abs().rolling(getattr(config, "ADX_PERIOD", 14)).mean() * 100).fillna(0)

    vol_window = params.get("volume_ma", 20)
    out["volume_ma"] = out["volume"].rolling(vol_window).mean().fillna(out["volume"])
    return out


def _compute_risk_statistics(equity_series: List[float], trade_pnls: List[float], initial_capital: float) -> Dict[str, float]:
    metrics = {
        "max_drawdown_pct": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "calmar": 0.0,
        "expectancy": 0.0,
        "profit_factor": 0.0,
        "cagr_pct": 0.0,
    }

    if equity_series:
        peak = float(equity_series[0])
        max_dd = 0.0
        for val in equity_series:
            peak = max(peak, float(val))
            if peak > 0:
                max_dd = max(max_dd, (peak - float(val)) / peak)
        metrics["max_drawdown_pct"] = float(max_dd * 100.0)

    if len(equity_series) >= 3:
        eq = np.array(equity_series, dtype=float)
        prev = np.clip(eq[:-1], 1e-9, None)
        rets = np.diff(eq) / prev
        rets = rets[np.isfinite(rets)]
        if len(rets) >= 2:
            avg = float(np.mean(rets))
            std = float(np.std(rets, ddof=0))
            if std > 1e-12:
                metrics["sharpe"] = float((avg / std) * np.sqrt(252))
            downside = rets[rets < 0]
            if len(downside) >= 2:
                down_std = float(np.std(downside, ddof=0))
                if down_std > 1e-12:
                    metrics["sortino"] = float((avg / down_std) * np.sqrt(252))

            years = max(1e-9, len(rets) / 252.0)
            end_eq = float(eq[-1]) if len(eq) else float(initial_capital or 1.0)
            start_eq = max(1e-9, float(initial_capital or 1.0))
            if end_eq <= 0:
                cagr = -1.0
            else:
                cagr = (end_eq / start_eq) ** (1.0 / years) - 1.0
            metrics["cagr_pct"] = float(cagr * 100.0)
            dd_dec = max(1e-9, metrics["max_drawdown_pct"] / 100.0)
            metrics["calmar"] = float(cagr / dd_dec) if metrics["max_drawdown_pct"] > 0 else float(cagr * 10.0)

    if trade_pnls:
        wins = [float(x) for x in trade_pnls if float(x) > 0]
        losses = [float(x) for x in trade_pnls if float(x) <= 0]
        win_rate = (len(wins) / len(trade_pnls)) if trade_pnls else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        metrics["expectancy"] = float((win_rate * avg_win) - ((1.0 - win_rate) * abs(avg_loss)))
        gross_win = float(sum(wins))
        gross_loss = abs(float(sum(losses)))
        metrics["profit_factor"] = float(gross_win / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_win > 0 else 0.0)
    return metrics


def backtest(config: TradingConfig, df: pd.DataFrame):
    logger = setup_logger(config)
    db = DummyDB()
    pm = PortfolioManager(config, logger, db)
    strategies = {
        "swing": SwingStrategy(config, logger, {}),
        "scalping": ScalpingStrategy(config, logger, {})
    }
    strat = strategies.get(config.STRATEGY, strategies["swing"])
    regime = MarketRegimeAnalyzer(logger, config)

    params = config.STRATEGY_CONFIGS[config.STRATEGY]["params"]
    df = calc_indicators(df, params, config)

    trades = 0
    base_equity = float(config.INITIAL_CAPITAL)
    equity_series = []

    # Look-ahead guard:
    # signal for candle[i] is computed only from data until candle[i-1],
    # then executed at candle[i] open price.
    for idx in range(1, len(df)):
        row = df.iloc[idx]
        pm.update_and_check_positions(row)
        equity_series.append(float(pm.current_equity))

        ltf_slice = df.iloc[:idx]
        if ltf_slice.empty:
            continue
        signal_row = ltf_slice.iloc[-1]

        atr_val = signal_row.get("atr")
        if pd.isna(atr_val) or float(atr_val) <= 0:
            continue

        market_regime = regime.get_regime(ltf_slice)
        htf_trend = "UP" if signal_row.get("close", 0) >= signal_row.get("ema_trend", 0) else "DOWN"
        score, signal, reason = strat.calculate_confluence(ltf_slice, htf_trend, market_regime)

        if signal != "HOLD":
            before_open = len(pm.open_positions)
            exec_row = row.copy()
            if "open" in exec_row and not pd.isna(exec_row.get("open")):
                exec_row["close"] = exec_row.get("open")
            try:
                close_px = float(exec_row.get("close", row.get("close", 0)) or 0)
                high_px = float(row.get("high", close_px) or close_px)
                low_px = float(row.get("low", close_px) or close_px)
                if close_px > 0:
                    configured_spread_pct = max(0.0, float(getattr(config, "BACKTEST_SPREAD_BPS", 4.0))) / 100.0
                    candle_spread_pct = max(0.0, ((high_px - low_px) / close_px) * 100.0) * 0.15
                    spread_pct = max(configured_spread_pct, candle_spread_pct)
                    spread_max_pct = max(0.05, float(getattr(config, "BACKTEST_SPREAD_MAX_PCT", 1.0)))
                    if spread_pct > spread_max_pct:
                        continue
                    slip_ratio = max(0.0, float(getattr(config, "BACKTEST_SLIPPAGE_BPS", 6.0))) / 10000.0
                    total_edge = (spread_pct / 100.0 / 2.0) + slip_ratio
                    if signal == "BUY":
                        close_px = close_px * (1.0 + total_edge)
                    else:
                        close_px = close_px * max(0.0001, 1.0 - total_edge)
                    exec_row["close"] = close_px
            except Exception:
                pass
            pm.execute_trade(signal, exec_row, reason, score)
            if len(pm.open_positions) > before_open:
                trades += 1
                mark_price = Decimal(str(exec_row.get("close", row.get("close", 0))))
                unrealized = sum(
                    (mark_price - p["entry_price"]) * p["current_size"]
                    if p["direction"] == "LONG"
                    else (p["entry_price"] - mark_price) * p["current_size"]
                    for p in pm.open_positions
                )
                pm.current_equity = pm.current_cash + unrealized

    if pm.open_positions and not df.empty:
        last_row = df.iloc[-1]
        last_ts = last_row.name
        last_price = Decimal(str(last_row["close"]))
        for pos in pm.open_positions[:]:
            pm._close_position_by_part(pos, pos["current_size"], last_price, last_ts, "Backtest End Flatten")
        equity_series.append(float(pm.current_equity))

    closed_trades = db.get_all_closed_trades_with_pnl()
    wins = sum(1 for t in closed_trades if float(t.get("final_pnl", 0) or 0) > 0)
    closed_count = len(closed_trades)
    final_eq = float(pm.current_equity)
    pnl = final_eq - base_equity
    win_rate = (wins / closed_count) if closed_count else 0
    trade_pnls = [float(t.get("final_pnl", 0) or 0) for t in closed_trades]
    stats = _compute_risk_statistics(equity_series, trade_pnls, initial_capital=base_equity)
    dd = float(stats.get("max_drawdown_pct", 0.0))
    sharpe = float(stats.get("sharpe", 0.0))
    sortino = float(stats.get("sortino", 0.0))
    calmar = float(stats.get("calmar", 0.0))
    expectancy = float(stats.get("expectancy", 0.0))
    profit_factor = float(stats.get("profit_factor", 0.0))
    cagr_pct = float(stats.get("cagr_pct", 0.0))

    logger.info(
        f"Backtest DONE | Trades(Open): {trades} | Trades(Closed): {closed_count} | "
        f"Final Equity: {final_eq:.2f} | PnL: {pnl:.2f} | MaxDD: {dd:.2f}% | "
        f"Sharpe: {sharpe:.2f} | Sortino: {sortino:.2f} | Calmar: {calmar:.2f} | "
        f"Expectancy: {expectancy:.2f}"
    )
    return {
        "trades_opened": int(trades),
        "trades_closed": int(closed_count),
        "final_equity": float(final_eq),
        "pnl": float(pnl),
        "win_rate": float(win_rate),
        "max_drawdown_pct": float(dd),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "expectancy": float(expectancy),
        "profit_factor": float(profit_factor),
        "cagr_pct": float(cagr_pct),
    }


def _safe_mean(values: List[float]) -> float:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return 0.0
    return float(sum(nums) / len(nums))


def _usd_to_idr(value_usd: float, usd_to_idr_rate: float) -> float:
    return float(float(value_usd or 0.0) * float(usd_to_idr_rate or 0.0))


def _build_requested_summary(
    metrics: Dict,
    *,
    symbol: str,
    strategy: str,
    dataset_source: str,
    dataset_period: str,
    dataset_interval: str,
    dataset_rows: int,
    dataset_start: str,
    dataset_end: str,
    initial_capital_usd: float,
    usd_to_idr_rate: float,
) -> Dict:
    base = metrics.get("baseline", metrics)
    base_final_equity = float(base.get("final_equity", 0.0) or 0.0)
    base_pnl = float(base.get("pnl", 0.0) or 0.0)
    base_win_rate_pct = float(base.get("win_rate", 0.0) or 0.0) * 100.0

    summary = {
        "symbol": str(symbol),
        "strategy": str(strategy),
        "dataset_source": str(dataset_source),
        "dataset_period": str(dataset_period),
        "dataset_interval": str(dataset_interval),
        "dataset_rows": int(dataset_rows or 0),
        "dataset_start": str(dataset_start or ""),
        "dataset_end": str(dataset_end or ""),
        "usd_to_idr_rate_assumption": float(usd_to_idr_rate),
        "initial_capital_usd": float(initial_capital_usd),
        "initial_capital_idr": _usd_to_idr(initial_capital_usd, usd_to_idr_rate),
        "final_equity_usd": float(base_final_equity),
        "final_equity_idr": _usd_to_idr(base_final_equity, usd_to_idr_rate),
        "final_pnl_usd": float(base_pnl),
        "final_pnl_idr": _usd_to_idr(base_pnl, usd_to_idr_rate),
        "final_profit_usd": float(base_pnl),
        "final_profit_idr": _usd_to_idr(base_pnl, usd_to_idr_rate),
        "final_win_rate_pct": float(base_win_rate_pct),
        "final_trades_opened": int(base.get("trades_opened", 0) or 0),
        "final_trades_closed": int(base.get("trades_closed", 0) or 0),
        "final_max_drawdown_pct": float(base.get("max_drawdown_pct", 0.0) or 0.0),
        "final_sharpe": float(base.get("sharpe", 0.0) or 0.0),
    }

    stress = metrics.get("stress_suite", {}) or {}
    stress_summary = stress.get("summary", {}) or {}
    if stress_summary:
        stress_worst_pnl = float(stress_summary.get("worst_pnl", 0.0) or 0.0)
        summary["stress_scenario_count"] = int(stress_summary.get("scenario_count", 0) or 0)
        summary["stress_worst_pnl_usd"] = float(stress_worst_pnl)
        summary["stress_worst_pnl_idr"] = _usd_to_idr(stress_worst_pnl, usd_to_idr_rate)
        summary["stress_worst_max_drawdown_pct"] = float(stress_summary.get("worst_max_drawdown_pct", 0.0) or 0.0)

    mc = metrics.get("monte_carlo", {}) or {}
    mc_summary = mc.get("summary", {}) or {}
    if mc_summary:
        mc_p05 = float(mc_summary.get("pnl_p05", 0.0) or 0.0)
        summary["monte_carlo_runs"] = int(mc_summary.get("runs", metrics.get("monte_carlo", {}).get("runs", 0)) or 0)
        summary["monte_carlo_pnl_p05_usd"] = float(mc_p05)
        summary["monte_carlo_pnl_p05_idr"] = _usd_to_idr(mc_p05, usd_to_idr_rate)
        summary["monte_carlo_probability_of_profit_pct"] = float(mc_summary.get("probability_of_profit", 0.0) or 0.0) * 100.0

    return summary


def _clone_config(config: TradingConfig) -> TradingConfig:
    out = TradingConfig()
    # Copy runtime overrides applied by CLI/caller.
    for key, value in getattr(config, "__dict__", {}).items():
        try:
            setattr(out, key, copy.deepcopy(value))
        except Exception:
            setattr(out, key, value)
    try:
        out.STRATEGY_CONFIGS = copy.deepcopy(getattr(config, "STRATEGY_CONFIGS"))
    except Exception:
        pass
    return out


def _apply_strategy_overrides(config: TradingConfig, overrides: Dict) -> TradingConfig:
    cfg = _clone_config(config)
    over = dict(overrides or {})
    if not over:
        return cfg
    strategy = str(getattr(cfg, "STRATEGY", "swing"))
    sc = cfg.STRATEGY_CONFIGS.get(strategy, {})
    if "params" in over and isinstance(over["params"], dict):
        sc.setdefault("params", {}).update(over["params"])
    if "confluence_threshold" in over:
        try:
            sc["confluence_threshold"] = int(over["confluence_threshold"])
        except Exception:
            pass
    cfg.STRATEGY_CONFIGS[strategy] = sc
    return cfg


def _objective_score(metrics: Dict, initial_capital: float) -> float:
    pnl = float(metrics.get("pnl", 0.0) or 0.0)
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    dd = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
    base = max(1.0, float(initial_capital or 1.0))
    pnl_pct = (pnl / base) * 100.0
    return (sharpe * 1.8) + pnl_pct - (dd * 0.65)


def _window_param_trials(config: TradingConfig, max_trials: int) -> List[Dict]:
    strategy = str(getattr(config, "STRATEGY", "swing"))
    sc = copy.deepcopy(config.STRATEGY_CONFIGS.get(strategy, {}))
    params = dict(sc.get("params", {}) or {})
    threshold = int(sc.get("confluence_threshold", 4))
    trials: List[Dict] = [{"params": {}, "confluence_threshold": threshold}]

    if strategy == "swing":
        ema_short = int(params.get("ema_short", 12))
        ema_long = int(params.get("ema_long", 26))
        atr = int(params.get("atr", 14))
        trials.extend(
            [
                {"params": {"ema_short": max(6, ema_short - 2)}, "confluence_threshold": max(2, threshold - 1)},
                {"params": {"ema_short": ema_short + 2}, "confluence_threshold": threshold},
                {"params": {"ema_long": max(12, ema_long - 4)}, "confluence_threshold": threshold},
                {"params": {"ema_long": ema_long + 4}, "confluence_threshold": min(8, threshold + 1)},
                {"params": {"atr": max(7, atr - 2)}, "confluence_threshold": threshold},
                {"params": {"atr": atr + 2}, "confluence_threshold": min(8, threshold + 1)},
            ]
        )
    else:
        ema_short = int(params.get("ema_short", 9))
        ema_long = int(params.get("ema_long", 21))
        rsi = int(params.get("rsi", 14))
        trials.extend(
            [
                {"params": {"ema_short": max(5, ema_short - 1)}, "confluence_threshold": max(3, threshold - 1)},
                {"params": {"ema_short": ema_short + 1}, "confluence_threshold": threshold},
                {"params": {"ema_long": max(10, ema_long - 2)}, "confluence_threshold": threshold},
                {"params": {"ema_long": ema_long + 2}, "confluence_threshold": min(9, threshold + 1)},
                {"params": {"rsi": max(7, rsi - 2)}, "confluence_threshold": threshold},
                {"params": {"rsi": rsi + 2}, "confluence_threshold": min(9, threshold + 1)},
            ]
        )

    dedup = []
    seen = set()
    for t in trials:
        key = (
            int(t.get("confluence_threshold", threshold)),
            tuple(sorted((t.get("params") or {}).items())),
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(t)
    return dedup[: max(1, int(max_trials))]


def _optimize_for_window(config: TradingConfig, train_slice: pd.DataFrame) -> Dict:
    if train_slice is None or train_slice.empty:
        return {"params": {}, "confluence_threshold": config.STRATEGY_CONFIGS.get(config.STRATEGY, {}).get("confluence_threshold", 4)}
    trials = _window_param_trials(config, int(getattr(config, "BACKTEST_WF_OPTIMIZE_TRIALS", 6)))
    best_overrides = trials[0] if trials else {"params": {}, "confluence_threshold": 4}
    best_score = float("-inf")
    for trial in trials:
        try:
            trial_cfg = _apply_strategy_overrides(config, trial)
            m = backtest(trial_cfg, train_slice)
            score = _objective_score(m, float(getattr(config, "INITIAL_CAPITAL", 1000)))
            if score > best_score:
                best_score = score
                best_overrides = trial
        except Exception:
            continue
    return best_overrides


def _run_backtest_window(config: TradingConfig, payload: Dict) -> Dict:
    test_slice = payload["test_slice"]
    cfg = _apply_strategy_overrides(config, payload.get("strategy_overrides") or {})
    metrics = backtest(cfg, test_slice)
    metrics["window"] = int(payload["window"])
    metrics["train_start"] = str(payload["train_start"])
    metrics["train_end"] = str(payload["train_end"])
    metrics["test_start"] = str(payload["test_start"])
    metrics["test_end"] = str(payload["test_end"])
    metrics["strategy_overrides"] = payload.get("strategy_overrides") or {}
    return metrics


def walk_forward_validate(
    config: TradingConfig,
    df: pd.DataFrame,
    train_bars: int = None,
    test_bars: int = None,
    step_bars: int = None,
):
    """
    Rolling walk-forward validation for stability checking.
    Note: strategy in current backtest is rule-based; this evaluates robustness
    across rolling out-of-sample windows.
    """
    train_bars = int(train_bars or getattr(config, "BACKTEST_WF_TRAIN_BARS", 600))
    test_bars = int(test_bars or getattr(config, "BACKTEST_WF_TEST_BARS", 200))
    step_bars = int(step_bars or getattr(config, "BACKTEST_WF_STEP_BARS", test_bars))
    train_bars = max(80, train_bars)
    test_bars = max(40, test_bars)
    step_bars = max(20, step_bars)

    n = len(df)
    if n < (train_bars + test_bars):
        return {
            "walk_forward": True,
            "windows": 0,
            "message": f"Insufficient data ({n} bars) for train={train_bars}, test={test_bars}",
            "summary": {},
            "windows_metrics": [],
        }

    windows_metrics: List[Dict] = []
    window_payloads: List[Dict] = []
    start = 0
    window_id = 1
    while (start + train_bars + test_bars) <= n:
        train_slice = df.iloc[start : start + train_bars]
        test_slice = df.iloc[start + train_bars : start + train_bars + test_bars]
        if test_slice.empty:
            break
        strategy_overrides = {}
        if bool(getattr(config, "BACKTEST_WF_OPTIMIZE_PER_WINDOW", True)):
            strategy_overrides = _optimize_for_window(config, train_slice)

        window_payloads.append(
            {
                "window": window_id,
                "train_start": str(train_slice.index[0]),
                "train_end": str(train_slice.index[-1]),
                "test_start": str(test_slice.index[0]),
                "test_end": str(test_slice.index[-1]),
                "test_slice": test_slice,
                "strategy_overrides": strategy_overrides,
            }
        )

        start += step_bars
        window_id += 1

    cpu_total = max(1, int(os.cpu_count() or 1))
    workers_cfg = int(getattr(config, "BACKTEST_PROCESS_WORKERS", 0) or 0)
    if workers_cfg <= 0:
        process_workers = min(len(window_payloads), max(1, cpu_total - 1))
    else:
        process_workers = min(len(window_payloads), max(1, workers_cfg))

    use_process_pool = process_workers > 1 and len(window_payloads) > 1
    if use_process_pool:
        try:
            ctx = get_context("spawn")
            with ProcessPoolExecutor(max_workers=process_workers, mp_context=ctx) as executor:
                futures = [
                    executor.submit(_run_backtest_window, config, payload)
                    for payload in window_payloads
                ]
                for fut in as_completed(futures):
                    windows_metrics.append(fut.result())
        except Exception:
            windows_metrics = []

    if not windows_metrics:
        for payload in window_payloads:
            windows_metrics.append(_run_backtest_window(config, payload))

    windows_metrics = sorted(windows_metrics, key=lambda item: int(item.get("window", 0)))

    if not windows_metrics:
        return {
            "walk_forward": True,
            "windows": 0,
            "message": "No valid windows generated.",
            "summary": {},
            "windows_metrics": [],
        }

    pnl_list = [m.get("pnl", 0.0) for m in windows_metrics]
    wr_list = [m.get("win_rate", 0.0) for m in windows_metrics]
    dd_list = [m.get("max_drawdown_pct", 0.0) for m in windows_metrics]
    sh_list = [m.get("sharpe", 0.0) for m in windows_metrics]
    eq_list = [m.get("final_equity", 0.0) for m in windows_metrics]
    positive_windows = sum(1 for p in pnl_list if float(p) > 0)

    summary = {
        "window_count": len(windows_metrics),
        "pnl_mean": _safe_mean(pnl_list),
        "pnl_median": float(np.median(pnl_list)) if pnl_list else 0.0,
        "win_rate_mean": _safe_mean(wr_list),
        "max_drawdown_mean_pct": _safe_mean(dd_list),
        "max_drawdown_worst_pct": float(max(dd_list)) if dd_list else 0.0,
        "sharpe_mean": _safe_mean(sh_list),
        "final_equity_mean": _safe_mean(eq_list),
        "positive_window_ratio": (positive_windows / len(windows_metrics)) if windows_metrics else 0.0,
    }
    return {
        "walk_forward": True,
        "windows": len(windows_metrics),
        "train_bars": train_bars,
        "test_bars": test_bars,
        "step_bars": step_bars,
        "process_pool_workers": process_workers if use_process_pool else 1,
        "summary": summary,
        "windows_metrics": windows_metrics,
    }


def purged_walk_forward_cv(
    config: TradingConfig,
    df: pd.DataFrame,
    *,
    splits: int = None,
    purge_bars: int = None,
    embargo_bars: int = None,
) -> Dict:
    n = len(df)
    splits = max(2, int(splits or getattr(config, "BACKTEST_PURGED_CV_SPLITS", 5)))
    purge_bars = max(0, int(purge_bars if purge_bars is not None else getattr(config, "BACKTEST_PURGE_BARS", 20)))
    embargo_bars = max(0, int(embargo_bars if embargo_bars is not None else getattr(config, "BACKTEST_EMBARGO_BARS", 20)))
    if n < 200:
        return {"purged_cv": True, "splits": 0, "summary": {}, "folds": [], "message": f"Insufficient bars: {n}"}

    test_size = max(40, n // (splits + 2))
    folds = []
    for i in range(splits):
        test_start = (i + 1) * test_size
        test_end = min(n, test_start + test_size)
        if test_end - test_start < 30:
            continue

        train_a_end = max(0, test_start - purge_bars)
        train_b_start = min(n, test_end + embargo_bars)
        train_parts = []
        if train_a_end > 0:
            train_parts.append(df.iloc[:train_a_end])
        if train_b_start < n:
            train_parts.append(df.iloc[train_b_start:])
        if not train_parts:
            continue
        train_df = pd.concat(train_parts, axis=0).dropna()
        test_df = df.iloc[test_start:test_end].dropna()
        if train_df.empty or test_df.empty or len(train_df) < 80:
            continue

        overrides = _optimize_for_window(config, train_df)
        cfg = _apply_strategy_overrides(config, overrides)
        metrics = backtest(cfg, test_df)
        metrics["fold"] = i + 1
        metrics["test_start"] = str(test_df.index[0])
        metrics["test_end"] = str(test_df.index[-1])
        metrics["train_size"] = int(len(train_df))
        metrics["test_size"] = int(len(test_df))
        metrics["strategy_overrides"] = overrides
        folds.append(metrics)

    if not folds:
        return {"purged_cv": True, "splits": 0, "summary": {}, "folds": [], "message": "No valid folds generated."}

    pnl = [float(x.get("pnl", 0.0) or 0.0) for x in folds]
    dd = [float(x.get("max_drawdown_pct", 0.0) or 0.0) for x in folds]
    sharpe = [float(x.get("sharpe", 0.0) or 0.0) for x in folds]
    sortino = [float(x.get("sortino", 0.0) or 0.0) for x in folds]
    calmar = [float(x.get("calmar", 0.0) or 0.0) for x in folds]
    expectancy = [float(x.get("expectancy", 0.0) or 0.0) for x in folds]
    summary = {
        "folds": len(folds),
        "pnl_mean": _safe_mean(pnl),
        "pnl_median": float(np.median(pnl)) if pnl else 0.0,
        "max_drawdown_worst_pct": float(max(dd)) if dd else 0.0,
        "sharpe_mean": _safe_mean(sharpe),
        "sortino_mean": _safe_mean(sortino),
        "calmar_mean": _safe_mean(calmar),
        "expectancy_mean": _safe_mean(expectancy),
        "positive_fold_ratio": float(sum(1 for p in pnl if p > 0) / len(folds)),
    }
    return {
        "purged_cv": True,
        "splits": len(folds),
        "purge_bars": purge_bars,
        "embargo_bars": embargo_bars,
        "summary": summary,
        "folds": folds,
    }


def _apply_stress_scenario(df: pd.DataFrame, *, scenario: str, crash_pct: float, vol_mult: float, liq_drop_pct: float) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    close = pd.to_numeric(out["close"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    vol = pd.to_numeric(out["volume"], errors="coerce").fillna(0)

    if scenario == "crash":
        start = max(1, int(len(out) * 0.62))
        end = min(len(out), start + max(4, int(len(out) * 0.05)))
        shock = max(0.0, min(0.95, float(crash_pct) / 100.0))
        factor = max(0.05, 1.0 - shock)
        close.iloc[start:end] = close.iloc[start:end] * factor
        open_.iloc[start:end] = open_.iloc[start:end] * max(0.05, factor * 1.01)
        high.iloc[start:end] = high.iloc[start:end] * max(0.05, factor * 1.02)
        low.iloc[start:end] = low.iloc[start:end] * max(0.02, factor * 0.98)
        vol.iloc[start:end] = vol.iloc[start:end] * (1.0 + abs(shock) * 3.0)
    elif scenario == "vol_spike":
        mult = max(1.0, float(vol_mult))
        mid = (high + low) / 2.0
        half = (high - low).abs() * (mult / 2.0)
        high = np.maximum(high, mid + half)
        low = np.minimum(low, mid - half)
    elif scenario == "liquidity_drought":
        drop = max(0.0, min(0.99, float(liq_drop_pct) / 100.0))
        vol = vol * max(0.01, (1.0 - drop))
    else:
        return out

    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["close"] = close
    out["volume"] = np.clip(vol, 0.0, None)
    out = out.dropna()
    return out


def run_stress_regime_suite(config: TradingConfig, df: pd.DataFrame) -> Dict:
    crash_pct = float(getattr(config, "BACKTEST_STRESS_CRASH_PCT", 12.0) or 12.0)
    vol_mult = float(getattr(config, "BACKTEST_STRESS_VOL_MULT", 1.8) or 1.8)
    liq_drop = float(getattr(config, "BACKTEST_STRESS_LIQUIDITY_DROP_PCT", 45.0) or 45.0)
    scenarios = [
        ("baseline", df),
        ("crash", _apply_stress_scenario(df, scenario="crash", crash_pct=crash_pct, vol_mult=vol_mult, liq_drop_pct=liq_drop)),
        ("vol_spike", _apply_stress_scenario(df, scenario="vol_spike", crash_pct=crash_pct, vol_mult=vol_mult, liq_drop_pct=liq_drop)),
        ("liquidity_drought", _apply_stress_scenario(df, scenario="liquidity_drought", crash_pct=crash_pct, vol_mult=vol_mult, liq_drop_pct=liq_drop)),
    ]
    reports = []
    for name, scen_df in scenarios:
        if scen_df is None or scen_df.empty:
            continue
        metrics = backtest(config, scen_df)
        metrics["scenario"] = name
        reports.append(metrics)
    worst_dd = max((float(x.get("max_drawdown_pct", 0.0) or 0.0) for x in reports), default=0.0)
    worst_pnl = min((float(x.get("pnl", 0.0) or 0.0) for x in reports), default=0.0)
    return {
        "stress_suite": True,
        "scenarios": reports,
        "summary": {
            "scenario_count": len(reports),
            "worst_max_drawdown_pct": float(worst_dd),
            "worst_pnl": float(worst_pnl),
        },
    }


def monte_carlo_slippage_spread(config: TradingConfig, df: pd.DataFrame, *, runs: int = None, shock_bps: float = None) -> Dict:
    runs = max(1, int(runs or getattr(config, "BACKTEST_MONTE_CARLO_RUNS", 64)))
    shock_bps = float(shock_bps if shock_bps is not None else getattr(config, "BACKTEST_MONTE_CARLO_SLIPPAGE_SHOCK_BPS", 8.0))
    base_slip = float(getattr(config, "BACKTEST_SLIPPAGE_BPS", 6.0))
    base_spread = float(getattr(config, "BACKTEST_SPREAD_BPS", 4.0))

    rng = np.random.default_rng(42)
    rows = []
    for _ in range(runs):
        cfg = _clone_config(config)
        slip = max(0.0, base_slip + abs(float(rng.normal(0.0, shock_bps))))
        spread = max(0.0, base_spread + abs(float(rng.normal(0.0, max(0.1, shock_bps * 0.6)))))
        cfg.BACKTEST_SLIPPAGE_BPS = float(slip)
        cfg.BACKTEST_SPREAD_BPS = float(spread)
        m = backtest(cfg, df)
        rows.append(
            {
                "slippage_bps": float(slip),
                "spread_bps": float(spread),
                "pnl": float(m.get("pnl", 0.0) or 0.0),
                "max_drawdown_pct": float(m.get("max_drawdown_pct", 0.0) or 0.0),
                "sharpe": float(m.get("sharpe", 0.0) or 0.0),
                "sortino": float(m.get("sortino", 0.0) or 0.0),
                "calmar": float(m.get("calmar", 0.0) or 0.0),
            }
        )

    if not rows:
        return {"monte_carlo": True, "runs": 0, "summary": {}, "samples": []}
    pnl = np.array([x["pnl"] for x in rows], dtype=float)
    dd = np.array([x["max_drawdown_pct"] for x in rows], dtype=float)
    sharpe = np.array([x["sharpe"] for x in rows], dtype=float)
    summary = {
        "runs": int(len(rows)),
        "pnl_mean": float(np.mean(pnl)),
        "pnl_p05": float(np.quantile(pnl, 0.05)),
        "pnl_p50": float(np.quantile(pnl, 0.50)),
        "pnl_p95": float(np.quantile(pnl, 0.95)),
        "max_drawdown_mean_pct": float(np.mean(dd)),
        "max_drawdown_p95_pct": float(np.quantile(dd, 0.95)),
        "sharpe_mean": float(np.mean(sharpe)),
        "probability_of_profit": float(np.mean(pnl > 0)),
    }
    return {"monte_carlo": True, "runs": len(rows), "summary": summary, "samples": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--strategy", default="swing", choices=["swing", "scalping"])
    ap.add_argument("--csv", help="Path to OHLCV csv (timestamp,open,high,low,close,volume)")
    ap.add_argument("--yf", action="store_true", help="Use yfinance instead of CSV")
    ap.add_argument("--period", default="180d")
    ap.add_argument("--interval", default="4h")
    ap.add_argument("--report", default="", help="Path to save metrics JSON")
    ap.add_argument("--walk-forward", action="store_true", help="Run rolling walk-forward validation")
    ap.add_argument("--wf-train-bars", type=int, default=0)
    ap.add_argument("--wf-test-bars", type=int, default=0)
    ap.add_argument("--wf-step-bars", type=int, default=0)
    ap.add_argument("--purged-cv", action="store_true", help="Run purged walk-forward cross validation")
    ap.add_argument("--cv-splits", type=int, default=0)
    ap.add_argument("--cv-purge-bars", type=int, default=-1)
    ap.add_argument("--cv-embargo-bars", type=int, default=-1)
    ap.add_argument("--stress-suite", action="store_true", help="Run stress regime suite")
    ap.add_argument("--monte-carlo", action="store_true", help="Run Monte Carlo slippage/spread simulation")
    ap.add_argument("--mc-runs", type=int, default=0)
    ap.add_argument("--mc-shock-bps", type=float, default=-1.0)
    ap.add_argument("--full-lab", action="store_true", help="Run full validation lab (baseline+WF+CV+stress+MC)")
    args = ap.parse_args()

    cfg = TradingConfig()
    cfg.STRATEGY = args.strategy
    cfg.ASSET_SYMBOL = args.symbol
    cfg.MODE = "backtest"
    usd_to_idr_rate = 17000.0

    if args.yf:
        df = load_df_yfinance(args.symbol.replace("/", "-"), args.period, args.interval)
        dataset_source = "yfinance"
    elif args.csv:
        df = load_df_from_csv(args.csv)
        dataset_source = "csv"
    else:
        raise SystemExit("Provide --csv file or --yf to download.")

    dataset_start = str(df.index[0]) if not df.empty else ""
    dataset_end = str(df.index[-1]) if not df.empty else ""
    dataset_rows = int(len(df))

    run_wf = bool(args.walk_forward or args.full_lab)
    run_cv = bool(args.purged_cv or args.full_lab)
    run_stress = bool(args.stress_suite or args.full_lab)
    run_mc = bool(args.monte_carlo or args.full_lab)
    run_lab = bool(run_wf or run_cv or run_stress or run_mc)

    if not run_lab:
        metrics = backtest(cfg, df)
        metrics["requested_summary"] = _build_requested_summary(
            metrics,
            symbol=args.symbol,
            strategy=args.strategy,
            dataset_source=dataset_source,
            dataset_period=args.period,
            dataset_interval=args.interval,
            dataset_rows=dataset_rows,
            dataset_start=dataset_start,
            dataset_end=dataset_end,
            initial_capital_usd=float(getattr(cfg, "INITIAL_CAPITAL", 0.0) or 0.0),
            usd_to_idr_rate=usd_to_idr_rate,
        )
        print(f"Trades Opened: {metrics['trades_opened']}")
        print(f"Trades Closed: {metrics['trades_closed']}")
        print(f"Final Equity: {metrics['final_equity']:.2f}")
        print(f"PnL: {metrics['pnl']:.2f}")
        print(f"WinRate (closed): {metrics['win_rate']:.2%}")
    else:
        report = {
            "validation_lab": True,
            "symbol": args.symbol,
            "strategy": args.strategy,
            "dataset_source": dataset_source,
            "dataset_period": args.period,
            "dataset_interval": args.interval,
            "dataset_rows": dataset_rows,
            "dataset_start": dataset_start,
            "dataset_end": dataset_end,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        base = backtest(cfg, df)
        report["baseline"] = base
        print(f"Baseline PnL: {base.get('pnl', 0.0):.2f} | Sharpe: {base.get('sharpe', 0.0):.2f}")

        if run_wf:
            wf = walk_forward_validate(
                cfg,
                df,
                train_bars=(args.wf_train_bars or None),
                test_bars=(args.wf_test_bars or None),
                step_bars=(args.wf_step_bars or None),
            )
            report["walk_forward"] = wf
            summary = wf.get("summary", {})
            print(f"WF Windows: {wf.get('windows', 0)}")
            if summary:
                print(f"WF Mean PnL: {summary.get('pnl_mean', 0.0):.2f}")
                print(f"WF Mean WinRate: {summary.get('win_rate_mean', 0.0):.2%}")
                print(f"WF Worst MaxDD: {summary.get('max_drawdown_worst_pct', 0.0):.2f}%")

        if run_cv:
            cv = purged_walk_forward_cv(
                cfg,
                df,
                splits=(args.cv_splits or None),
                purge_bars=(None if args.cv_purge_bars < 0 else args.cv_purge_bars),
                embargo_bars=(None if args.cv_embargo_bars < 0 else args.cv_embargo_bars),
            )
            report["purged_cv"] = cv
            cv_summary = cv.get("summary", {})
            print(f"Purged CV Folds: {cv.get('splits', 0)}")
            if cv_summary:
                print(f"CV Mean PnL: {cv_summary.get('pnl_mean', 0.0):.2f}")
                print(f"CV Worst MaxDD: {cv_summary.get('max_drawdown_worst_pct', 0.0):.2f}%")

        if run_stress:
            stress = run_stress_regime_suite(cfg, df)
            report["stress_suite"] = stress
            ssum = stress.get("summary", {})
            print(f"Stress Scenarios: {ssum.get('scenario_count', 0)}")
            print(f"Stress Worst PnL: {ssum.get('worst_pnl', 0.0):.2f}")
            print(f"Stress Worst MaxDD: {ssum.get('worst_max_drawdown_pct', 0.0):.2f}%")

        if run_mc:
            mc = monte_carlo_slippage_spread(
                cfg,
                df,
                runs=(args.mc_runs or None),
                shock_bps=(None if args.mc_shock_bps < 0 else args.mc_shock_bps),
            )
            report["monte_carlo"] = mc
            msum = mc.get("summary", {})
            print(f"MC Runs: {mc.get('runs', 0)} | PnL p05: {msum.get('pnl_p05', 0.0):.2f} | P(profit): {msum.get('probability_of_profit', 0.0):.2%}")

        metrics = report
        metrics["requested_summary"] = _build_requested_summary(
            metrics,
            symbol=args.symbol,
            strategy=args.strategy,
            dataset_source=dataset_source,
            dataset_period=args.period,
            dataset_interval=args.interval,
            dataset_rows=dataset_rows,
            dataset_start=dataset_start,
            dataset_end=dataset_end,
            initial_capital_usd=float(getattr(cfg, "INITIAL_CAPITAL", 0.0) or 0.0),
            usd_to_idr_rate=usd_to_idr_rate,
        )
    if args.report:
        import json, os
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w") as f:
            json.dump(metrics, f, indent=2)


if __name__ == "__main__":
    main()

