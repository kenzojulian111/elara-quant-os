from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.trusted_pickle import dump_pickle_artifact


CRYPTO_SYMBOLS = ("BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD")
MARKET_PROXIES = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dxy": "DX-Y.NYB",
    "gold": "GC=F",
    "oil": "CL=F",
    "vix": "^VIX",
    "us10y": "^TNX",
}
FRED_SERIES = {
    "fed_funds": "DFF",
    "cpi": "CPIAUCSL",
    "unemployment": "UNRATE",
    "m2": "M2SL",
    "dgs2": "DGS2",
    "dgs10": "DGS10",
}


def _normalize_yf(raw: pd.DataFrame | pd.Series | None, symbol: str) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame()
    # yfinance may return an empty object or a Series for single-column results
    if getattr(raw, "empty", True):
        return pd.DataFrame()
    if isinstance(raw, pd.Series):
        frame = raw.to_frame()
    else:
        frame = raw.copy()
    # Ensure we have a DataFrame (type-checkers may still think this could be a Series)
    frame = pd.DataFrame(frame)
    if isinstance(frame.columns, pd.MultiIndex):
        if symbol in frame.columns.get_level_values(-1):
            frame = frame.xs(symbol, axis=1, level=-1)
        else:
            frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(col).strip().lower().replace(" ", "_") for col in frame.columns]# type: ignore
    if "adj_close" in frame.columns and "close" not in frame.columns:
        frame["close"] = frame["adj_close"]
    keep = [col for col in ("open", "high", "low", "close", "volume") if col in frame.columns]
    frame = frame[keep].copy()
    for col in keep:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame.index = pd.to_datetime(frame.index, errors="coerce", utc=True).normalize()
    return frame.dropna(how="any").sort_index() # type: ignore


def _download_yf_symbol(symbol: str, start: str, end: str) -> pd.DataFrame:
    raw = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False, progress=False, threads=False)
    return _normalize_yf(raw, symbol)


