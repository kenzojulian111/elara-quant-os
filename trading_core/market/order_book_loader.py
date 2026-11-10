from __future__ import annotations

import pandas as pd


def load_order_book_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(col).strip().lower() for col in df.columns]
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.set_index("timestamp")
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.set_index("date")
    if "order_book_imbalance" not in df.columns:
        if "bid_volume" in df.columns:
            bid_vol = pd.to_numeric(df["bid_volume"], errors="coerce").fillna(0.0)
        else:
            bid_vol = pd.Series(0.0, index=df.index)

        if "ask_volume" in df.columns:
            ask_vol = pd.to_numeric(df["ask_volume"], errors="coerce").fillna(0.0)
        else:
            ask_vol = pd.Series(0.0, index=df.index)
        total = (bid_vol + ask_vol).replace(0.0, 1.0)
        df["order_book_imbalance"] = (bid_vol - ask_vol) / total
    return df[["order_book_imbalance"]].dropna()

