from __future__ import annotations

import pandas as pd


def merge_market_sources(
    ohlcv: pd.DataFrame,
    order_book: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
    macro: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if order_book is not None and not order_book.empty:
        ohlcv = ohlcv.join(order_book, how="left")
    if funding is not None and not funding.empty:
        ohlcv = ohlcv.join(funding, how="left")
    if macro is not None and not macro.empty:
        ohlcv = ohlcv.join(macro, how="left")

    if "order_book_imbalance" not in ohlcv.columns:
        ohlcv["order_book_imbalance"] = pd.NA
    if "funding_rate" not in ohlcv.columns:
        ohlcv["funding_rate"] = pd.NA
    if "macro_signal" not in ohlcv.columns:
        ohlcv["macro_signal"] = "neutral"

    ohlcv["order_book_imbalance"] = ohlcv["order_book_imbalance"].ffill()
    ohlcv["funding_rate"] = ohlcv["funding_rate"].ffill()
    ohlcv["macro_signal"] = ohlcv["macro_signal"].ffill().fillna("neutral")
    return ohlcv

