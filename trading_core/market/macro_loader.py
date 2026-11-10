from __future__ import annotations

import pandas as pd


def load_macro_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(col).strip().lower() for col in df.columns]
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.set_index("timestamp")
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.set_index("date")
    macro_signal = df.get("macro_signal")
    if macro_signal is None:
        raw_score = df.get("macro_score")
        if raw_score is None:
            score = pd.Series(0.0, index=df.index)
        else:
            score = pd.to_numeric(raw_score, errors="coerce").fillna(0.0)
        df["macro_signal"] = score.apply(_score_to_label)
    else:
        df["macro_signal"] = macro_signal.astype(str).fillna("")
    return df[["macro_signal"]].dropna()


def _score_to_label(score: float) -> str:
    try:
        value = float(score)
    except Exception:
        return "neutral"
    if value >= 0.2:
        return "bullish"
    if value <= -0.2:
        return "bearish"
    return "neutral"

