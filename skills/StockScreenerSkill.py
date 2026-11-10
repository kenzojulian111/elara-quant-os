import os
import logging
import asyncio
import threading
import time
import io
import re
import random
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None

from core.base import Skill, NLPUnderstanding
from config.settings import Config
from utils.openbb_adapter import (
    fetch_stock_ohlcv,
    period_to_days,
    is_openbb_available,
    get_openbb_import_error,
)

log = logging.getLogger("elara.skill.stock_screener")


class StockScreenerSkill(Skill):
    @staticmethod
    def _resolve_config_path(path_value: str) -> str:
        raw = str(path_value or "").strip()
        if not raw:
            return ""
        if os.path.isabs(raw):
            return raw
        root = str(getattr(Config, "root_path", "") or "").strip()
        if root:
            candidate = os.path.abspath(os.path.join(root, raw))
            if os.path.exists(candidate):
                return candidate
        return os.path.abspath(raw)

    def __init__(self):
        self.provider = Config.STOCK_DATA_PROVIDER
        self.openbb_enabled = bool(getattr(Config, "OPENBB_ENABLED", False))
        self.openbb_only = bool(getattr(Config, "OPENBB_ONLY", False))
        self.openbb_strict = bool(getattr(Config, "OPENBB_STRICT", False))
        self.openbb_provider = str(getattr(Config, "OPENBB_PROVIDER_STOCK", "yfinance") or "yfinance").strip().lower()
        if self.openbb_only and not self.openbb_enabled:
            log.warning("OPENBB_ONLY aktif sementara OPENBB_ENABLED=0; memaksa OPENBB_ENABLED=1 untuk konsistensi mode.")
            self.openbb_enabled = True
        if self.openbb_only:
            self.provider = "openbb"
        if self.provider not in {"yfinance", "csv", "openbb"}:
            self.provider = "yfinance"
        self.yf_lookback_period = str(getattr(Config, "STOCK_YF_LOOKBACK_PERIOD", "18mo") or "18mo").strip()
        self.universe_path = self._resolve_config_path(getattr(Config, "STOCK_UNIVERSE_PATH", ""))
        self.fundamentals_path = self._resolve_config_path(getattr(Config, "STOCK_FUNDAMENTALS_PATH", ""))
        self.csv_dir = self._resolve_config_path(getattr(Config, "STOCK_CSV_DIR", ""))
        self.benchmark_symbol = Config.STOCK_BENCHMARK_SYMBOL
        self.screen_timeout_sec = float(getattr(Config, "STOCK_SCREEN_TIMEOUT_SEC", 12.0))
        self.cache_ttl_sec = int(getattr(Config, "STOCK_SCREEN_CACHE_TTL_SEC", 120))
        self.api_wait_sec = float(getattr(Config, "STOCK_SCREEN_API_WAIT_SEC", 1.5))
        self._bg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="StockScreenerBG")
        self._state_lock = threading.Lock()
        self._screen_future = None
        self._last_results: List[Dict] = []
        self._last_completed_ts: float = 0.0
        self._last_error: str = ""
        self._last_chart_sent_ts: Dict[str, float] = {}
        self._warned_missing_bot_token = False
        self._warned_openbb_unavailable = False
        self.send_chart_to_telegram = bool(getattr(Config, "STOCK_SCREENER_SEND_CHART", True))
        self.chart_points = max(60, int(getattr(Config, "STOCK_SCREENER_CHART_POINTS", 120)))
        self.chart_cooldown_sec = max(10, int(getattr(Config, "STOCK_SCREENER_CHART_COOLDOWN_SEC", 45)))
        self.network_retries = max(1, int(getattr(Config, "STOCK_SCREEN_NETWORK_RETRIES", 3)))
        self.chart_width = max(960, int(getattr(Config, "STOCK_SCREENER_CHART_WIDTH", 1400)))
        self.chart_height = max(640, int(getattr(Config, "STOCK_SCREENER_CHART_HEIGHT", 860)))
        self.event_blackout_enabled = bool(getattr(Config, "STOCK_EVENT_BLACKOUT_ENABLED", True))
        self.event_calendar_path = self._resolve_config_path(getattr(Config, "STOCK_EVENT_CALENDAR_PATH", ""))
        self.win_prob_slope = float(getattr(Config, "STOCK_WIN_PROB_SLOPE", 4.8) or 4.8)
        self.win_prob_bias = float(getattr(Config, "STOCK_WIN_PROB_BIAS", -2.0) or -2.0)
        self.expected_value_min = float(getattr(Config, "STOCK_EXPECTED_VALUE_MIN", 0.05) or 0.05)
        self.hit_prob_weight = float(getattr(Config, "STOCK_HIT_PROB_WEIGHT", 0.35) or 0.35)
        self.hit_prob_lookback = max(30, int(getattr(Config, "STOCK_HIT_PROB_LOOKBACK", 60) or 60))
        self.kelly_cap = float(getattr(Config, "STOCK_KELLY_MAX", 0.25) or 0.25)
        self.hurst_window = max(40, int(getattr(Config, "STOCK_HURST_WINDOW", 90) or 90))
        self.slope_fast_window = max(10, int(getattr(Config, "STOCK_TREND_SLOPE_FAST", 20) or 20))
        self.slope_slow_window = max(30, int(getattr(Config, "STOCK_TREND_SLOPE_SLOW", 60) or 60))
        self.cvar_alpha = float(getattr(Config, "STOCK_CVAR_ALPHA", 0.05) or 0.05)
        self._event_blackout_windows = self._load_event_blackout_windows()
        self._http = requests.Session()
        if Retry is not None:
            retry = Retry(
                total=self.network_retries,
                connect=self.network_retries,
                read=self.network_retries,
                backoff_factor=0.4,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset(["GET", "POST"]),
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._http.mount("https://", adapter)
            self._http.mount("http://", adapter)

    @staticmethod
    def _effective_worker_count(requested: int) -> int:
        cpu = max(1, int(os.cpu_count() or 1))
        hard_cap = max(4, cpu * 4)
        return max(1, min(int(requested), hard_cap))

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
            if len(series) < max(5, int(window or 0)):
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
            if len(series) < 40:
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
            if len(series) < max(10, int(lookback or 0)):
                return 0.5
            window = series.tail(int(lookback))
            rets = np.log(window / window.shift(1)).dropna()
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

            default_before = max(0, int(getattr(Config, "STOCK_EVENT_BLACKOUT_BEFORE_DAYS", 1)))
            default_after = max(0, int(getattr(Config, "STOCK_EVENT_BLACKOUT_AFTER_DAYS", 1)))
            for _, row in cal.iterrows():
                raw_sym = str(row.get(symbol_col, "") or "").strip()
                if not raw_sym:
                    continue
                sym = self._normalize_symbol(raw_sym)
                if not sym:
                    continue
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
            log.warning(f"Failed loading stock event blackout calendar: {e}")
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
        sym = self._normalize_symbol(symbol) or str(symbol or "").strip().upper()
        windows = list(self._event_blackout_windows.get(sym, []))
        windows.extend(self._event_blackout_windows.get("*", []))
        for start_ts, end_ts, label in windows:
            if start_ts <= ts <= end_ts:
                return str(label or "event_blackout")
        return ""

    def _compute_win_prob_and_ev(
        self,
        *,
        score: float,
        rr: float,
        sharpe20: float,
        max_dd20_pct: float,
        entry: float,
        stop: float,
        tp1: float,
        hit_prob: Optional[float] = None,
    ) -> Tuple[float, float, float]:
        score_z = self._clamp((float(score) - 50.0) / 25.0, -2.0, 2.0)
        rr_z = self._clamp(float(rr) - 1.0, -1.0, 4.0)
        sharpe_z = self._clamp(float(sharpe20), -3.0, 4.0)
        dd_penalty = self._clamp(float(max_dd20_pct) / 12.0, 0.0, 3.0)
        z = self.win_prob_bias + (self.win_prob_slope * ((score_z * 0.55) + (rr_z * 0.25) + (sharpe_z * 0.20))) - (0.45 * dd_penalty)
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

    def get_intents(self):
        return ["screen_stocks"]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        requested_symbol = self._extract_requested_symbol(nlu)
        snapshot = await asyncio.to_thread(
            self.get_screen_snapshot,
            True,
            self.screen_timeout_sec
        )
        results = snapshot.get("results") or []
        if results:
            await asyncio.to_thread(self._push_chart_if_needed, nlu, user_id, results, requested_symbol)
            return self._format_results(results)
        if requested_symbol:
            sent = await asyncio.to_thread(self._push_symbol_chart_if_needed, nlu, user_id, requested_symbol)
            if sent:
                return (
                    f"Screener global belum menemukan kandidat kuat, "
                    f"tapi chart {requested_symbol} sudah dikirim."
                )
        if snapshot.get("running"):
            return "Screener sedang berjalan di background. Coba lagi beberapa detik."
        if snapshot.get("error"):
            return f"Screener gagal: {snapshot['error']}"
        return "Tidak ada saham yang cukup kuat saat ini (scoring threshold tidak terpenuhi)."

    def _can_send_chart_to_user(self, nlu: NLPUnderstanding, user_id: str, results: List[Dict]) -> bool:
        if not self.send_chart_to_telegram or not results:
            return False
        token = str(getattr(Config, "BOT_TOKEN", "") or "").strip()
        if not token:
            if not self._warned_missing_bot_token:
                log.warning("BOT_TOKEN kosong. Pengiriman chart screener ke Telegram dinonaktifkan.")
                self._warned_missing_bot_token = True
            return False
        chat_id = str(user_id or "").strip()
        if not chat_id.isdigit():
            return False

        now = time.time()
        last_ts = float(self._last_chart_sent_ts.get(chat_id, 0) or 0)
        if (now - last_ts) < self.chart_cooldown_sec:
            return False

        raw = (nlu.raw_text or "").lower()
        chart_keys = ("chart", "grafik", "candlestick", "gambar", "plot")
        if any(k in raw for k in chart_keys):
            return True
        # Default: kirim chart saat command screener dari Telegram agar output lebih visual.
        return True

    def _render_chart_png_bytes(self, symbol: str) -> Optional[bytes]:
        df = self._fetch_ohlcv(symbol)
        if df is None or df.empty or len(df) < 40:
            return None

        frame = df.tail(self.chart_points).copy()
        if frame.columns.duplicated().any():
            frame = frame.loc[:, ~frame.columns.duplicated(keep="last")]
        for col in ("open", "high", "low", "close", "volume"):
            if col in frame.columns:
                series = frame[col]
                if isinstance(series, pd.DataFrame):
                    series = series.iloc[:, -1]
                frame[col] = pd.to_numeric(series, errors="coerce")
        frame = frame.dropna()
        if frame.empty:
            return None

        try:
            import mplfinance as mpf

            ohlcv = frame.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            buf = io.BytesIO()
            mpf.plot(
                ohlcv,
                type="candle",
                mav=(20, 50),
                volume=True,
                style="charles",
                title=f"{symbol} - Screener Setup",
                savefig={"fname": buf, "dpi": 120, "format": "png", "bbox_inches": "tight"},
            )
            buf.seek(0)
            return buf.read()
        except Exception as e:
            log.debug(f"mplfinance chart render failed for {symbol}: {e}")

        try:
            from PIL import Image, ImageDraw

            ma20 = frame["close"].rolling(20).mean()
            ma50 = frame["close"].rolling(50).mean()
            width, height = int(self.chart_width), int(self.chart_height)
            pad_left, pad_right, pad_top, pad_bottom = 70, 30, 50, 50
            volume_height = 170
            price_bottom = height - pad_bottom - volume_height
            price_top = pad_top
            vol_top = price_bottom + 15
            vol_bottom = height - pad_bottom
            plot_width = max(1, width - pad_left - pad_right)

            img = Image.new("RGB", (width, height), "#0b1020")
            draw = ImageDraw.Draw(img)

            highs = pd.to_numeric(frame["high"], errors="coerce").dropna()
            lows = pd.to_numeric(frame["low"], errors="coerce").dropna()
            vols = pd.to_numeric(frame["volume"], errors="coerce").dropna()
            if highs.empty or lows.empty or vols.empty:
                return None
            max_price = float(highs.max())
            min_price = float(lows.min())
            if max_price <= min_price:
                max_price = min_price + 1.0
            max_vol = float(vols.max()) if float(vols.max()) > 0 else 1.0

            def _y_price(v: float) -> int:
                ratio = (float(v) - min_price) / (max_price - min_price)
                ratio = max(0.0, min(1.0, ratio))
                return int(price_bottom - ratio * (price_bottom - price_top))

            def _y_vol(v: float) -> int:
                ratio = float(v) / max_vol
                ratio = max(0.0, min(1.0, ratio))
                return int(vol_bottom - ratio * (vol_bottom - vol_top))

            n = max(1, len(frame))
            step = max(4, int(plot_width / n))
            body_w = max(2, int(step * 0.58))

            draw.rectangle((pad_left, price_top, width - pad_right, price_bottom), outline="#2d3755", width=1)
            draw.rectangle((pad_left, vol_top, width - pad_right, vol_bottom), outline="#2d3755", width=1)

            ma20_points = []
            ma50_points = []
            for i, (_, row) in enumerate(frame.iterrows()):
                x = pad_left + i * step + (step // 2)
                try:
                    o = float(row["open"])
                    h = float(row["high"])
                    l = float(row["low"])
                    c = float(row["close"])
                    v = float(row["volume"])
                except Exception:
                    continue
                is_up = c >= o
                color = "#2ecc71" if is_up else "#ff5c5c"

                y_high = _y_price(h)
                y_low = _y_price(l)
                y_open = _y_price(o)
                y_close = _y_price(c)
                draw.line((x, y_high, x, y_low), fill=color, width=1)
                top = min(y_open, y_close)
                bot = max(y_open, y_close)
                if bot - top < 1:
                    bot = top + 1
                draw.rectangle((x - body_w // 2, top, x + body_w // 2, bot), fill=color, outline=color)

                yv = _y_vol(v)
                draw.rectangle((x - body_w // 2, yv, x + body_w // 2, vol_bottom), fill="#4b7bec", outline="#4b7bec")

                m20 = ma20.iloc[i]
                if pd.notna(m20):
                    ma20_points.append((x, _y_price(float(m20))))
                m50 = ma50.iloc[i]
                if pd.notna(m50):
                    ma50_points.append((x, _y_price(float(m50))))

            if len(ma20_points) > 1:
                draw.line(ma20_points, fill="#f7dc6f", width=2)
            if len(ma50_points) > 1:
                draw.line(ma50_points, fill="#7fb3d5", width=2)

            draw.text((pad_left, 14), f"{symbol} - Screener Setup", fill="#ecf0f1")
            draw.text((pad_left + 260, 14), "MA20", fill="#f7dc6f")
            draw.text((pad_left + 320, 14), "MA50", fill="#7fb3d5")
            draw.text((pad_left + 380, 14), "Volume", fill="#4b7bec")

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as e:
            log.warning(f"Fallback chart render failed for {symbol}: {e}")
            return None

    def _send_chart_to_telegram(self, chat_id: str, png_bytes: bytes, caption: str):
        token = str(getattr(Config, "BOT_TOKEN", "") or "").strip()
        if not token:
            return
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        last_err = None
        for attempt in range(1, self.network_retries + 1):
            try:
                resp = self._http.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption},
                    files={"photo": ("screener.png", png_bytes, "image/png")},
                    timeout=12,
                )
                resp.raise_for_status()
                return
            except Exception as e:
                last_err = e
                time.sleep(min(2.0, 0.4 * attempt))
        if last_err is not None:
            log.warning(f"Failed sending screener chart to Telegram: {last_err}")

    def _yf_download_with_retry(self, **kwargs):
        import yfinance as yf
        last_err = None
        for attempt in range(1, self.network_retries + 1):
            try:
                data = yf.download(**kwargs)
                if data is not None and not data.empty:
                    return data
            except Exception as e:
                last_err = e
            time.sleep(min(2.0, 0.35 * attempt))
        if last_err is not None:
            raise last_err
        return None

    def _push_chart_if_needed(
        self,
        nlu: NLPUnderstanding,
        user_id: str,
        results: List[Dict],
        requested_symbol: Optional[str] = None
    ):
        if not self._can_send_chart_to_user(nlu, user_id, results):
            return
        top = results[0] if results else {}
        symbol = str((requested_symbol or top.get("symbol") or "")).strip()
        if not symbol:
            return
        png = self._render_chart_png_bytes(symbol)
        if not png:
            return

        selected = None
        for item in results:
            if str(item.get("symbol") or "").upper() == symbol.upper():
                selected = item
                break
        meta = selected or top
        caption = (
            f"Screener Top Pick: {symbol}\n"
            f"Score {float(meta.get('score', 0.0)):.1f} | {meta.get('setup', '-')}\n"
            f"Entry {float(meta.get('entry', 0.0)):.0f} | "
            f"SL {float(meta.get('stop', 0.0)):.0f} | TP1 {float(meta.get('tp1', 0.0)):.0f}"
        )
        chat_id = str(user_id).strip()
        self._send_chart_to_telegram(chat_id, png, caption)
        self._last_chart_sent_ts[chat_id] = time.time()

    def _push_symbol_chart_if_needed(self, nlu: NLPUnderstanding, user_id: str, symbol: str) -> bool:
        if not self._can_send_chart_to_user(nlu, user_id, [{"symbol": symbol}]):
            return False
        png = self._render_chart_png_bytes(symbol)
        if not png:
            return False
        chat_id = str(user_id).strip()
        self._send_chart_to_telegram(
            chat_id,
            png,
            f"Screener Requested Chart: {symbol}\nNo qualified global candidates yet."
        )
        self._last_chart_sent_ts[chat_id] = time.time()
        return True

    def _extract_requested_symbol(self, nlu: NLPUnderstanding) -> Optional[str]:
        for ent in (nlu.entities or []):
            try:
                if str(getattr(ent, "type", "")).lower() == "symbol":
                    return self._normalize_symbol(str(getattr(ent, "value", "")))
            except Exception:
                continue

        raw = str(getattr(nlu, "raw_text", "") or "").upper()
        if not raw:
            return None
        matches = re.findall(r"\b([A-Z]{4}(?:\.JK)?)\b", raw)
        if not matches:
            return None
        return self._normalize_symbol(matches[0])

    def _normalize_symbol(self, symbol: str) -> Optional[str]:
        s = str(symbol or "").strip().upper()
        if not s:
            return None
        if "." not in s and len(s) == 4 and s.isalpha():
            return s + ".JK"
        return s

    def _normalize_yfinance_frame(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        out = df.copy()
        if isinstance(out.columns, pd.MultiIndex):
            pairs = [(str(c[0]).strip().lower(), str(c[1]).strip().lower()) for c in out.columns]
            level0 = [p[0] for p in pairs]
            level1 = [p[1] for p in pairs]
            fields = {"open", "high", "low", "close", "adj close", "volume"}
            if len(set(level0).intersection(fields)) >= 4:
                out.columns = level0
            elif len(set(level1).intersection(fields)) >= 4:
                out.columns = level1
            else:
                out.columns = level0
        else:
            out.columns = [str(c).strip().lower() for c in out.columns]

        if out.columns.duplicated().any():
            out = out.loc[:, ~out.columns.duplicated(keep="last")]
        if "adj close" in out.columns and "close" not in out.columns:
            out["close"] = out["adj close"]
        required = ["open", "high", "low", "close", "volume"]
        if any(col not in out.columns for col in required):
            return None
        clean = out[required].copy()
        for col in required:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")
        clean = clean.dropna()
        return clean if not clean.empty else None

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
            log.error(f"Background screener execution failed: {e}")
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
                # X-Fetch early refresh to spread regeneration load.
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
        self._refresh_in_background(force_refresh=force_refresh)

        with self._state_lock:
            fut = self._screen_future

        if fut and wait_timeout_sec > 0:
            try:
                fut.result(timeout=wait_timeout_sec)
            except FutureTimeout:
                log.debug("Screener future still running after %.2fs wait timeout.", wait_timeout_sec)
            except Exception as e:
                log.error(f"Screener future failed while waiting: {e}")
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

    def _load_universe(self) -> List[str]:
        if not os.path.exists(self.universe_path):
            return []
        symbols = []
        with open(self.universe_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                symbols.append(s)
        # Keep order but deduplicate.
        return list(dict.fromkeys(symbols))

    def _load_fundamentals(self) -> Dict[str, Dict]:
        if not os.path.exists(self.fundamentals_path):
            return {}
        try:
            df = pd.read_csv(self.fundamentals_path)
            if "symbol" not in df.columns:
                return {}
            df["symbol"] = df["symbol"].astype(str)
            return {row["symbol"]: row.to_dict() for _, row in df.iterrows()}
        except Exception as e:
            log.warning(f"Failed to load fundamentals: {e}")
            return {}

    def _fetch_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        normalized_symbol = self._normalize_symbol(symbol) or str(symbol or "").strip().upper()
        allow_non_openbb_fallback = not (self.openbb_only and self.openbb_strict)

        if self.provider == "openbb":
            lookback_days = period_to_days(self.yf_lookback_period, default_days=540)
            frame = fetch_stock_ohlcv(
                normalized_symbol,
                period_days=lookback_days,
                interval="1d",
                provider=self.openbb_provider,
            )
            if frame is not None and not frame.empty:
                return frame
            if not self._warned_openbb_unavailable and not is_openbb_available():
                self._warned_openbb_unavailable = True
                reason = get_openbb_import_error() or "unknown import error"
                log.warning(f"OpenBB stock source unavailable: {reason}")
            if not allow_non_openbb_fallback:
                return None

        if self.provider in {"yfinance", "openbb"}:
            try:
                df = self._yf_download_with_retry(
                    tickers=normalized_symbol,
                    period=self.yf_lookback_period,
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                )
                if df is None or df.empty:
                    return None
                return self._normalize_yfinance_frame(df)
            except Exception as e:
                log.warning(f"yfinance failed for {normalized_symbol}: {e}")
            if self.provider == "openbb" and not allow_non_openbb_fallback:
                return None
        # CSV fallback
        try:
            path = os.path.join(self.csv_dir, f"{normalized_symbol}.csv")
            if not os.path.exists(path):
                return None
            df = pd.read_csv(path)
            df.columns = [c.lower() for c in df.columns]
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            return df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            log.warning(f"CSV load failed for {normalized_symbol}: {e}")
            return None

    def _fetch_bulk_ohlcv(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Bulk download OHLCV to reduce HTTP overhead. Falls back silently to per-symbol on failure.
        """
        out: Dict[str, pd.DataFrame] = {}
        if self.provider != "yfinance" or not symbols:
            return out
        try:
            data = self._yf_download_with_retry(
                tickers=" ".join(symbols),
                period=self.yf_lookback_period,
                interval="1d",
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True
            )
            if data is None or data.empty:
                return out

            if isinstance(data.columns, pd.MultiIndex):
                top = data.columns.levels[0]
                for sym in symbols:
                    if sym not in top:
                        continue
                    sub = data[sym].dropna()
                    sub.columns = [c.lower() for c in sub.columns]
                    if not {"open", "high", "low", "close", "volume"}.issubset(sub.columns):
                        continue
                    out[sym] = sub[["open", "high", "low", "close", "volume"]].dropna()
            else:
                sub = data.rename(columns=str.lower)
                if {"open", "high", "low", "close", "volume"}.issubset(sub.columns):
                    out[symbols[0]] = sub[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            log.warning(f"Bulk yfinance download failed: {e}")
        return out

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1
        ).max(axis=1)
        return tr.rolling(period).mean()

    def _compute_quality_metrics(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Risk-adjusted quality metrics to avoid raw-momentum traps.
        """
        out = {
            "sharpe20": 0.0,
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
            if len(close) < 22:
                return out

            ret = close.pct_change().dropna()
            win = ret.tail(20)
            if len(win) >= 5:
                avg = float(win.mean())
                std = float(win.std(ddof=0))
                if std > 1e-12:
                    out["sharpe20"] = float((avg / std) * np.sqrt(252))
                    out["tstat20"] = float((avg / std) * np.sqrt(len(win)))
                out["skew20"] = float(win.skew()) if len(win) >= 6 else 0.0
                out["kurt20"] = float(win.kurt()) if len(win) >= 6 else 0.0
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

    def _recommend_position_size(self, entry: float, stop: float) -> Tuple[int, float, float]:
        """
        Compute risk-based position size recommendation for equity entries.
        Returns (shares, notional_idr, risk_per_share).
        """
        entry_px = max(0.0, float(entry or 0.0))
        stop_px = max(0.0, float(stop or 0.0))
        if entry_px <= 0:
            return 0, 0.0, 0.0

        tick = float(self._get_tick_size(entry_px))
        risk_per_share = max(entry_px - stop_px, tick)
        if risk_per_share <= 0:
            return 0, 0.0, 0.0

        risk_budget = max(0.0, float(getattr(Config, "STOCK_RISK_BUDGET_IDR", 500000.0)))
        max_notional = max(0.0, float(getattr(Config, "STOCK_MAX_POSITION_NOTIONAL_IDR", 25000000.0)))
        shares = int(max(0.0, np.floor(risk_budget / risk_per_share)))
        if shares <= 0:
            return 0, 0.0, risk_per_share

        if max_notional > 0 and entry_px > 0:
            shares_cap = int(max(0.0, np.floor(max_notional / entry_px)))
            if shares_cap > 0:
                shares = min(shares, shares_cap)

        notional = float(shares * entry_px)
        return int(shares), notional, float(risk_per_share)

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
            s_close = pd.to_numeric(symbol_df.get("close"), errors="coerce")
            b_close = pd.to_numeric(benchmark_df.get("close"), errors="coerce")
            merged = pd.concat([s_close, b_close], axis=1, join="inner").dropna()
            if len(merged) < max(20, bars + 1):
                return out
            merged.columns = ["sym_close", "bench_close"]
            rets = merged.pct_change().dropna().tail(max(20, int(bars)))
            if rets.empty:
                return out
            sym_ret = rets["sym_close"]
            bench_ret = rets["bench_close"]
            var_b = float(bench_ret.var(ddof=0))
            if var_b > 1e-12:
                cov = float(np.cov(sym_ret, bench_ret, ddof=0)[0, 1])
                out["beta"] = float(self._clamp(cov / var_b, -2.0, 4.0))
            cum_sym = float((1.0 + sym_ret).prod() - 1.0)
            cum_bench = float((1.0 + bench_ret).prod() - 1.0)
            out["alpha_pct"] = float((cum_sym - (out["beta"] * cum_bench)) * 100.0)
        except Exception:
            return out
        return out

    def _conviction_weight(self, score: float, sharpe20: float, max_dd20_pct: float) -> float:
        score_norm = self._clamp(float(score or 0.0) / 100.0, 0.0, 1.0)
        sharpe_norm = self._clamp((float(sharpe20 or 0.0) + 1.0) / 3.0, 0.0, 1.0)
        dd_penalty = self._clamp(float(max_dd20_pct or 0.0) / 20.0, 0.0, 1.0)
        raw = (score_norm * 0.55) + (sharpe_norm * 0.35) + ((1.0 - dd_penalty) * 0.10)
        return float(self._clamp(raw, 0.05, 1.0))

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

    def _fundamental_score(self, row: Dict) -> Tuple[float, List[str]]:
        if not row:
            return 8.0, ["NoFundData"]
        tags: List[str] = []
        score = 0.0

        der = self._safe_float(row.get("der"))
        roe = self._safe_float(row.get("roe"))
        ocf = self._safe_float(row.get("ocf"))
        eps_yoy = self._safe_float(row.get("eps_yoy"))

        if not np.isnan(der):
            if der <= Config.STOCK_MAX_DER * 0.6:
                score += 6
                tags.append("DER_Low")
            elif der <= Config.STOCK_MAX_DER:
                score += 3
            else:
                score -= 6
                tags.append("DER_High")

        if not np.isnan(roe):
            if roe >= Config.STOCK_MIN_ROE * 1.5:
                score += 6
                tags.append("ROE_Strong")
            elif roe >= Config.STOCK_MIN_ROE:
                score += 3
            else:
                score -= 4
                tags.append("ROE_Weak")

        if not np.isnan(ocf):
            if ocf > 0:
                score += 4
                tags.append("OCF_Pos")
            else:
                score -= 6
                tags.append("OCF_Neg")

        if not np.isnan(eps_yoy):
            if eps_yoy >= 15:
                score += 6
                tags.append("EPS_Growth")
            elif eps_yoy > 0:
                score += 3
            else:
                score -= 6
                tags.append("EPS_Neg")

        return score, tags[:3]

    def _get_tick_size(self, price: float) -> float:
        # IDX tick approximation
        if price <= 200:
            return 1
        if price <= 500:
            return 2
        if price <= 2000:
            return 5
        if price <= 5000:
            return 10
        return 25

    def _analyze_symbol(
        self,
        sym: str,
        fundamentals: Dict[str, Dict],
        benchmark_ret_pct: Optional[float],
        preloaded: Optional[pd.DataFrame],
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> Optional[Dict]:
        df = preloaded if preloaded is not None else self._fetch_ohlcv(sym)
        if df is None or len(df) < 220:
            return None

        df = df.copy()
        df["ma20"] = df["close"].rolling(20).mean()
        df["ma50"] = df["close"].rolling(50).mean()
        df["ma200"] = df["close"].rolling(200).mean()
        df["atr"] = self._compute_atr(df)
        df["atr_pct"] = (df["atr"] / df["close"]) * 100
        df["ret20"] = (df["close"] / df["close"].shift(20) - 1) * 100
        df["ret60"] = (df["close"] / df["close"].shift(60) - 1) * 100
        df["std10_pct"] = (df["close"].rolling(10).std() / df["close"]) * 100
        df["hh20_prev"] = df["high"].rolling(20).max().shift(1)

        latest = df.iloc[-1]
        close = self._safe_float(latest.get("close"), 0.0)
        open_p = self._safe_float(latest.get("open"), close)
        high = self._safe_float(latest.get("high"), close)
        low = self._safe_float(latest.get("low"), close)
        volume = self._safe_float(latest.get("volume"), 0.0)
        atr = self._safe_float(latest.get("atr"), 0.0)
        atr_pct = self._safe_float(latest.get("atr_pct"), 0.0)
        blackout_reason = self._event_blackout_reason(sym, latest.name)
        if blackout_reason:
            return None

        if close <= 0:
            return None
        if close < Config.STOCK_MIN_PRICE or close > Config.STOCK_MAX_PRICE:
            return None
        if atr_pct <= 0 or atr_pct < Config.STOCK_ATR_PCT_MIN * 0.7:
            return None

        avg_vol20 = self._safe_float(df["volume"].rolling(20).mean().iloc[-1], 0.0)
        vol_mult = Config.STOCK_VOLUME_LOT_MULT if hasattr(Config, "STOCK_VOLUME_LOT_MULT") else 1.0
        liquidity = avg_vol20 * vol_mult * close
        if liquidity < Config.STOCK_MIN_LIQUIDITY_IDR * 0.5:
            return None

        ma20 = self._safe_float(latest.get("ma20"), close)
        ma50 = self._safe_float(latest.get("ma50"), close)
        ma200 = self._safe_float(latest.get("ma200"), close)
        vol_ratio = volume / max(avg_vol20, 1e-9)
        momentum20 = self._safe_float(latest.get("ret20"), 0.0)
        momentum60 = self._safe_float(latest.get("ret60"), 0.0)
        vcp_std_pct = self._safe_float(latest.get("std10_pct"), 999.0)
        breakout = close > self._safe_float(latest.get("hh20_prev"), close * 10)
        trend_stack = close > ma20 > ma50 > ma200
        rs_edge = momentum60 - (benchmark_ret_pct or 0.0)
        quality = self._compute_quality_metrics(df)
        sharpe20 = self._safe_float(quality.get("sharpe20"), 0.0)
        trend_eff20 = self._safe_float(quality.get("trend_eff20"), 0.0)
        max_dd20_pct = self._safe_float(quality.get("max_dd20_pct"), 0.0)
        tstat20 = self._safe_float(quality.get("tstat20"), 0.0)
        ulcer20 = self._safe_float(quality.get("ulcer20"), 0.0)
        cvar20 = self._safe_float(quality.get("cvar20"), 0.0)
        skew20 = self._safe_float(quality.get("skew20"), 0.0)
        kurt20 = self._safe_float(quality.get("kurt20"), 0.0)
        slope_fast = self._trend_slope_pct(df["close"], self.slope_fast_window)
        slope_slow = self._trend_slope_pct(df["close"], self.slope_slow_window)
        hurst = self._hurst_exponent(df["close"].tail(self.hurst_window))
        beta_alpha = self._compute_beta_alpha(df, benchmark_df, bars=max(30, int(getattr(Config, "STOCK_RS_DAYS", 60))))
        beta = self._safe_float(beta_alpha.get("beta"), 1.0)
        alpha_pct = self._safe_float(beta_alpha.get("alpha_pct"), 0.0)
        bench_regime = "neutral"
        if benchmark_ret_pct is not None:
            if benchmark_ret_pct >= float(getattr(Config, "STOCK_SCORE_BENCH_BULL_PCT", 2.5)):
                bench_regime = "bull"
            elif benchmark_ret_pct <= -abs(float(getattr(Config, "STOCK_SCORE_BENCH_BEAR_PCT", 2.5))):
                bench_regime = "bear"

        score = 0.0
        tags: List[str] = []

        if trend_stack:
            score += 24
            tags.append("Trend_Stack")
        elif close > ma50:
            score += 8
        else:
            score -= 10

        if breakout:
            score += 12
            tags.append("Breakout")

        score += self._clamp((vol_ratio - 1.0) * 10.0, -4.0, 18.0)
        if vol_ratio >= Config.STOCK_VOL_SPIKE_MULT:
            tags.append("Vol_Spike")

        score += self._clamp(momentum20 * 0.8, -6.0, 14.0)
        score += self._clamp(rs_edge * 0.9, -5.0, 18.0)
        if rs_edge > 0:
            tags.append("RS_Pos")

        # Risk-adjusted trend quality layer.
        score += self._clamp(sharpe20 * 2.2, -6.0, 14.0)
        if sharpe20 >= 1.0:
            tags.append("Sharpe_Pos")
        elif sharpe20 <= -0.5:
            tags.append("Sharpe_Neg")

        score += self._clamp((trend_eff20 - 0.35) * 18.0, -5.0, 8.0)
        if trend_eff20 >= 0.55:
            tags.append("Trend_Eff")

        score += self._clamp(tstat20 * 1.8, -6.0, 8.0)
        if tstat20 >= 1.0:
            tags.append("TStat_Pos")
        if ulcer20 > 5.0:
            score -= self._clamp((ulcer20 - 5.0) * 0.8, 0.0, 8.0)
            tags.append("Ulcer_Risk")
        if cvar20 < -3.0:
            score -= self._clamp(abs(cvar20) * 0.9, 0.0, 8.0)
            tags.append("CVaR_Risk")
        if skew20 < -0.5:
            score -= 3.0
            tags.append("Skew_Neg")
        if kurt20 > 3.5:
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

        max_dd20_limit = float(getattr(Config, "STOCK_MAX_DD20_PCT", 9.0))
        if max_dd20_pct > max_dd20_limit:
            score -= self._clamp((max_dd20_pct - max_dd20_limit) * 0.7, 0.0, 12.0)
            tags.append("DD_Risk")

        if alpha_pct > 0:
            score += self._clamp(alpha_pct * 0.12, 0.0, 8.0)
            tags.append("Alpha_Pos")
        else:
            score += self._clamp(alpha_pct * 0.08, -6.0, 0.0)

        beta_soft = float(getattr(Config, "STOCK_MAX_BETA_SOFT", 1.8))
        if beta > beta_soft:
            score -= self._clamp((beta - beta_soft) * 4.0, 0.0, 10.0)
            tags.append("Beta_High")

        # Favor tradable volatility; too low/high gets penalized.
        target_atr = max(Config.STOCK_ATR_PCT_MIN, 2.0)
        vol_quality = 1.0 - abs(atr_pct - target_atr) / max(target_atr, 1e-9)
        score += self._clamp(vol_quality * 12.0, -5.0, 12.0)

        if vcp_std_pct <= Config.STOCK_VCP_STD_PCT * 100:
            score += 8
            tags.append("VCP")
        else:
            score -= self._clamp((vcp_std_pct - (Config.STOCK_VCP_STD_PCT * 100)) * 0.8, 0.0, 8.0)

        candle_range = max(high - low, 1e-9)
        upper_shadow = high - max(close, open_p)
        if close > open_p and (upper_shadow / candle_range) <= 0.35:
            score += 4
            tags.append("Candle_Strong")
        else:
            score -= 3

        f_score, f_tags = self._fundamental_score(fundamentals.get(sym, {}))
        score += f_score
        tags.extend(f_tags)
        score = self._clamp(score, 0.0, 100.0)
        if bench_regime == "bull":
            score += float(getattr(Config, "STOCK_SCORE_BULL_BONUS", 2.0))
        elif bench_regime == "bear":
            score -= float(getattr(Config, "STOCK_SCORE_BEAR_PENALTY", 3.0))
        score = self._clamp(score, 0.0, 100.0)

        dynamic_min = float(getattr(Config, "STOCK_SCORE_MIN_BASE", 45.0))
        if bench_regime == "bear":
            dynamic_min += float(getattr(Config, "STOCK_SCORE_MIN_BEAR_EXTRA", 3.0))
        if score < dynamic_min:
            return None

        tick = self._get_tick_size(close)
        setup = "Breakout Momentum" if breakout else "Trend Pullback"
        entry = high + tick if breakout else close + tick
        stop = max(entry - max(atr, tick) * 1.4, low - tick)
        if stop >= entry:
            stop = entry - tick
        risk_unit = max(entry - stop, tick)
        tp1 = entry + (risk_unit * 1.8)
        tp2 = entry + (risk_unit * 3.0)
        rr = (tp1 - entry) / risk_unit if risk_unit else 0.0
        position_size, position_notional, risk_per_share = self._recommend_position_size(entry, stop)
        conviction = self._conviction_weight(score, sharpe20, max_dd20_pct)
        hit_prob = self._estimate_hit_prob(df["close"], entry, stop, tp1, self.hit_prob_lookback)
        win_prob, ev_abs, ev_r = self._compute_win_prob_and_ev(
            score=score,
            rr=rr,
            sharpe20=sharpe20,
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
        conviction *= self._clamp(min(kelly_fraction, kelly_cap) / kelly_cap, 0.2, 1.0)
        target_notional = position_notional * conviction
        if ev_r < self.expected_value_min:
            return None

        return {
            "symbol": sym,
            "score": float(score),
            "setup": setup,
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
            "trend_eff20": float(trend_eff20),
            "max_dd20_pct": float(max_dd20_pct),
            "risk_per_share": float(risk_per_share),
            "position_size": int(position_size),
            "position_notional": float(position_notional),
            "conviction": float(conviction),
            "target_notional": float(target_notional),
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
            "liquidity": float(liquidity),
            "tags": tags[:4]
        }

    def _screen_results(self) -> List[Dict]:
        symbols = self._load_universe()
        if not symbols:
            return []

        fundamentals = self._load_fundamentals()
        benchmark_df = self._fetch_ohlcv(self.benchmark_symbol) if self.benchmark_symbol else None
        benchmark_ret_pct = None
        if benchmark_df is not None and len(benchmark_df) > Config.STOCK_RS_DAYS:
            try:
                benchmark_ret_pct = (
                    (benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[-Config.STOCK_RS_DAYS]) - 1
                ) * 100
            except Exception:
                benchmark_ret_pct = None

        results = []
        max_workers = self._effective_worker_count(max(1, int(Config.STOCK_SCREEN_WORKERS)))
        preloaded_map = self._fetch_bulk_ohlcv(symbols)

        # ThreadPool is more stable on Windows for this mixed IO+compute workload.
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._analyze_symbol,
                    sym,
                    fundamentals,
                    benchmark_ret_pct,
                    preloaded_map.get(sym),
                    benchmark_df,
                ): sym
                for sym in symbols
            }
            for fut in as_completed(future_map):
                try:
                    res = fut.result()
                    if res:
                        results.append(res)
                except Exception as e:
                    log.warning(f"Screen error {future_map[fut]}: {e}")

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
        return results[:Config.STOCK_SCREEN_LIMIT]

    def _run_screen(self) -> str:
        results = self._screen_results()
        return self._format_results(results)

    def _format_results(self, results: List[Dict]) -> str:
        if not results:
            return "Tidak ada saham yang cukup kuat saat ini (scoring threshold tidak terpenuhi)."

        lines = ["Hasil Screener Saham (Ranked, EOD/Delayed feed):"]
        for r in results:
            tags = ",".join(r["tags"]) if r["tags"] else "N/A"
            lines.append(
                f"- {r['symbol']} | Score {r['score']:.1f} | {r['setup']} | Close {r['close']:.0f} | "
                f"Entry {r['entry']:.0f} | SL {r['stop']:.0f} | TP1 {r['tp1']:.0f} | TP2 {r['tp2']:.0f} | "
                f"RR {r['rr']:.2f} | WP {r.get('win_prob', 0.0):.0%} | HitP {r.get('hit_prob', 0.0):.0%} | "
                f"Kelly {r.get('kelly_fraction', 0.0):.2f} | EV(R) {r.get('expected_value_r', 0.0):+.2f} | "
                f"Vol {r['vol_ratio']:.1f}x | ATR {r['atr_pct']:.2f}% | RS {r['rs_edge']:+.1f}% | "
                f"Alpha {r.get('alpha_pct', 0.0):+.1f}% | Beta {r.get('beta', 1.0):.2f} | "
                f"Sharpe20 {r.get('sharpe20', 0.0):+.2f} | DD20 {r.get('max_dd20_pct', 0.0):.1f}% | "
                f"Size~{int(r.get('position_size', 0) or 0)} | Conv {r.get('conviction', 0.0):.2f} | Tags {tags}"
            )
        lines.append("Catatan: Data saham provider default bersifat delayed/end-of-day.")
        return "\n".join(lines)

