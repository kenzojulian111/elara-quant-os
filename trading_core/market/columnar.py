from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


try:
    import polars as _polars
except Exception:
    _polars = None

try:
    import pandas as _pandas
except Exception:
    _pandas = None


@dataclass(frozen=True)
class ColumnarFrame:
    backend: str
    rows: list[dict[str, Any]]
    native: Any = None

    @property
    def height(self) -> int:
        return len(self.rows)


def preferred_backend() -> str:
    if _polars is not None:
        return "polars"
    if _pandas is not None:
        return "pandas"
    return "python"


def from_rows(rows: Iterable[dict[str, Any]], *, prefer: str = "auto") -> ColumnarFrame:
    normalized = [_normalize_row(row) for row in rows]
    backend = preferred_backend() if prefer == "auto" else str(prefer or "python")
    if backend == "polars" and _polars is not None:
        return ColumnarFrame("polars", normalized, _polars.DataFrame(normalized))
    if backend == "pandas" and _pandas is not None:
        return ColumnarFrame("pandas", normalized, _pandas.DataFrame(normalized))
    return ColumnarFrame("python", normalized, normalized)


def rolling_return_pct(frame: ColumnarFrame, *, close_col: str = "close", window: int = 1) -> list[float]:
    window = max(1, int(window or 1))
    closes = [_to_float(row.get(close_col)) for row in frame.rows]
    out: list[float] = []
    for idx, close in enumerate(closes):
        prev_idx = idx - window
        if prev_idx < 0 or closes[prev_idx] == 0:
            out.append(0.0)
            continue
        out.append(((close - closes[prev_idx]) / closes[prev_idx]) * 100.0)
    return out


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row or {})
    for key in ("open", "high", "low", "close", "volume"):
        if key in data:
            data[key] = _to_float(data.get(key))
    return data


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)