def _download_fred(series_id: str, start: str, end: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"
    raw = pd.read_csv(url)
    if raw.empty or "observation_date" not in raw.columns or series_id not in raw.columns:
        return pd.Series(dtype=float)
    raw["date"] = pd.to_datetime(raw["observation_date"], errors="coerce", utc=True).dt.normalize()
    values = pd.to_numeric(raw[series_id].replace(".", pd.NA), errors="coerce")
    return pd.Series(values.to_numpy(), index=raw["date"], name=series_id.lower()).dropna().sort_index()


def _technical_features(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    close = frame["close"].astype(float)
    volume = frame.get("volume", pd.Series(0.0, index=frame.index)).astype(float)
    out = pd.DataFrame(index=frame.index)
    out[f"{prefix}_ret_1d"] = close.pct_change(1)
    out[f"{prefix}_ret_3d"] = close.pct_change(3)
    out[f"{prefix}_ret_7d"] = close.pct_change(7)
    out[f"{prefix}_vol_7d"] = out[f"{prefix}_ret_1d"].rolling(7).std()
    out[f"{prefix}_vol_21d"] = out[f"{prefix}_ret_1d"].rolling(21).std()
    out[f"{prefix}_mom_14d"] = close / close.shift(14) - 1.0
    out[f"{prefix}_ma_gap_21d"] = close / close.rolling(21).mean() - 1.0
    volume_std = volume.rolling(21).std().replace(0, pd.NA)
    out[f"{prefix}_volume_z_21d"] = (volume - volume.rolling(21).mean()) / volume_std
    return out


def build_dataset(start: str, end: str, symbols: tuple[str, ...] = CRYPTO_SYMBOLS) -> tuple[pd.DataFrame, pd.Series, dict]:
    crypto = {symbol: _download_yf_symbol(symbol, start, end) for symbol in symbols}
    crypto = {symbol: frame for symbol, frame in crypto.items() if not frame.empty}
    if not crypto:
        raise RuntimeError("No crypto OHLCV data downloaded.")

    macro_parts: list[pd.DataFrame] = []
    for name, ticker in MARKET_PROXIES.items():
        frame = _download_yf_symbol(ticker, start, end)
        if not frame.empty:
            macro_parts.append(_technical_features(frame, name).add_prefix("macro_"))
    for name, series_id in FRED_SERIES.items():
        series = _download_fred(series_id, start, end)
        if not series.empty:
            daily = series.resample("D").ffill()
            part = pd.DataFrame(index=daily.index)
            part[f"econ_{name}"] = daily
            part[f"econ_{name}_chg_30d"] = daily.pct_change(30)
            macro_parts.append(part)
    if macro_parts:
        macro = pd.concat(macro_parts, axis=1).sort_index().ffill()
    else:
        macro = pd.DataFrame()

    rows: list[pd.DataFrame] = []
    for symbol, frame in crypto.items():
        own = _technical_features(frame, "asset")
        target = (frame["close"].shift(-3) / frame["close"] - 1.0 > 0).astype(int)
        joined = own.join(macro, how="left").ffill().bfill()
        joined["asset_symbol"] = symbol
        joined["target"] = target
        rows.append(joined)

    features = pd.concat(rows).sort_index()
    features = pd.get_dummies(features, columns=["asset_symbol"], prefix="asset", dtype=float)
    features = features.replace([float("inf"), float("-inf")], pd.NA)
    min_non_null = max(1, int(len(features) * 0.40))
    features = features.dropna(axis=1, thresh=min_non_null)
    features = features.ffill().bfill().fillna(0.0)
    features = features.iloc[:-3]
    target = features.pop("target")
    meta = {
        "crypto_symbols": tuple(crypto.keys()),
        "market_proxy_symbols": MARKET_PROXIES,
        "fred_series": FRED_SERIES,
        "start": start,
        "end": end,
    }
    return features, target.astype(int), meta


def _metrics(y_true: pd.Series, pred, prob) -> dict[str, float]:
    out = {
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
    }
    try:
        out["roc_auc"] = float(roc_auc_score(y_true, prob))
    except Exception:
        out["roc_auc"] = 0.0
    return out


def train(features: pd.DataFrame, target: pd.Series, val_ratio: float) -> tuple[RandomForestClassifier, dict]:
    split = int(len(features) * (1.0 - max(0.05, min(0.4, val_ratio))))
    x_train, x_val = features.iloc[:split], features.iloc[split:]
    y_train, y_val = target.iloc[:split], target.iloc[split:]
    if len(x_train) < 200 or len(x_val) < 50:
        raise RuntimeError("Training dataset too small after feature engineering.")
    model = RandomForestClassifier(
        n_estimators=400,
        max_depth=8,
        min_samples_leaf=8,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=42,
    )
    model.fit(x_train.to_numpy(dtype=float), y_train.to_numpy(dtype=int))
    pred = model.predict(x_val.to_numpy(dtype=float))
    prob = model.predict_proba(x_val.to_numpy(dtype=float))[:, 1]
    report = {
        "train_rows": int(len(x_train)),
        "validation_rows": int(len(x_val)),
        "feature_count": int(features.shape[1]),
        "positive_rate_train": float(y_train.mean()),
        "positive_rate_validation": float(y_val.mean()),
        "validation": _metrics(y_val, pred, prob),
    }
    return model, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Elara crypto/economics model from public 2024+ data.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--symbols", default=",".join(CRYPTO_SYMBOLS))
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--output-dir", default=str(ROOT_DIR / "runtime" / "training" / "crypto_economics"))
    args = parser.parse_args()

    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    features, target, meta = build_dataset(args.start, args.end, symbols)
    model, report = train(features, target, args.val_ratio)
    payload = {
        "model": model,
        "feature_columns": tuple(features.columns),
        "metadata": {
            **meta,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "target": "next_3d_crypto_return_positive",
            "data_sources": (
                "Yahoo Finance via yfinance for crypto and market proxies",
                "FRED CSV endpoint for economics series",
            ),
        },
        "report": report,
    }

    dataset_path = out_dir / "crypto_economics_features.csv"
    target_path = out_dir / "crypto_economics_target.csv"
    model_path = out_dir / "crypto_economics_model.json"
    summary_path = out_dir / "crypto_economics_summary.json"
    features.to_csv(dataset_path, index_label="timestamp")
    target.to_frame("target").to_csv(target_path, index_label="timestamp")
    dump_pickle_artifact(payload, str(model_path), purpose="ml_model")
    summary = {
        "dataset_path": str(dataset_path),
        "target_path": str(target_path),
        "model_path": str(model_path),
        "rows": int(len(features)),
        "target_positive_rate": float(target.mean()),
        **report,
        "metadata": payload["metadata"],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
