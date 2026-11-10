from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import Dict, Iterator, Optional

from trading_core.data_contracts import MarketState


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        text = str(value).strip()
        if text == "":
            return float(default)
        return float(text)
    except Exception:
        return float(default)


def _to_optional_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except Exception:
        return None


def _parse_timestamp(value: object, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return fallback


class MarketReplayEngine:
    def __iter__(self) -> Iterator[MarketState]:
        raise NotImplementedError


class DataFrameReplayEngine(MarketReplayEngine):
    def __init__(self, df, symbol: str):
        self.df = df
        self.symbol = str(symbol or "").strip().upper() or "BTC"

    def __iter__(self) -> Iterator[MarketState]:
        if self.df is None:
            return iter(())
        from trading_core.market.state_builder import row_to_market_state

        for _, row in self.df.iterrows():
            yield row_to_market_state(row, self.symbol)


class CSVReplayEngine(MarketReplayEngine):
    def __init__(self, path: str, symbol: str):
        self.path = str(path or "").strip()
        self.symbol = str(symbol or "").strip().upper() or "BTC"

    def _row_to_state(self, row: Dict[str, str], fallback_ts: datetime) -> MarketState:
        ts = _parse_timestamp(row.get("timestamp") or row.get("date"), fallback_ts)
        return MarketState(
            ts=ts,
            symbol=self.symbol,
            open=_to_float(row.get("open")),
            high=_to_float(row.get("high")),
            low=_to_float(row.get("low")),
            close=_to_float(row.get("close")),
            volume=_to_float(row.get("volume")),
            order_book_imbalance=_to_optional_float(row.get("order_book_imbalance")),
            funding_rate=_to_optional_float(row.get("funding_rate")),
            macro_signal=(row.get("macro_signal") or "").strip() or None,
            trend=(row.get("trend") or "").strip() or None,
            volatility=(row.get("volatility") or "").strip() or None,
            liquidity=(row.get("liquidity") or "").strip() or None,
        )

    def __iter__(self) -> Iterator[MarketState]:
        if not self.path:
            return iter(())
        fallback_ts = datetime.now(timezone.utc)
        with open(self.path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                row = {k.lower(): v for k, v in (raw or {}).items() if k}
                yield self._row_to_state(row, fallback_ts)


def load_replay(source: str, symbol: str) -> MarketReplayEngine:
    return CSVReplayEngine(source, symbol)

