from __future__ import annotations

import pandas as pd


class DataManager:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.log = logger
        self.htf_data: pd.DataFrame | None = None
        self.ltf_data: pd.DataFrame | None = None
        self.ml_features: pd.DataFrame | None = None

    def _build_requests(self) -> list[dict]:
        strategy_cfg = dict(
            (getattr(self.cfg, "STRATEGY_CONFIGS", {}) or {}).get(getattr(self.cfg, "STRATEGY", ""), {})
            or {}
        )
        return [
            {
                "name": "htf",
                "symbol": getattr(self.cfg, "ASSET_SYMBOL", ""),
                "timeframe": strategy_cfg.get("htf_timeframe", "1d"),
            },
            {
                "name": "ltf",
                "symbol": getattr(self.cfg, "ASSET_SYMBOL", ""),
                "timeframe": strategy_cfg.get("ltf_timeframe", "1h"),
            },
        ]

    def _build_ml_features(self, ltf: pd.DataFrame) -> pd.DataFrame:
        frame = ltf.copy()
        frame["return_1"] = frame["close"].pct_change().fillna(0.0)
        frame["range_pct"] = (
            (frame["high"] - frame["low"]) / frame["close"].replace(0, pd.NA)
        ).fillna(0.0)
        frame["volume_z"] = (
            (frame["volume"] - frame["volume"].rolling(20, min_periods=5).mean())
            / frame["volume"].rolling(20, min_periods=5).std().replace(0, pd.NA)
        ).fillna(0.0)
        return frame.dropna(how="all")

    async def load_all_data(self, exchange) -> bool:
        try:
            payload = await exchange.fetch_ohlcv_batch(self._build_requests(), concurrency=2)
        except TimeoutError:
            return False
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        htf = payload.get("htf")
        ltf = payload.get("ltf")
        if htf is None or ltf is None or getattr(htf, "empty", True) or getattr(ltf, "empty", True):
            return False
        self.htf_data = htf.copy()
        self.ltf_data = ltf.copy()
        self.ml_features = self._build_ml_features(self.ltf_data)
        return self.ml_features is not None and not self.ml_features.empty

