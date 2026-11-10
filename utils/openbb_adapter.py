import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import pandas as pd

log = logging.getLogger("elara.openbb")

_OBB = None
_OBB_IMPORT_ATTEMPTED = False
_OBB_IMPORT_ERROR = ""
_OBB_WARNED_MISSING = False


def period_to_days(period_text: str, default_days: int = 120) -> int:
    raw = str(period_text or "").strip().lower()
    if not raw:
        return max(7, int(default_days))
    m = re.match(r"^\s*(\d+)\s*(d|w|m|mo|y)?\s*$", raw)
    if not m:
        return max(7, int(default_days))
    value = max(1, int(m.group(1)))
    unit = str(m.group(2) or "d").lower()
    if unit == "d":
        return value
    if unit == "w":
        return value * 7
    if unit in {"m", "mo"}:
        return value * 30
    if unit == "y":
        return value * 365
    return max(7, int(default_days))


def _load_obb():
    global _OBB, _OBB_IMPORT_ATTEMPTED, _OBB_IMPORT_ERROR, _OBB_WARNED_MISSING
    if _OBB_IMPORT_ATTEMPTED:
        return _OBB
    _OBB_IMPORT_ATTEMPTED = True
    try:
        from openbb import obb  # type: ignore

        _OBB = obb
    except Exception as e:
        _OBB = None
        _OBB_IMPORT_ERROR = str(e)
        if not _OBB_WARNED_MISSING:
            log.warning(f"OpenBB SDK unavailable: {_OBB_IMPORT_ERROR}")
            _OBB_WARNED_MISSING = True
    return _OBB


def is_openbb_available() -> bool:
    return _load_obb() is not None


def get_openbb_import_error() -> str:
    _load_obb()
    return str(_OBB_IMPORT_ERROR or "")


def _resolve_attr_path(root: Any, path: Iterable[str]):
    node = root
    for part in path:
        node = getattr(node, str(part), None)
        if node is None:
            return None
    return node


def _object_to_df(obj: Any) -> Optional[pd.DataFrame]:
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return obj
    if isinstance(obj, list):
        df = pd.DataFrame(obj)
        return df if not df.empty else None
    if isinstance(obj, dict):
        if isinstance(obj.get("results"), list):
            df = pd.DataFrame(obj.get("results"))
            return df if not df.empty else None
        if isinstance(obj.get("data"), list):
            df = pd.DataFrame(obj.get("data"))
            return df if not df.empty else None
        df = pd.DataFrame([obj])
        return df if not df.empty else None
    to_df = getattr(obj, "to_df", None)
    if callable(to_df):
        try:
            df = to_df()
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        except Exception:
            pass
    results = getattr(obj, "results", None)
    if isinstance(results, pd.DataFrame):
        return results if not results.empty else None
    if isinstance(results, list):
        df = pd.DataFrame(results)
        return df if not df.empty else None
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            return _object_to_df(dumped)
        except Exception:
            pass
    return None


def _normalize_ohlcv_frame(frame: pd.DataFrame, include_quote_volume: bool = False) -> Optional[pd.DataFrame]:
    if frame is None or frame.empty:
        return None
    out = frame.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    rename_map = {
        "adj close": "close",
        "adj_close": "close",
        "vol": "volume",
        "base_volume": "volume",
        "quotevolume": "quote_volume",
        "quote_volume_24h": "quote_volume",
    }
    out = out.rename(columns=rename_map)
    if not isinstance(out.index, pd.DatetimeIndex):
        time_col = ""
        for candidate in ("date", "datetime", "timestamp", "time"):
            if candidate in out.columns:
                time_col = candidate
                break
        if time_col:
            out[time_col] = pd.to_datetime(out[time_col], errors="coerce", utc=True)
            out = out.dropna(subset=[time_col])
            out = out.set_index(time_col)
    if isinstance(out.index, pd.DatetimeIndex):
        try:
            if out.index.tz is not None:
                out.index = out.index.tz_convert("UTC").tz_localize(None)
        except Exception:
            pass
    required = ("open", "high", "low", "close")
    if any(col not in out.columns for col in required):
        return None
    if "volume" not in out.columns:
        out["volume"] = 0.0
    cols = ["open", "high", "low", "close", "volume"]
    if include_quote_volume:
        if "quote_volume" not in out.columns:
            out["quote_volume"] = pd.to_numeric(out["close"], errors="coerce") * pd.to_numeric(
                out["volume"], errors="coerce"
            )
        cols.append("quote_volume")
    clean = out.loc[:, [c for c in cols if c in out.columns]].copy()
    for col in clean.columns:
        clean[col] = pd.to_numeric(clean[col], errors="coerce")
    clean = clean.dropna(subset=["open", "high", "low", "close"])
    if "volume" in clean.columns:
        clean["volume"] = clean["volume"].fillna(0.0).clip(lower=0.0)
    if include_quote_volume and "quote_volume" in clean.columns:
        clean["quote_volume"] = clean["quote_volume"].fillna(0.0).clip(lower=0.0)
    clean = clean.sort_index()
    return clean if not clean.empty else None


