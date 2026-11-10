from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from trading_core.data_contracts import MarketState


def row_to_market_state(row: pd.Series, symbol: str) -> MarketState:
    ts = row.name if isinstance(row.name, datetime) else datetime.now(timezone.utc)
    return MarketState(
        ts=ts,
        symbol=str(symbol or "").strip().upper() or "BTC",
        open=float(row.get("open", 0.0)),
        high=float(row.get("high", 0.0)),
        low=float(row.get("low", 0.0)),
        close=float(row.get("close", 0.0)),
        volume=float(row.get("volume", 0.0)),
        order_book_imbalance=_optional_float(row.get("order_book_imbalance")),
        funding_rate=_optional_float(row.get("funding_rate")),
        macro_signal=str(row.get("macro_signal") or "").strip() or None,
        trend=str(row.get("trend") or "").strip() or None,
        volatility=str(row.get("volatility") or "").strip() or None,
        liquidity=str(row.get("liquidity") or "").strip() or None,
    )


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None

