from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import logging

import pandas as pd


class PortfolioManager:
    def __init__(self, cfg, logger=None, db_manager=None):
        self.cfg = cfg
        self.log = logger or logging.getLogger("PortfolioManager")
        self.db_manager = db_manager
        self.open_positions = []
        self._trade_counter = 0
        self.current_cash = self._to_decimal(getattr(self.cfg, "INITIAL_CAPITAL", 1000))
        self.current_equity = Decimal(self.current_cash)

    def _is_backtest_mode(self) -> bool:
        return str(getattr(self.cfg, "MODE", "") or "").lower() == "backtest"

    def _use_ohlc_path(self) -> bool:
        return self._is_backtest_mode() and bool(
            getattr(self.cfg, "BACKTEST_USE_OHLC_PATH", False)
        )

    def _closed_trade_pnls(self) -> list[Decimal]:
        getter = getattr(self.db_manager, "get_all_closed_trades_with_pnl", None)
        if getter is None:
            return []
        rows = list(getter() or [])
        out: list[Decimal] = []
        for row in rows:
            try:
                out.append(Decimal(str((row or {}).get("final_pnl", 0) or 0)))
            except Exception:
                continue
        return out

    def _compute_performance_risk_multiplier(self) -> Decimal:
        pnls = self._closed_trade_pnls()
        min_trades = max(1, int(getattr(self.cfg, "RISK_PERF_MIN_TRADES", 5) or 5))
        if len(pnls) < min_trades:
            return Decimal("1.0")

        wins = sum(1 for pnl in pnls if pnl > 0)
        win_rate = Decimal(wins) / Decimal(len(pnls))
        gross_profit = sum((pnl for pnl in pnls if pnl > 0), Decimal("0"))
        gross_loss = sum((-pnl for pnl in pnls if pnl < 0), Decimal("0"))
        net = sum(pnls, Decimal("0"))
        capital = Decimal(str(getattr(self.cfg, "INITIAL_CAPITAL", 1) or 1))
        upside = net / max(Decimal("1"), capital)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("10")

        strong_win = Decimal(str(getattr(self.cfg, "RISK_PERF_WINRATE_STRONG", 0.58) or 0.58))
        weak_win = Decimal(str(getattr(self.cfg, "RISK_PERF_WINRATE_WEAK", 0.45) or 0.45))
        upside_soft = Decimal(str(getattr(self.cfg, "RISK_PERF_UPSIDE_SOFT", 0.04) or 0.04))
        drawdown_soft = Decimal(str(getattr(self.cfg, "RISK_PERF_DRAWDOWN_SOFT", -0.03) or -0.03))
        mult_min = Decimal(str(getattr(self.cfg, "RISK_PERF_MULT_MIN", "0.70") or "0.70"))
        mult_max = Decimal(str(getattr(self.cfg, "RISK_PERF_MULT_MAX", "1.15") or "1.15"))

        multiplier = Decimal("1.0")
        if win_rate >= strong_win and upside >= upside_soft and profit_factor >= Decimal("1.2"):
            multiplier += Decimal("0.10")
        elif win_rate <= weak_win or upside <= drawdown_soft or profit_factor < Decimal("1.0"):
            multiplier -= Decimal("0.15")
        return min(mult_max, max(mult_min, multiplier))

    def _compute_volatility_risk_multiplier(self, candle: pd.Series) -> Decimal:
        atr_pct = Decimal(str(candle.get("atr_pct", 0) or 0))
        target = Decimal(str(getattr(self.cfg, "RISK_ATR_TARGET_PCT", "2.5") or "2.5"))
        mult_min = Decimal(str(getattr(self.cfg, "RISK_ATR_MULT_MIN", "0.70") or "0.70"))
        mult_max = Decimal(str(getattr(self.cfg, "RISK_ATR_MULT_MAX", "1.25") or "1.25"))
        if atr_pct <= 0 or target <= 0:
            return Decimal("1.0")
        ratio = target / atr_pct
        return min(mult_max, max(mult_min, ratio))

    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        try:
            if value is None:
                return Decimal(default)
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def _next_trade_id(self):
        self._trade_counter += 1
        return f"bt_{self._trade_counter}"

    def _resolve_trade_id(self, trade_data: dict):
        creator = getattr(self.db_manager, "create_new_trade", None)
        if callable(creator):
            try:
                trade_id = creator(trade_data)
                if trade_id is not None:
                    return trade_id
            except Exception as e:
                self.log.warning(f"create_new_trade failed: {e}")
        return self._next_trade_id()

    def _risk_percent(self, score: int, threshold: int) -> Decimal:
        default_risk = self._to_decimal(getattr(self.cfg, "DEFAULT_RISK_PERCENTAGE", 1.0), Decimal("1.0"))
        high_risk = self._to_decimal(
            getattr(self.cfg, "HIGH_CONFIDENCE_RISK_PERCENTAGE", default_risk),
            default_risk,
        )
        if score >= (threshold + 1):
            return high_risk
        return default_risk

    def _calc_position_size(self, entry: Decimal, stop: Decimal, risk_pct: Decimal, candle: pd.Series) -> Decimal:
        if entry <= 0 or stop <= 0:
            return Decimal("0")
        per_unit_risk = abs(entry - stop)
        if per_unit_risk <= 0:
            return Decimal("0")
        perf_mult = self._compute_performance_risk_multiplier()
        vol_mult = self._compute_volatility_risk_multiplier(candle)
        equity = self.current_equity if self.current_equity > 0 else self.current_cash
        risk_budget = equity * (risk_pct / Decimal("100")) * perf_mult * vol_mult
        if risk_budget <= 0:
            return Decimal("0")
        return max(Decimal("0"), risk_budget / per_unit_risk)

    def update_and_check_positions(self, candle: pd.Series):
        if candle is None or getattr(candle, "empty", False):
            return False
        high = self._to_decimal(candle.get("high", candle.get("close", 0)))
        low = self._to_decimal(candle.get("low", candle.get("close", 0)))
        close = self._to_decimal(candle.get("close", 0))
        open_px = self._to_decimal(candle.get("open", close))
        ts = candle.name if hasattr(candle, "name") else None
        if ts is None:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

        changed = False
        use_path = self._use_ohlc_path()
        for pos in list(self.open_positions):
            direction = pos.get("direction")
            stop = self._to_decimal(pos.get("stop_loss", 0))
            take_profit = self._to_decimal(pos.get("take_profit", 0))
            exit_price = None
            if use_path:
                bullish = close >= open_px
                if direction == "LONG":
                    if bullish:
                        if take_profit > 0 and high >= take_profit:
                            exit_price = take_profit
                        elif stop > 0 and low <= stop:
                            exit_price = stop
                    else:
                        if stop > 0 and low <= stop:
                            exit_price = stop
                        elif take_profit > 0 and high >= take_profit:
                            exit_price = take_profit
                elif direction == "SHORT":
                    bearish = close <= open_px
                    if bearish:
                        if take_profit > 0 and low <= take_profit:
                            exit_price = take_profit
                        elif stop > 0 and high >= stop:
                            exit_price = stop
                    else:
                        if stop > 0 and high >= stop:
                            exit_price = stop
                        elif take_profit > 0 and low <= take_profit:
                            exit_price = take_profit
            else:
                if direction == "LONG":
                    if stop > 0 and low <= stop:
                        exit_price = stop
                    elif take_profit > 0 and high >= take_profit:
                        exit_price = take_profit
                elif direction == "SHORT":
                    if stop > 0 and high >= stop:
                        exit_price = stop
                    elif take_profit > 0 and low <= take_profit:
                        exit_price = take_profit
            if (
                self._is_backtest_mode()
                and bool(getattr(self.cfg, "BACKTEST_TP_PRIORITY", False))
                and stop > 0
                and take_profit > 0
            ):
                if direction == "LONG" and low <= stop and high >= take_profit:
                    exit_price = take_profit
                elif direction == "SHORT" and high >= stop and low <= take_profit:
                    exit_price = take_profit
            if exit_price is not None:
                self._close_position_by_part(pos, pos.get("current_size", 0), exit_price, ts, "tp_sl")
                changed = True

        unrealized = Decimal("0")
        for pos in self.open_positions:
            entry = self._to_decimal(pos.get("entry_price", 0))
            size = self._to_decimal(pos.get("current_size", 0))
            if pos.get("direction") == "LONG":
                unrealized += (close - entry) * size
            else:
                unrealized += (entry - close) * size
        self.current_equity = self.current_cash + unrealized
        return changed

    def execute_trade(self, signal: str, candle: pd.Series, reason: str, score: int):
        if signal not in {"BUY", "SELL"}:
            return False
        if len(self.open_positions) >= int(getattr(self.cfg, "MAX_OPEN_POSITIONS", 5) or 5):
            return False
        close = self._to_decimal(candle.get("close", 0))
        atr = self._to_decimal(candle.get("atr", 0))
        if close <= 0 or atr <= 0:
            return False
        direction = "LONG" if signal == "BUY" else "SHORT"
        strategy = str(getattr(self.cfg, "STRATEGY", "swing") or "swing")
        sc = (getattr(self.cfg, "STRATEGY_CONFIGS", {}) or {}).get(strategy, {}) or {}
        threshold = int(sc.get("confluence_threshold", 4) or 4)
        risk_cfg = sc.get("risk", {}) or {}
        tsl_mult = self._to_decimal(risk_cfg.get("tsl_atr_multiple", 1.5), Decimal("1.5"))
        tp_mult = self._to_decimal(risk_cfg.get("tp_atr_multiple", 3.0), Decimal("3.0"))

        if self._is_backtest_mode() and bool(getattr(self.cfg, "BACKTEST_WINRATE_MODE", False)):
            min_score_buffer = int(getattr(self.cfg, "BACKTEST_MIN_SCORE_BUFFER", 1) or 1)
            if score < (threshold + min_score_buffer):
                return False
            if bool(getattr(self.cfg, "BACKTEST_LONG_ONLY", False)) and direction == "SHORT":
                return False
            if bool(getattr(self.cfg, "BACKTEST_TREND_ONLY", False)):
                ema_trend = self._to_decimal(candle.get("ema_trend", close))
                if direction == "LONG" and close < ema_trend:
                    return False
                if direction == "SHORT" and close > ema_trend:
                    return False
            tsl_mult = self._to_decimal(getattr(self.cfg, "BACKTEST_SL_ATR_MULT", tsl_mult), tsl_mult)
            tp_mult = self._to_decimal(getattr(self.cfg, "BACKTEST_TP_ATR_MULT", tp_mult), tp_mult)

        if direction == "LONG":
            stop = close - (atr * tsl_mult)
            take_profit = close + (atr * tp_mult)
        else:
            stop = close + (atr * tsl_mult)
            take_profit = close - (atr * tp_mult)

        risk_pct = self._risk_percent(score, threshold)
        size = self._calc_position_size(close, stop, risk_pct, candle)
        if size <= 0:
            return False

        ts = candle.name if hasattr(candle, "name") else None
        if ts is None:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)
        trade_data = {
            "symbol": str(getattr(self.cfg, "ASSET_SYMBOL", "") or ""),
            "strategy": str(strategy),
            "direction": direction,
            "status": "OPEN",
            "entry_time": ts,
            "entry_price": close,
            "current_size": size,
            "initial_size": size,
            "entry_reason": str(reason or ""),
        }
        trade_id = self._resolve_trade_id(trade_data)

        position = {
            "trade_id": trade_id,
            "direction": direction,
            "entry_price": close,
            "current_size": size,
            "stop_loss": stop,
            "take_profit": take_profit,
            "entry_time": ts,
        }
        self.open_positions.append(position)
        return True

    def _close_position_by_part(self, pos: dict, size, price, timestamp, reason: str):
        if not pos:
            return False
        size_dec = self._to_decimal(size)
        if size_dec <= 0:
            return False
        remaining = self._to_decimal(pos.get("current_size", 0))
        size_dec = min(size_dec, remaining)
        if size_dec <= 0:
            return False

        entry = self._to_decimal(pos.get("entry_price", 0))
        price_dec = self._to_decimal(price, entry)
        direction = pos.get("direction")
        if direction == "LONG":
            pnl = (price_dec - entry) * size_dec
        else:
            pnl = (entry - price_dec) * size_dec

        pos["current_size"] = remaining - size_dec
        self.current_cash += pnl
        self.current_equity = self.current_cash

        trade_id = pos.get("trade_id")
        if hasattr(self.db_manager, "log_trade_update"):
            try:
                self.db_manager.log_trade_update(
                    {
                        "trade_id": trade_id,
                        "event_type": "close_part",
                        "pnl": pnl,
                        "timestamp": timestamp,
                        "reason": str(reason or ""),
                    }
                )
            except Exception as e:
                self.log.debug(f"log_trade_update failed: {e}")
        if pos["current_size"] <= 0:
            if hasattr(self.db_manager, "update_trade"):
                try:
                    self.db_manager.update_trade(
                        trade_id,
                        {"status": "CLOSED", "pnl": pnl, "exit_time": timestamp},
                    )
                except Exception as e:
                    self.log.debug(f"update_trade failed: {e}")
            try:
                self.open_positions.remove(pos)
            except ValueError:
                pass
        return True

