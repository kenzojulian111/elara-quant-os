from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class Scenario:
    name: str
    frame: pd.DataFrame


def generate_scenarios(df: pd.DataFrame) -> List[Scenario]:
    if df.empty:
        return []

    close = pd.to_numeric(df["close"], errors="coerce")
    returns = close.pct_change().fillna(0.0)
    rolling_ret = close.pct_change(periods=50).fillna(0.0)
    rolling_vol = returns.rolling(50).std().fillna(0.0)

    bull = df[rolling_ret >= 0.05]
    bear = df[rolling_ret <= -0.05]
    high_vol = df[rolling_vol >= rolling_vol.quantile(0.75)]
    low_vol = df[rolling_vol <= rolling_vol.quantile(0.25)]

    scenarios = [
        Scenario("bull", bull),
        Scenario("bear", bear),
        Scenario("high_vol", high_vol),
        Scenario("low_vol", low_vol),
        Scenario("full", df),
    ]
    return [s for s in scenarios if not s.frame.empty]

