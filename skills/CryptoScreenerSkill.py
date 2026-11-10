import asyncio
import logging
import random
import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import Config
from core.base import Skill, NLPUnderstanding
from skills.advanced.trading.rate_limiter import GlobalRateLimiter
from utils.openbb_adapter import (
    fetch_crypto_ohlcv,
    period_to_days,
    is_openbb_available,
    get_openbb_import_error,
)

log = logging.getLogger("elara.skill.crypto_screener")


class CryptoScreenerSkill(Skill):
    @staticmethod
    def _effective_worker_count(requested: int) -> int:
        cpu = max(1, int(os.cpu_count() or 1))
        hard_cap = max(4, cpu * 4)
        return max(1, min(int(requested), hard_cap))

    def __init__(self):
        self.enabled = bool(getattr(Config, "CRYPTO_SCREEN_ENABLED", True))
        self.exchange_id = str(getattr(Config, "CRYPTO_EXCHANGE_ID", "binance") or "binance").strip().lower()
        self.allow_yf_fallback = bool(getattr(Config, "CRYPTO_SCREEN_ALLOW_YFINANCE_FALLBACK", True))
        self.openbb_enabled = bool(getattr(Config, "OPENBB_ENABLED", False))
        self.openbb_only = bool(getattr(Config, "OPENBB_ONLY", False))
        self.openbb_strict = bool(getattr(Config, "OPENBB_STRICT", False))
        if self.openbb_only and not self.openbb_enabled:
            log.warning("OPENBB_ONLY aktif sementara OPENBB_ENABLED=0; memaksa OPENBB_ENABLED=1 untuk konsistensi mode.")
            self.openbb_enabled = True
        self.use_openbb = bool(getattr(Config, "CRYPTO_SCREEN_USE_OPENBB", False) or self.openbb_only)
        self.openbb_provider = str(getattr(Config, "OPENBB_PROVIDER_CRYPTO", "yfinance") or "yfinance").strip().lower()
        self.yf_lookback_period = str(getattr(Config, "CRYPTO_YF_LOOKBACK_PERIOD", "60d") or "60d").strip()
        self.timeframe = str(getattr(Config, "CRYPTO_TIMEFRAME", "1h") or "1h").strip().lower()
        self.lookback = max(240, int(getattr(Config, "CRYPTO_LOOKBACK_CANDLES", 320)))
        self.rs_bars = max(20, int(getattr(Config, "CRYPTO_RS_BARS", 72)))
        self.min_quote_volume = float(getattr(Config, "CRYPTO_MIN_QUOTE_VOLUME", 10_000_000))
        self.atr_pct_min = float(getattr(Config, "CRYPTO_ATR_PCT_MIN", 1.2))
        self.vol_spike_mult = float(getattr(Config, "CRYPTO_VOL_SPIKE_MULT", 1.8))
        self.vcp_std_pct = float(getattr(Config, "CRYPTO_VCP_STD_PCT", 0.035))
        self.limit = max(1, int(getattr(Config, "CRYPTO_SCREEN_LIMIT", 10)))
        self.max_workers = self._effective_worker_count(max(1, int(getattr(Config, "CRYPTO_SCREEN_WORKERS", 6))))
        self.taker_fee_bps = max(0.0, float(getattr(Config, "CRYPTO_TAKER_FEE_BPS", 10)))
        self.screen_timeout_sec = float(getattr(Config, "CRYPTO_SCREEN_TIMEOUT_SEC", 10))
        self.cache_ttl_sec = int(getattr(Config, "CRYPTO_SCREEN_CACHE_TTL_SEC", 90))
        self.api_wait_sec = float(getattr(Config, "CRYPTO_SCREEN_API_WAIT_SEC", 1.2))
        self.benchmark_symbol = str(getattr(Config, "CRYPTO_BENCHMARK_SYMBOL", "BTC/USDT") or "BTC/USDT").upper()
        self.event_blackout_enabled = bool(getattr(Config, "CRYPTO_EVENT_BLACKOUT_ENABLED", True))
        self.event_calendar_path = str(getattr(Config, "CRYPTO_EVENT_CALENDAR_PATH", "") or "").strip()
        self.win_prob_slope = float(getattr(Config, "CRYPTO_WIN_PROB_SLOPE", 5.0) or 5.0)
        self.win_prob_bias = float(getattr(Config, "CRYPTO_WIN_PROB_BIAS", -2.2) or -2.2)
        self.expected_value_min = float(getattr(Config, "CRYPTO_EXPECTED_VALUE_MIN", 0.05) or 0.05)
        self.hit_prob_weight = float(getattr(Config, "CRYPTO_HIT_PROB_WEIGHT", 0.35) or 0.35)
        self.hit_prob_lookback = max(40, int(getattr(Config, "CRYPTO_HIT_PROB_LOOKBACK", 120) or 120))
        self.kelly_cap = float(getattr(Config, "CRYPTO_KELLY_MAX", 0.3) or 0.3)
        self.hurst_window = max(50, int(getattr(Config, "CRYPTO_HURST_WINDOW", 140) or 140))
        self.slope_fast_window = max(12, int(getattr(Config, "CRYPTO_TREND_SLOPE_FAST", 24) or 24))
        self.slope_slow_window = max(24, int(getattr(Config, "CRYPTO_TREND_SLOPE_SLOW", 72) or 72))
        self.cvar_alpha = float(getattr(Config, "CRYPTO_CVAR_ALPHA", 0.05) or 0.05)
        self._event_blackout_windows = self._load_event_blackout_windows()
        self._rate_limiter = GlobalRateLimiter.get(
            name=f"crypto-screener:{self.exchange_id}",
            max_calls=int(getattr(Config, "CRYPTO_API_RATE_LIMIT_MAX_CALLS", 1100)),
            period_sec=float(getattr(Config, "CRYPTO_API_RATE_LIMIT_PERIOD_SEC", 60)),
        )
        self._bg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="CryptoScreenerBG")
        self._state_lock = threading.Lock()
        self._screen_future = None
        self._last_results: List[Dict] = []
        self._last_completed_ts: float = 0.0
        self._last_error: str = ""
        self._exchange_lock = threading.Lock()
        self._exchange_cached = None
        self._exchange_loaded_ts = 0.0
        self._warned_openbb_unavailable = False

    def get_intents(self):
        return ["screen_crypto"]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        if not self.enabled:
            return "Crypto screener dinonaktifkan di konfigurasi."
        snapshot = await asyncio.to_thread(
            self.get_screen_snapshot,
            True,
            self.screen_timeout_sec,
        )
        results = snapshot.get("results") or []
        if results:
            return self._format_results(results)
        if snapshot.get("running"):
            return "Crypto screener sedang berjalan di background. Coba lagi beberapa detik."
        if snapshot.get("error"):
            return f"Crypto screener gagal: {snapshot['error']}"
        return "Belum ada setup crypto yang lolos filter saat ini."

    def _safe_float(self, value, default=np.nan) -> float:
        try:
            if value is None:
                return float(default)
            out = float(value)
            if np.isnan(out):
                return float(default)
            return out
        except Exception:
            return float(default)

    def _clamp(self, val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    @staticmethod
    def _sigmoid(x: float) -> float:
        z = float(np.clip(float(x or 0.0), -50.0, 50.0))
        return float(1.0 / (1.0 + np.exp(-z)))

    @staticmethod
    def _to_naive_timestamp(value) -> Optional[pd.Timestamp]:
        try:
            ts = pd.to_datetime(value, errors="coerce")
            if ts is None or pd.isna(ts):
                return None
            out = pd.Timestamp(ts)
            if out.tzinfo is not None:
                out = out.tz_convert("UTC").tz_localize(None)
            return out
        except Exception:
            return None

    @staticmethod
    def _trend_slope_pct(close: pd.Series, window: int) -> float:
        try:
            series = pd.to_numeric(close, errors="coerce").dropna()
            if len(series) < max(6, int(window or 0)):
                return 0.0
            tail = series.tail(int(window))
            x = np.arange(len(tail))
            y = np.log(tail.astype(float).clip(lower=1e-9))
            slope = float(np.polyfit(x, y, 1)[0])
            return float((np.exp(slope) - 1.0) * 100.0)
        except Exception:
            return 0.0

    @staticmethod
    def _hurst_exponent(close: pd.Series) -> float:
        try:
            series = pd.to_numeric(close, errors="coerce").dropna()
            if len(series) < 50:
                return 0.5
            series = np.log(series.astype(float).clip(lower=1e-9))
            lags = range(2, 20)
            tau = [np.std(series[lag:] - series[:-lag]) for lag in lags]
            if any(t <= 0 for t in tau):
                return 0.5
            poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
            hurst = float(poly[0] * 2.0)
            return float(max(0.0, min(1.0, hurst)))
        except Exception:
            return 0.5

    @staticmethod
    def _ulcer_index(close: pd.Series) -> float:
        try:
            series = pd.to_numeric(close, errors="coerce").dropna()
            if len(series) < 5:
                return 0.0
            peak = series.cummax()
            dd_pct = (series / peak - 1.0) * 100.0
            return float(np.sqrt(np.mean(np.square(dd_pct))))
        except Exception:
            return 0.0

    @staticmethod
    def _cvar(returns: pd.Series, alpha: float) -> float:
        try:
            series = pd.to_numeric(returns, errors="coerce").dropna()
            if series.empty:
                return 0.0
            alpha = float(alpha or 0.05)
            alpha = min(0.5, max(0.01, alpha))
            var = np.quantile(series, alpha)
            tail = series[series <= var]
            if tail.empty:
                return float(var)
            return float(tail.mean())
        except Exception:
            return 0.0

    @staticmethod
    def _calc_kelly_fraction(win_prob: float, rr: float) -> float:
        try:
            win = float(win_prob)
            rr = float(rr)
            if rr <= 0:
                return 0.0
            kelly = (win * (rr + 1.0) - 1.0) / rr
            return float(max(0.0, min(1.0, kelly)))
        except Exception:
            return 0.0

    def _estimate_hit_prob(
        self,
        close: pd.Series,
        entry: float,
        stop: float,
        tp: float,
        lookback: int,
    ) -> float:
        try:
            entry = float(entry)
            stop = float(stop)
            tp = float(tp)
            if entry <= 0 or stop <= 0 or tp <= 0:
                return 0.5
            if stop >= entry or tp <= entry:
                return 0.5
            series = pd.to_numeric(close, errors="coerce").dropna()
            if len(series) < max(12, int(lookback or 0)):
                return 0.5
            window = series.tail(int(lookback))
            rets = pd.Series(np.log(window / window.shift(1))).dropna()
            if rets.empty:
                return 0.5
            mu = float(rets.mean())
            sigma = float(rets.std(ddof=0))
            if sigma <= 1e-12:
                return 0.5
            up = float(np.log(tp / entry))
            down = float(np.log(stop / entry))  # negative
            if up <= 0 or down >= 0:
                return 0.5
            var = sigma ** 2
            if abs(mu) <= 1e-12:
                prob = abs(down) / (up - down)
            else:
                num = 1.0 - np.exp(-2.0 * mu * abs(down) / var)
                den = 1.0 - np.exp(-2.0 * mu * (up - down) / var)
                prob = num / den if abs(den) > 1e-12 else 0.5
            return float(max(0.02, min(0.98, prob)))
        except Exception:
            return 0.5

    def _load_event_blackout_windows(self) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, str]]]:
        out: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, str]]] = {}
        if not self.event_blackout_enabled:
            return out
        if not self.event_calendar_path or not os.path.exists(self.event_calendar_path):
            return out
        try:
            cal = pd.read_csv(self.event_calendar_path)
            if cal is None or cal.empty:
                return out
            cal.columns = [str(c).strip().lower() for c in cal.columns]
            symbol_col = "symbol" if "symbol" in cal.columns else ("ticker" if "ticker" in cal.columns else "")
            date_col = "date" if "date" in cal.columns else ("event_date" if "event_date" in cal.columns else "")
            start_col = "start" if "start" in cal.columns else ("start_date" if "start_date" in cal.columns else "")
            end_col = "end" if "end" in cal.columns else ("end_date" if "end_date" in cal.columns else "")
            if not symbol_col or (not date_col and (not start_col or not end_col)):
                return out

            default_before = max(0, int(getattr(Config, "CRYPTO_EVENT_BLACKOUT_BEFORE_DAYS", 1)))
            default_after = max(0, int(getattr(Config, "CRYPTO_EVENT_BLACKOUT_AFTER_DAYS", 1)))
            for _, row in cal.iterrows():
                raw_sym = str(row.get(symbol_col, "") or "").strip().upper()
                if not raw_sym:
                    continue
                sym = raw_sym.replace(" ", "")
                if "/" not in sym and sym != "*":
                    sym = f"{sym}/USDT"
                label = str(row.get("event", row.get("event_type", "event_blackout")) or "event_blackout").strip() or "event_blackout"

                if date_col:
                    date_ts = self._to_naive_timestamp(row.get(date_col))
                    if date_ts is None:
                        continue
                    before_days = max(0, int(row.get("before_days", default_before) or default_before))
                    after_days = max(0, int(row.get("after_days", default_after) or default_after))
                    start_ts = date_ts - pd.Timedelta(days=before_days)
                    end_ts = date_ts + pd.Timedelta(days=after_days)
                else:
                    start_ts = self._to_naive_timestamp(row.get(start_col))
                    end_ts = self._to_naive_timestamp(row.get(end_col))
                    if start_ts is None or end_ts is None:
                        continue
                if start_ts > end_ts:
                    start_ts, end_ts = end_ts, start_ts
                out.setdefault(sym, []).append((start_ts, end_ts, label))
        except Exception as e:
            log.warning(f"Failed loading crypto event blackout calendar: {e}")
            return {}
        return out

    def _event_blackout_reason(self, symbol: str, ts_value) -> str:
        if not self.event_blackout_enabled:
            return ""
        if not self._event_blackout_windows:
            return ""
        ts = self._to_naive_timestamp(ts_value)
        if ts is None:
            return ""
        sym = str(symbol or "").strip().upper()
        windows = list(self._event_blackout_windows.get(sym, []))
        windows.extend(self._event_blackout_windows.get("*", []))
        for start_ts, end_ts, label in windows:
            if start_ts <= ts <= end_ts:
                return str(label or "event_blackout")
        return ""

    def _load_universe(self) -> List[str]:
        raw = str(getattr(Config, "CRYPTO_UNIVERSE", "") or "")
        out = []
        for item in raw.split(","):
            sym = item.strip().upper().replace(" ", "")
            if not sym:
                continue
            if "/" not in sym:
                sym = f"{sym}/USDT"
            out.append(sym)
        if self.benchmark_symbol and self.benchmark_symbol not in out:
            out.insert(0, self.benchmark_symbol)
        return list(dict.fromkeys(out))

    def _bars_per_day(self) -> int:
        tf = self.timeframe
        if tf.endswith("m"):
            mins = max(1, int(tf[:-1] or "1"))
            return max(1, int(24 * 60 / mins))
        if tf.endswith("h"):
            hrs = max(1, int(tf[:-1] or "1"))
            return max(1, int(24 / hrs))
        if tf.endswith("d"):
            days = max(1, int(tf[:-1] or "1"))
            return max(1, int(1 / days))
        return 24

    def _build_exchange(self):
        try:
            import ccxt
        except Exception as e:
            raise RuntimeError(f"ccxt unavailable: {e}") from e
        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            raise RuntimeError(f"Unsupported exchange: {self.exchange_id}")
        exchange = exchange_cls(
            {
                "enableRateLimit": True,
                "timeout": 15000,
            }
        )
        try:
            self._rate_limiter.acquire_blocking()
            exchange.load_markets()
        except Exception as e:
            raise RuntimeError(f"load_markets failed: {e}") from e
        return exchange

    def _get_exchange(self):
        reuse_sec = max(10, int(getattr(Config, "CRYPTO_SCREEN_EXCHANGE_REUSE_SEC", 180)))
        now = time.time()
        with self._exchange_lock:
            if self._exchange_cached is not None and (now - self._exchange_loaded_ts) < reuse_sec:
                return self._exchange_cached
            exchange = self._build_exchange()
            self._exchange_cached = exchange
            self._exchange_loaded_ts = now
            return exchange

    def _yfinance_interval(self) -> str:
        tf = self.timeframe
        if tf.endswith("m"):
            mins = max(1, int(tf[:-1] or "1"))
            if mins in (1, 2, 5, 15, 30, 60, 90):
                return f"{mins}m"
            if mins <= 120:
                return "60m"
            return "1d"
        if tf.endswith("h"):
            hrs = max(1, int(tf[:-1] or "1"))
            if hrs <= 1:
                return "60m"
            if hrs <= 24:
                return "60m"
            return "1d"
        if tf.endswith("d"):
            return "1d"
        return "60m"

    def _target_pandas_freq(self) -> Optional[str]:
        tf = self.timeframe
        if tf.endswith("m"):
            mins = max(1, int(tf[:-1] or "1"))
            return f"{mins}min"
        if tf.endswith("h"):
            hrs = max(1, int(tf[:-1] or "1"))
            return f"{hrs}H"
        if tf.endswith("d"):
            days = max(1, int(tf[:-1] or "1"))
            return f"{days}D"
        return None

    def _resample_to_timeframe(self, frame: pd.DataFrame) -> pd.DataFrame:
        freq = self._target_pandas_freq()
        if not freq or frame is None or frame.empty:
            return frame
        try:
            ohlcv = frame.resample(freq).agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                    "quote_volume": "sum",
                }
            )
            ohlcv = ohlcv.dropna(subset=["open", "high", "low", "close", "volume"])
            return ohlcv if not ohlcv.empty else frame
        except Exception:
            return frame

    def _to_yf_ticker(self, symbol: str) -> str:
        base = str(symbol or "").upper().strip()
        if "/" in base:
            base = base.split("/")[0]
        return f"{base}-USD"

    def _fetch_ohlcv_yfinance(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
        except Exception as e:
            log.debug(f"yfinance unavailable for crypto fallback: {e}")
            return None
        ticker = self._to_yf_ticker(symbol)
        interval = self._yfinance_interval()
        try:
            df = yf.download(
                ticker,
                period=self.yf_lookback_period,
                interval=interval,
                auto_adjust=False,
                progress=False,
            )
        except Exception as e:
            log.debug(f"yfinance crypto fetch failed for {symbol} ({ticker}): {e}")
            return None
        if df is None or df.empty:
            return None

        out = df.copy()
        if isinstance(out.columns, pd.MultiIndex):
            out.columns = [str(c[0]).strip().lower() for c in out.columns]
        else:
            out.columns = [str(c).strip().lower() for c in out.columns]
        if out.columns.duplicated().any():
            out = out.loc[:, ~out.columns.duplicated(keep="last")]
        if "adj close" in out.columns and "close" not in out.columns:
            out["close"] = out["adj close"]
        needed = {"open", "high", "low", "close", "volume"}
        if not needed.issubset(set(out.columns)):
            return None
        frame = out[["open", "high", "low", "close", "volume"]].copy()
        for col in ("open", "high", "low", "close", "volume"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna()
        if frame.empty:
            return None
        frame["quote_volume"] = frame["close"] * frame["volume"]
        if bool(getattr(Config, "CRYPTO_SCREEN_YF_INTERVAL_SYNC", True)):
            frame = self._resample_to_timeframe(frame)
        return frame

    def _fetch_ohlcv_openbb(self, symbol: str) -> Optional[pd.DataFrame]:
        if not (self.openbb_enabled and self.use_openbb):
            return None
        bars_per_day = max(1, self._bars_per_day())
        lookback_by_candles = max(7, int(np.ceil(float(self.lookback) / float(bars_per_day))) + 5)
        lookback_days = max(lookback_by_candles, period_to_days(self.yf_lookback_period, default_days=90))
        frame = fetch_crypto_ohlcv(
            symbol,
            timeframe=self.timeframe,
            lookback_days=lookback_days,
            provider=self.openbb_provider,
        )
        if frame is not None and not frame.empty:
            return frame
        if not self._warned_openbb_unavailable and not is_openbb_available():
            self._warned_openbb_unavailable = True
            reason = get_openbb_import_error() or "unknown import error"
            log.warning(f"OpenBB crypto source unavailable: {reason}")
        return None

    def _fetch_ohlcv(self, exchange, symbol: str) -> Optional[pd.DataFrame]:
        try:
            self._rate_limiter.acquire_blocking()
            rows = exchange.fetch_ohlcv(symbol, timeframe=self.timeframe, limit=self.lookback)
        except Exception as e:
            log.debug(f"fetch_ohlcv failed for {symbol}: {e}")
            return None
        if not rows:
            return None
        try:
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df = df.set_index("ts").dropna()
            if df.empty:
                return None
            df["quote_volume"] = df["close"] * df["volume"]
            return df
        except Exception as e:
            log.debug(f"Invalid OHLCV frame for {symbol}: {e}")
            return None

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.rolling(period).mean()

    def _compute_quality_metrics(self, df: pd.DataFrame) -> Dict[str, float]:
        out = {
            "sharpe20": 0.0,
            "sortino20": 0.0,
            "trend_eff20": 0.0,
            "max_dd20_pct": 0.0,
            "tstat20": 0.0,
            "skew20": 0.0,
            "kurt20": 0.0,
            "cvar20": 0.0,
            "ulcer20": 0.0,
        }
        if df is None or df.empty or "close" not in df.columns:
            return out
        try:
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(close) < 25:
                return out
            ret = close.pct_change().dropna()
            win = ret.tail(20)
            if len(win) >= 6:
                mean_r = float(win.mean())
                std_r = float(win.std(ddof=0))
                if std_r > 1e-12:
                    out["sharpe20"] = float((mean_r / std_r) * np.sqrt(365))
                    out["tstat20"] = float((mean_r / std_r) * np.sqrt(len(win)))
                downside = win[win < 0]
                downside_std = float(downside.std(ddof=0)) if len(downside) else 0.0
                if downside_std > 1e-12:
                    out["sortino20"] = float((mean_r / downside_std) * np.sqrt(365))
                out["skew20"] = float(np.float64(win.skew())) # type: ignore
                out["kurt20"] = float(np.float64(win.kurt())) # type: ignore
                out["cvar20"] = float(self._cvar(win, self.cvar_alpha))

            recent = close.tail(21)
            if len(recent) >= 2:
                path = float(recent.diff().abs().sum() or 0.0)
                net = abs(float(recent.iloc[-1] - recent.iloc[0]))
                if path > 1e-12:
                    out["trend_eff20"] = float(net / path)
                peak = recent.cummax()
                dd = (recent / peak) - 1.0
                out["max_dd20_pct"] = abs(float(min(0.0, float(dd.min() * 100.0))))
                out["ulcer20"] = float(self._ulcer_index(recent))
        except Exception:
            return out
        return out

    def _compute_beta_alpha(
        self,
        symbol_df: Optional[pd.DataFrame],
        benchmark_df: Optional[pd.DataFrame],
        bars: int = 60,
    ) -> Dict[str, float]:
        out = {"beta": 1.0, "alpha_pct": 0.0}
        if symbol_df is None or benchmark_df is None:
            return out
        try:
            s_series = symbol_df.get("close") if symbol_df is not None else None
            b_series = benchmark_df.get("close") if benchmark_df is not None else None
            # ensure we pass a Series (not None) to to_numeric
            s_close = pd.to_numeric(s_series if s_series is not None else pd.Series(dtype=float), errors="coerce")
            b_close = pd.to_numeric(b_series if b_series is not None else pd.Series(dtype=float), errors="coerce")
            merged = pd.concat([s_close, b_close], axis=1, join="inner").dropna()
            if len(merged) < max(15, bars + 1):
                return out
            merged.columns = ["sym_close", "bench_close"]
            rets = merged.pct_change().dropna().tail(max(15, int(bars)))
            if rets.empty:
                return out
            sym_ret = rets["sym_close"]
            bench_ret = rets["bench_close"]
            var_b = float(bench_ret.var(ddof=0))
            if var_b > 1e-12:
                cov = float(np.cov(sym_ret, bench_ret, ddof=0)[0, 1])
                beta = cov / var_b
                out["beta"] = float(self._clamp(beta, -3.0, 5.0))
            # ensure we convert the product result to float before arithmetic to avoid
            # operator type issues from numpy/pandas scalar types
            cum_sym_prod = (1.0 + sym_ret).prod()
            cum_bench_prod = (1.0 + bench_ret).prod()
            cum_sym = float(cum_sym_prod) - 1.0 # type: ignore
            cum_bench = float(cum_bench_prod) - 1.0 # type: ignore
            out["alpha_pct"] = float((cum_sym - (out["beta"] * cum_bench)) * 100.0)
        except Exception:
            return out
        return out

    def _recommend_position_size(
        self,
        *,
        entry: float,
        stop: float,
        vol_24h_quote: float,
    ) -> Dict[str, float]:
        entry_px = max(0.0, float(entry or 0.0))
        stop_px = max(0.0, float(stop or 0.0))
        if entry_px <= 0:
            return {"size": 0.0, "notional": 0.0, "risk_per_unit": 0.0}
        risk_per_unit = max(entry_px - stop_px, entry_px * 0.001)
        if risk_per_unit <= 0:
            return {"size": 0.0, "notional": 0.0, "risk_per_unit": 0.0}

        risk_budget = max(0.0, float(getattr(Config, "CRYPTO_RISK_BUDGET_USDT", 100.0)))
        max_notional = max(0.0, float(getattr(Config, "CRYPTO_MAX_POSITION_NOTIONAL_USDT", 5000.0)))
        max_liq_share = self._clamp(float(getattr(Config, "CRYPTO_MAX_LIQUIDITY_SHARE", 0.0025)), 0.0001, 0.05)

        size_by_risk = risk_budget / risk_per_unit if risk_budget > 0 else 0.0
        size_by_notional = (max_notional / entry_px) if max_notional > 0 else 0.0
        size_by_liquidity = ((max(0.0, float(vol_24h_quote or 0.0)) * max_liq_share) / entry_px)
        candidates = [x for x in (size_by_risk, size_by_notional, size_by_liquidity) if x > 0]
        size = min(candidates) if candidates else 0.0
        notional = size * entry_px
        return {
            "size": float(max(0.0, size)),
            "notional": float(max(0.0, notional)),
            "risk_per_unit": float(risk_per_unit),
        }

    def _calc_ret_pct(self, close_series: pd.Series, bars: int) -> Optional[float]:
        if close_series is None or len(close_series) <= bars:
            return None
        try:
            start = float(close_series.iloc[-bars])
            end = float(close_series.iloc[-1])
            if start <= 0:
                return None
            return ((end / start) - 1.0) * 100.0
        except Exception:
            return None

    def _compute_win_prob_and_ev(
        self,
        *,
        score: float,
        rr: float,
        sharpe20: float,
        sortino20: float,
        max_dd20_pct: float,
        entry: float,
        stop: float,
        tp1: float,
        hit_prob: Optional[float] = None,
    ) -> Tuple[float, float, float]:
        score_z = self._clamp((float(score) - 50.0) / 25.0, -2.0, 2.0)
        rr_z = self._clamp(float(rr) - 1.0, -1.0, 4.0)
        sharpe_z = self._clamp(float(sharpe20), -3.0, 4.0)
        sortino_z = self._clamp(float(sortino20), -3.0, 5.0)
        dd_penalty = self._clamp(float(max_dd20_pct) / 15.0, 0.0, 3.0)
        blended = (score_z * 0.45) + (rr_z * 0.20) + (sharpe_z * 0.20) + (sortino_z * 0.15)
        z = self.win_prob_bias + (self.win_prob_slope * blended) - (0.40 * dd_penalty)
        win_prob = self._sigmoid(z)
        if hit_prob is not None:
            weight = self._clamp(float(self.hit_prob_weight), 0.0, 0.85)
            hp = self._clamp(float(hit_prob), 0.0, 1.0)
            win_prob = ((1.0 - weight) * win_prob) + (weight * hp)

        risk_unit = max(float(entry) - float(stop), 1e-9)
        reward_unit = max(0.0, float(tp1) - float(entry))
        ev_abs = (win_prob * reward_unit) - ((1.0 - win_prob) * risk_unit)
        ev_r = ev_abs / max(risk_unit, 1e-9)
        return float(win_prob), float(ev_abs), float(ev_r)

    def _analyze_symbol(
        self,
        sym: str,
        preloaded: Optional[pd.DataFrame],
        benchmark_ret_pct: Optional[float],
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> Optional[Dict]:
        df = preloaded
        if df is None or len(df) < 220:
            return None

        frame = df.copy()
        frame["ma20"] = frame["close"].rolling(20).mean()
        frame["ma50"] = frame["close"].rolling(50).mean()
        frame["ma200"] = frame["close"].rolling(200).mean()
        frame["atr"] = self._compute_atr(frame)
        frame["atr_pct"] = (frame["atr"] / frame["close"]) * 100
        frame["ret20"] = (frame["close"] / frame["close"].shift(20) - 1) * 100
        frame["ret60"] = (frame["close"] / frame["close"].shift(60) - 1) * 100
        frame["std10_pct"] = (frame["close"].rolling(10).std() / frame["close"]) * 100
        frame["hh20_prev"] = frame["high"].rolling(20).max().shift(1)

        latest = frame.iloc[-1]
        close = self._safe_float(latest.get("close"), 0.0)
        open_p = self._safe_float(latest.get("open"), close)
        high = self._safe_float(latest.get("high"), close)
        low = self._safe_float(latest.get("low"), close)
        atr = self._safe_float(latest.get("atr"), 0.0)
        atr_pct = self._safe_float(latest.get("atr_pct"), 0.0)
        blackout_reason = self._event_blackout_reason(sym, latest.name)
        if blackout_reason:
            return None
        if close <= 0 or atr_pct <= 0:
            return None
        if atr_pct < self.atr_pct_min * 0.6:
            return None

        vol = self._safe_float(latest.get("volume"), 0.0)
        avg_vol20 = self._safe_float(frame["volume"].rolling(20).mean().iloc[-1], 0.0)
        quote_vol_bar = self._safe_float(latest.get("quote_volume"), 0.0)
        bars_day = max(1, self._bars_per_day())
        vol_24h = self._safe_float(frame["quote_volume"].tail(bars_day).sum(), quote_vol_bar * bars_day)
        if vol_24h < self.min_quote_volume * 0.5:
            return None

        ma20 = self._safe_float(latest.get("ma20"), close)
        ma50 = self._safe_float(latest.get("ma50"), close)
        ma200 = self._safe_float(latest.get("ma200"), close)
        vol_ratio = vol / max(avg_vol20, 1e-9)
        momentum20 = self._safe_float(latest.get("ret20"), 0.0)
        momentum60 = self._safe_float(latest.get("ret60"), 0.0)
        vcp_std_pct = self._safe_float(latest.get("std10_pct"), 999.0)
        breakout = close > self._safe_float(latest.get("hh20_prev"), close * 10)
        trend_stack = close > ma20 > ma50 > ma200
        rs_edge = momentum60 - (benchmark_ret_pct or 0.0)
        quality = self._compute_quality_metrics(frame)
        sharpe20 = self._safe_float(quality.get("sharpe20"), 0.0)
        sortino20 = self._safe_float(quality.get("sortino20"), 0.0)
        trend_eff20 = self._safe_float(quality.get("trend_eff20"), 0.0)
        max_dd20_pct = self._safe_float(quality.get("max_dd20_pct"), 0.0)
        tstat20 = self._safe_float(quality.get("tstat20"), 0.0)
        ulcer20 = self._safe_float(quality.get("ulcer20"), 0.0)
        cvar20 = self._safe_float(quality.get("cvar20"), 0.0)
        skew20 = self._safe_float(quality.get("skew20"), 0.0)
        kurt20 = self._safe_float(quality.get("kurt20"), 0.0)
        slope_fast = self._trend_slope_pct(frame["close"], self.slope_fast_window)
        slope_slow = self._trend_slope_pct(frame["close"], self.slope_slow_window)
        hurst = self._hurst_exponent(frame["close"].tail(self.hurst_window))
        beta_alpha = self._compute_beta_alpha(frame, benchmark_df, bars=min(self.rs_bars, 96))
        beta = self._safe_float(beta_alpha.get("beta"), 1.0)
        alpha_pct = self._safe_float(beta_alpha.get("alpha_pct"), 0.0)

        score = 0.0
        tags: List[str] = []

        if trend_stack:
            score += 22
            tags.append("Trend_Stack")
        elif close > ma50:
            score += 8
        else:
            score -= 9

        if breakout:
            score += 11
            tags.append("Breakout")

        score += self._clamp((vol_ratio - 1.0) * 9.0, -4.0, 16.0)
        if vol_ratio >= self.vol_spike_mult:
            tags.append("Vol_Spike")

        score += self._clamp(momentum20 * 0.8, -6.0, 13.0)
        score += self._clamp(rs_edge * 0.7, -5.0, 16.0)
        if rs_edge > 0:
            tags.append("RS_Pos")

        score += self._clamp(sharpe20 * 2.0, -7.0, 14.0)
        if sharpe20 >= 1.0:
            tags.append("Sharpe_Pos")
        elif sharpe20 <= -0.5:
            tags.append("Sharpe_Neg")

        score += self._clamp(sortino20 * 1.4, -5.0, 10.0)
        if sortino20 >= 1.2:
            tags.append("Sortino_Pos")

        score += self._clamp((trend_eff20 - 0.35) * 15.0, -5.0, 8.0)
        if trend_eff20 >= 0.55:
            tags.append("Trend_Eff")

        score += self._clamp(tstat20 * 1.6, -6.0, 8.0)
        if tstat20 >= 1.0:
            tags.append("TStat_Pos")
        if ulcer20 > 6.0:
            score -= self._clamp((ulcer20 - 6.0) * 0.7, 0.0, 9.0)
            tags.append("Ulcer_Risk")
        if cvar20 < -3.5:
            score -= self._clamp(abs(cvar20) * 0.8, 0.0, 9.0)
            tags.append("CVaR_Risk")
        if skew20 < -0.6:
            score -= 3.0
            tags.append("Skew_Neg")
        if kurt20 > 3.8:
            score -= 2.0
            tags.append("Kurt_Fat")

        score += self._clamp(slope_fast * 6.0, -5.0, 6.0)
        score += self._clamp(slope_slow * 8.0, -6.0, 10.0)
        if hurst >= 0.55:
            score += 4.0
            tags.append("Hurst_Trend")
        elif hurst <= 0.45:
            score -= 4.0
            tags.append("Hurst_Chop")

        dd_limit = float(getattr(Config, "CRYPTO_MAX_DD20_PCT", 12.0))
        if max_dd20_pct > dd_limit:
            score -= self._clamp((max_dd20_pct - dd_limit) * 0.7, 0.0, 12.0)
            tags.append("DD_Risk")

        if alpha_pct > 0:
            score += self._clamp(alpha_pct * 0.15, 0.0, 8.0)
            tags.append("Alpha_Pos")
        else:
            score += self._clamp(alpha_pct * 0.10, -6.0, 0.0)

        if beta >= float(getattr(Config, "CRYPTO_MAX_BETA_SOFT", 1.8)):
            score -= self._clamp((beta - 1.8) * 4.0, 0.0, 10.0)
            tags.append("Beta_High")

        target_atr = max(self.atr_pct_min, 1.6)
        vol_quality = 1.0 - abs(atr_pct - target_atr) / max(target_atr, 1e-9)
        score += self._clamp(vol_quality * 11.0, -5.0, 11.0)

        if vcp_std_pct <= self.vcp_std_pct * 100:
            score += 8
            tags.append("VCP")
        else:
            score -= self._clamp((vcp_std_pct - (self.vcp_std_pct * 100)) * 0.75, 0.0, 7.0)

        if vol_24h >= self.min_quote_volume:
            score += 6
            tags.append("Liq_OK")
        else:
            score -= 5
            tags.append("Liq_Thin")

        candle_range = max(high - low, 1e-9)
        upper_shadow = high - max(close, open_p)
        if close > open_p and (upper_shadow / candle_range) <= 0.4:
            score += 3
            tags.append("Candle_Pos")
        else:
            score -= 2

        score = self._clamp(score, 0.0, 100.0)
        dynamic_min = float(getattr(Config, "CRYPTO_SCORE_MIN_BASE", 45.0))
        if benchmark_ret_pct is not None:
            if benchmark_ret_pct <= -2.5:
                dynamic_min += float(getattr(Config, "CRYPTO_SCORE_MIN_BEAR_EXTRA", 4.0))
            elif benchmark_ret_pct >= 3.0:
                dynamic_min -= float(getattr(Config, "CRYPTO_SCORE_MIN_BULL_RELAX", 1.0))
        dynamic_min = max(35.0, dynamic_min)
        if score < dynamic_min:
            return None

        entry = high * 1.001 if breakout else close * 1.0008
        stop = entry - max(atr * 1.2, close * 0.015)
        if stop >= entry:
            stop = entry * 0.99
        fee_cost = entry * (self.taker_fee_bps / 10000.0) * 2.0
        risk_unit = max(entry - stop, close * 0.003)
        tp1 = entry + (risk_unit * 1.7) + fee_cost
        tp2 = entry + (risk_unit * 2.8) + fee_cost
        rr = (tp1 - entry) / max(risk_unit, 1e-9)
        pos = self._recommend_position_size(entry=entry, stop=stop, vol_24h_quote=vol_24h)
        hit_prob = self._estimate_hit_prob(frame["close"], entry, stop, tp1, self.hit_prob_lookback)
        win_prob, ev_abs, ev_r = self._compute_win_prob_and_ev(
            score=score,
            rr=rr,
            sharpe20=sharpe20,
            sortino20=sortino20,
            max_dd20_pct=max_dd20_pct,
            entry=entry,
            stop=stop,
            tp1=tp1,
            hit_prob=hit_prob,
        )
        kelly_fraction = self._calc_kelly_fraction(win_prob, rr)
        if kelly_fraction <= 0.0:
            return None
        kelly_cap = max(0.05, float(self.kelly_cap))
        kelly_scale = self._clamp(min(kelly_fraction, kelly_cap) / kelly_cap, 0.2, 1.0)
        pos = {
            "size": float(pos.get("size", 0.0)) * kelly_scale,
            "notional": float(pos.get("notional", 0.0)) * kelly_scale,
            "risk_per_unit": float(pos.get("risk_per_unit", 0.0)),
        }
        if ev_r < self.expected_value_min:
            return None

        return {
            "symbol": sym,
            "score": float(score),
            "setup": "Breakout Momentum" if breakout else "Trend Pullback",
            "close": float(close),
            "entry": float(entry),
            "stop": float(stop),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "rr": float(rr),
            "vol_ratio": float(vol_ratio),
            "atr_pct": float(atr_pct),
            "rs_edge": float(rs_edge),
            "alpha_pct": float(alpha_pct),
            "beta": float(beta),
            "sharpe20": float(sharpe20),
            "sortino20": float(sortino20),
            "trend_eff20": float(trend_eff20),
            "max_dd20_pct": float(max_dd20_pct),
            "position_size": float(pos.get("size", 0.0)),
            "position_notional": float(pos.get("notional", 0.0)),
            "win_prob": float(win_prob),
            "hit_prob": float(hit_prob),
            "expected_value": float(ev_abs),
            "expected_value_r": float(ev_r),
            "kelly_fraction": float(kelly_fraction),
            "tstat20": float(tstat20),
            "ulcer20": float(ulcer20),
            "cvar20": float(cvar20),
            "skew20": float(skew20),
            "kurt20": float(kurt20),
            "slope_fast": float(slope_fast),
            "slope_slow": float(slope_slow),
            "hurst": float(hurst),
            "liquidity": float(vol_24h),
            "tags": tags[:6],
        }

    def _collect_background_result(self):
        with self._state_lock:
            fut = self._screen_future
        if not fut or not fut.done():
            return
        try:
            results = fut.result()
            with self._state_lock:
                self._last_results = results
                self._last_completed_ts = time.time()
                self._last_error = ""
                self._screen_future = None
        except Exception as e:
            log.error(f"Background crypto screener failed: {e}")
            with self._state_lock:
                self._last_error = str(e)
                self._screen_future = None

    def _refresh_in_background(self, force_refresh: bool = False) -> bool:
        self._collect_background_result()
        with self._state_lock:
            if self._screen_future and not self._screen_future.done():
                return False
            age = max(0.0, time.time() - float(self._last_completed_ts or 0))
            ttl = max(1.0, float(self.cache_ttl_sec))
            should_refresh = False
            if force_refresh or not self._last_results:
                should_refresh = True
            elif age >= ttl:
                should_refresh = True
            else:
                # X-Fetch early refresh window to avoid burst refresh at exact TTL boundary.
                early_window = max(1.0, min(ttl * 0.40, ttl - 1e-6))
                if age >= (ttl - early_window):
                    progress = (age - (ttl - early_window)) / max(early_window, 1e-9)
                    probability = max(0.05, min(1.0, progress))
                    should_refresh = random.random() < probability
            if should_refresh:
                self._screen_future = self._bg_executor.submit(self._screen_results)
                return True
        return False

    def get_screen_snapshot(self, force_refresh: bool = False, wait_timeout_sec: float = 0.0) -> Dict:
        if not self.enabled:
            return {
                "running": False,
                "results": [],
                "updated_at": self._last_completed_ts,
                "error": "disabled",
            }
        self._refresh_in_background(force_refresh=force_refresh)

        with self._state_lock:
            fut = self._screen_future

        if fut and wait_timeout_sec > 0:
            try:
                fut.result(timeout=wait_timeout_sec)
            except FutureTimeout:
                log.debug("Crypto screener still running after %.2fs wait.", wait_timeout_sec)
            except Exception as e:
                log.error(f"Crypto screener future failed while waiting: {e}")
            finally:
                self._collect_background_result()
        else:
            self._collect_background_result()

        with self._state_lock:
            running = bool(self._screen_future and not self._screen_future.done())
            return {
                "running": running,
                "results": list(self._last_results),
                "updated_at": self._last_completed_ts,
                "error": self._last_error,
            }

    def _screen_results(self) -> List[Dict]:
        symbols = self._load_universe()
        if not symbols:
            return []

        preloaded: Dict[str, pd.DataFrame] = {}
        exchange = None
        exchange_error = ""
        allow_non_openbb_fallback = not (self.openbb_only and self.openbb_strict)
        prefer_openbb = bool(self.openbb_enabled and self.use_openbb)
        if not prefer_openbb:
            try:
                exchange = self._get_exchange()
            except Exception as e:
                exchange_error = str(e)
                if not self.allow_yf_fallback:
                    raise
                log.warning(f"Primary crypto exchange unavailable ({exchange_error}). Switching to yfinance fallback.")
        elif not is_openbb_available() and not allow_non_openbb_fallback:
            reason = get_openbb_import_error() or "OpenBB SDK not available"
            raise RuntimeError(f"OpenBB-only strict mode active but OpenBB is unavailable ({reason})")

        for sym in symbols:
            frame = None
            if prefer_openbb:
                frame = self._fetch_ohlcv_openbb(sym)
            if frame is None and exchange is not None:
                frame = self._fetch_ohlcv(exchange, sym)
            if frame is None and self.allow_yf_fallback and (not prefer_openbb or allow_non_openbb_fallback):
                frame = self._fetch_ohlcv_yfinance(sym)
            if frame is not None and not frame.empty:
                preloaded[sym] = frame

        if not preloaded:
            if prefer_openbb and not allow_non_openbb_fallback:
                raise RuntimeError("OpenBB-only strict mode active and no market data fetched from OpenBB.")
            if exchange_error:
                raise RuntimeError(f"No market data fetched ({exchange_error})")
            source_name = "openbb" if prefer_openbb else self.exchange_id
            raise RuntimeError(f"No market data fetched from {source_name} ({self.timeframe}).")

        benchmark_df = preloaded.get(self.benchmark_symbol)
        benchmark_ret = None
        if benchmark_df is not None:
            benchmark_ret = self._calc_ret_pct(benchmark_df["close"], min(self.rs_bars, len(benchmark_df) - 1))

        results: List[Dict] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            fut_map = {
                executor.submit(self._analyze_symbol, sym, preloaded.get(sym), benchmark_ret, benchmark_df): sym
                for sym in symbols
                if sym in preloaded and sym != self.benchmark_symbol
            }
            for fut in as_completed(fut_map):
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                except Exception as e:
                    log.debug(f"Analyze error {fut_map[fut]}: {e}")

        results = sorted(
            results,
            key=lambda x: (
                float(x.get("expected_value_r", 0.0) or 0.0),
                float(x.get("win_prob", 0.0) or 0.0),
                float(x.get("score", 0.0) or 0.0),
                float(x.get("rr", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return results[: self.limit]

    def _format_results(self, results: List[Dict]) -> str:
        if not results:
            return "Belum ada setup crypto yang lolos filter saat ini."
        source_name = "OPENBB" if (self.openbb_enabled and self.use_openbb) else self.exchange_id.upper()
        lines = [f"Hasil Crypto Screener ({source_name} {self.timeframe}):"]
        for r in results:
            tags = ",".join(r["tags"]) if r.get("tags") else "N/A"
            lines.append(
                f"- {r['symbol']} | Score {r['score']:.1f} | {r['setup']} | Close {r['close']:.5f} | "
                f"Entry {r['entry']:.5f} | SL {r['stop']:.5f} | TP1 {r['tp1']:.5f} | "
                f"RR {r['rr']:.2f} | WP {r.get('win_prob', 0.0):.0%} | HitP {r.get('hit_prob', 0.0):.0%} | "
                f"Kelly {r.get('kelly_fraction', 0.0):.2f} | EV(R) {r.get('expected_value_r', 0.0):+.2f} | "
                f"Vol {r['vol_ratio']:.1f}x | ATR {r['atr_pct']:.2f}% | "
                f"RS {r['rs_edge']:+.1f}% | Alpha {r.get('alpha_pct', 0.0):+.1f}% | Beta {r.get('beta', 1.0):.2f} | "
                f"Sharpe20 {r.get('sharpe20', 0.0):+.2f} | DD20 {r.get('max_dd20_pct', 0.0):.1f}% | "
                f"Size~{r.get('position_size', 0.0):.6f} | 24hVol ${r['liquidity']:.0f} | Tags {tags}"
            )
        lines.append("Catatan: Data crypto bersifat near-real-time exchange feed, bisa delay singkat.")
        return "\n".join(lines)