def _invoke_historical(callable_obj, symbol: str, attempt_kwargs: list[dict]) -> Optional[pd.DataFrame]:
    if not callable(callable_obj):
        return None
    for kwargs in attempt_kwargs:
        payload = {k: v for k, v in kwargs.items() if v is not None and v != ""}
        try:
            data = callable_obj(**payload)
            frame = _object_to_df(data)
            if frame is not None and not frame.empty:
                return frame
        except TypeError:
            continue
        except Exception:
            continue
    for kwargs in attempt_kwargs:
        # Positional fallback: remove symbol-like keys to avoid
        # "multiple values for argument 'symbol'" errors.
        payload = {
            k: v
            for k, v in kwargs.items()
            if v is not None and v != "" and str(k).strip().lower() not in {"symbol", "ticker", "tickers"}
        }
        try:
            data = callable_obj(symbol, **payload)
            frame = _object_to_df(data)
            if frame is not None and not frame.empty:
                return frame
        except TypeError:
            continue
        except Exception:
            continue
    return None


def fetch_stock_ohlcv(
    symbol: str,
    *,
    period_days: int = 540,
    interval: str = "1d",
    provider: str = "yfinance",
) -> Optional[pd.DataFrame]:
    obb = _load_obb()
    if obb is None:
        return None
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    start_date = (datetime.now(timezone.utc) - timedelta(days=max(7, int(period_days)))).date().isoformat()
    paths = [
        ("equity", "price", "historical"),
        ("stocks", "price", "historical"),
        ("equities", "price", "historical"),
        ("equity", "historical"),
    ]
    attempts = [
        {"symbol": sym, "start_date": start_date, "interval": interval, "provider": provider},
        {"symbol": sym, "start_date": start_date, "provider": provider},
        {"symbol": sym, "interval": interval, "provider": provider},
        {"symbol": sym, "provider": provider},
        {"symbol": sym, "start_date": start_date, "interval": interval},
        {"symbol": sym, "start_date": start_date},
        {"symbol": sym},
    ]
    for path in paths:
        fn = _resolve_attr_path(obb, path)
        frame = _invoke_historical(fn, sym, attempts)
        if frame is not None:
            normalized = _normalize_ohlcv_frame(frame, include_quote_volume=False)
            if normalized is not None and not normalized.empty:
                return normalized
    return None


def _crypto_symbol_candidates(symbol: str) -> list[str]:
    sym = str(symbol or "").strip().upper().replace(" ", "")
    if not sym:
        return []
    out = [sym]
    if "/" in sym:
        base, quote = sym.split("/", 1)
        out.extend(
            [
                f"{base}{quote}",
                f"{base}-{quote}",
                f"{base}-{quote}".replace("-USDT", "-USD"),
                f"{base}-{quote}".replace("-USDC", "-USD"),
                f"{base}-USD",
            ]
        )
    elif sym.endswith("USDT") and len(sym) > 4:
        base = sym[:-4]
        out.extend([f"{base}/USDT", f"{base}-USDT", f"{base}-USD"])
    elif sym.endswith("USD") and len(sym) > 3:
        base = sym[:-3]
        out.extend([f"{base}/USD", f"{base}-USD"])
    uniq = []
    for item in out:
        token = str(item or "").strip().upper()
        if token and token not in uniq:
            uniq.append(token)
    return uniq


def fetch_crypto_ohlcv(
    symbol: str,
    *,
    timeframe: str = "1h",
    lookback_days: int = 90,
    provider: str = "yfinance",
) -> Optional[pd.DataFrame]:
    obb = _load_obb()
    if obb is None:
        return None
    start_date = (datetime.now(timezone.utc) - timedelta(days=max(7, int(lookback_days)))).date().isoformat()
    paths = [
        ("crypto", "price", "historical"),
        ("cryptocurrency", "price", "historical"),
        ("crypto", "historical"),
    ]
    for sym in _crypto_symbol_candidates(symbol):
        attempts = [
            {"symbol": sym, "start_date": start_date, "interval": timeframe, "provider": provider},
            {"symbol": sym, "start_date": start_date, "timeframe": timeframe, "provider": provider},
            {"symbol": sym, "interval": timeframe, "provider": provider},
            {"symbol": sym, "timeframe": timeframe, "provider": provider},
            {"symbol": sym, "provider": provider},
            {"symbol": sym, "start_date": start_date, "interval": timeframe},
            {"symbol": sym, "interval": timeframe},
            {"symbol": sym},
        ]
        for path in paths:
            fn = _resolve_attr_path(obb, path)
            frame = _invoke_historical(fn, sym, attempts)
            if frame is not None:
                normalized = _normalize_ohlcv_frame(frame, include_quote_volume=True)
                if normalized is not None and not normalized.empty:
                    return normalized
    return None

