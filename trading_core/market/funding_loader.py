from __future__ import annotations

import pandas as pd


def load_funding_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(col).strip().lower() for col in df.columns]
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.set_index("timestamp")
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.set_index("date")
    df["funding_rate"] = pd.to_numeric(df.get("funding_rate", 0.0), errors="coerce")
    return df[["funding_rate"]].dropna()

