import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.advanced.trading.config import TradingConfig
from skills.advanced.trading.data_manager import DataManager
from skills.advanced.trading.ml_trainer import train_model
from utils.trusted_pickle import dump_pickle_artifact


def _make_logger(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("elara.colab_train")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def _to_yf_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return "BTC-USD"
    if "/" in raw:
        base, quote = raw.split("/", 1)
        quote = quote.replace("USDT", "USD").replace("USDC", "USD").replace("BUSD", "USD")
        return f"{base}-{quote}"
    return raw.replace("USDT", "-USD")


def _normalize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
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
    if any(c not in out.columns for c in required):
        return pd.DataFrame()

    out = out[required].copy()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna()

    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()]
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    return out.sort_index()


def _load_ohlcv_from_csv(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    ts_col = "timestamp" if "timestamp" in raw.columns else ("date" if "date" in raw.columns else None)
    if ts_col:
        raw[ts_col] = pd.to_datetime(raw[ts_col], errors="coerce", utc=True)
        raw = raw.dropna(subset=[ts_col]).set_index(ts_col)
    return _normalize_df_columns(raw)


def _download_ohlcv(symbol: str, period: str, interval: str) -> pd.DataFrame:
    yf_symbol = _to_yf_symbol(symbol)
    raw = yf.download(yf_symbol, period=period, interval=interval, auto_adjust=False, progress=False)
    return _normalize_df_columns(raw)


def _build_training_dataset(
    ohlcv: pd.DataFrame,
    strategy: str,
    logger: logging.Logger,
):
    cfg = TradingConfig()
    cfg.STRATEGY = str(strategy).strip().lower()
    if cfg.STRATEGY not in cfg.STRATEGY_CONFIGS:
        raise ValueError(f"Unsupported strategy: {strategy}")

    params = cfg.STRATEGY_CONFIGS[cfg.STRATEGY]["params"]
    dm = DataManager(cfg, logger)
    dm.ltf_data = ohlcv.copy()
    dm._calculate_indicators(dm.ltf_data, params, "ltf")
    dm._prepare_ml_data()

    if dm.ml_features is None or dm.ml_target is None:
        return pd.DataFrame(), pd.Series(dtype=int)
    return dm.ml_features.copy(), dm.ml_target.copy()


def _safe_roc_auc(y_true, y_prob) -> float:
    try:
        return float(roc_auc_score(y_true, y_prob))
    except Exception:
        return 0.0


def _evaluate_time_split(features: pd.DataFrame, target: pd.Series, val_ratio: float) -> dict:
    ratio = min(max(float(val_ratio), 0.0), 0.4)
    if ratio <= 0 or len(features) < 80:
        return {}

    cut = int(len(features) * (1.0 - ratio))
    if cut < 40 or cut >= len(features):
        return {}

    x_train = features.iloc[:cut]
    y_train = target.iloc[:cut]
    x_val = features.iloc[cut:]
    y_val = target.iloc[cut:]
    if x_train.empty or x_val.empty:
        return {}

    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=5,
        min_samples_split=10,
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    model.fit(x_train, y_train)
    pred = model.predict(x_val)
    prob = model.predict_proba(x_val)[:, 1]

    return {
        "val_rows": int(len(x_val)),
        "accuracy": float(accuracy_score(y_val, pred)),
        "precision": float(precision_score(y_val, pred, zero_division=0)),
        "recall": float(recall_score(y_val, pred, zero_division=0)),
        "f1": float(f1_score(y_val, pred, zero_division=0)),
        "roc_auc": _safe_roc_auc(y_val, prob),
        "positive_rate_val": float(y_val.mean()) if len(y_val) else 0.0,
    }


def _probe_finbert(logger: logging.Logger) -> dict:
    try:
        import torch
        from transformers import pipeline

        use_cuda = bool(torch.cuda.is_available())
        device = 0 if use_cuda else -1
        clf = pipeline("sentiment-analysis", model="ProsusAI/finbert", device=device)
        sample = "Bitcoin ETF inflows increased while funding rates stayed healthy."
        result = clf(sample)[0]
        out = {
            "device": "cuda:0" if use_cuda else "cpu",
            "label": str(result.get("label", "")),
            "score": float(result.get("score", 0.0)),
        }
        logger.info("FinBERT probe completed on %s", out["device"])
        return out
    except Exception as exc:
        logger.warning("FinBERT probe skipped: %s", exc)
        return {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Colab-friendly ML trainer for Elara trading filter.")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--period", default="730d")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--strategy", default="swing", choices=["swing", "scalping"])
    parser.add_argument("--csv", default="", help="Optional CSV path (timestamp/open/high/low/close/volume)")
    parser.add_argument("--min-samples", type=int, default=100)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument(
        "--output-data",
        default=str(ROOT_DIR / "runtime" / "colab" / "ml_train.json"),
    )
    parser.add_argument(
        "--output-model",
        default=str(ROOT_DIR / "runtime" / "colab" / "ml_model.json"),
    )
    parser.add_argument("--probe-finbert", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = _make_logger(args.verbose)

    if args.csv:
        logger.info("Loading OHLCV from CSV: %s", args.csv)
        ohlcv = _load_ohlcv_from_csv(args.csv)
        source = "csv"
        source_ref = args.csv
    else:
        logger.info(
            "Downloading OHLCV from Yahoo Finance: symbol=%s period=%s interval=%s",
            args.symbol,
            args.period,
            args.interval,
        )
        ohlcv = _download_ohlcv(args.symbol, args.period, args.interval)
        source = "yfinance"
        source_ref = _to_yf_symbol(args.symbol)

    if ohlcv.empty:
        logger.error("OHLCV dataset is empty. Abort.")
        return 1

    logger.info("OHLCV rows: %s", len(ohlcv))
    features, target = _build_training_dataset(ohlcv, args.strategy, logger)
    if features.empty or target.empty:
        logger.error("Failed generating ML dataset from OHLCV.")
        return 1

    dump_pickle_artifact(
        {"features": features, "target": target},
        args.output_data,
        purpose="ml_train_payload",
    )
    logger.info("Saved training payload: %s", args.output_data)

    trained = train_model(
        data_path=args.output_data,
        model_path=args.output_model,
        min_samples=max(20, int(args.min_samples)),
    )
    if not trained:
        logger.error("Model training failed. Check min-samples / dataset quality.")
        return 1
    logger.info("Saved trained model: %s", args.output_model)

    metrics = _evaluate_time_split(features, target, args.val_ratio)
    probe = _probe_finbert(logger) if args.probe_finbert else {}

    summary = {
        "source": source,
        "source_ref": source_ref,
        "strategy": args.strategy,
        "rows_ohlcv": int(len(ohlcv)),
        "rows_features": int(len(features)),
        "feature_count": int(features.shape[1]),
        "target_positive_rate": float(target.mean()) if len(target) else 0.0,
        "output_data": args.output_data,
        "output_model": args.output_model,
        "time_split_metrics": metrics,
        "finbert_probe": probe,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

