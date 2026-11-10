import os
import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.Portfoliomanager import PortfolioManager


class _FakeDB:
    def __init__(self, closed_trades):
        self._closed_trades = list(closed_trades or [])

    def load_open_positions(self):
        return []

    def get_all_closed_trades_with_pnl(self):
        return list(self._closed_trades)


def _build_cfg():
    return SimpleNamespace(
        INITIAL_CAPITAL=Decimal("10000"),
        DEFAULT_RISK_PERCENTAGE=Decimal("1.0"),
        HIGH_CONFIDENCE_RISK_PERCENTAGE=Decimal("1.5"),
        TAKER_FEE_PERCENT=Decimal("0.001"),
        PARTIAL_TP_LEVELS=[{"tp_atr_multiple": Decimal("1.5"), "size_percentage": Decimal("0.5")}],
        ASSET_SYMBOL="BTC/USDT",
        STRATEGY="swing",
        STRATEGY_CONFIGS={"swing": {"confluence_threshold": 4, "risk": {"tsl_atr_multiple": Decimal("1.5"), "tp_atr_multiple": Decimal("3.0")}}},
        RISK_PERF_MIN_TRADES=5,
        RISK_PERF_WINRATE_STRONG=0.58,
        RISK_PERF_WINRATE_WEAK=0.45,
        RISK_PERF_UPSIDE_SOFT=0.04,
        RISK_PERF_DRAWDOWN_SOFT=-0.03,
        RISK_PERF_MULT_MIN=Decimal("0.70"),
        RISK_PERF_MULT_MAX=Decimal("1.15"),
        RISK_ATR_TARGET_PCT=Decimal("2.5"),
        RISK_ATR_MULT_MIN=Decimal("0.70"),
        RISK_ATR_MULT_MAX=Decimal("1.25"),
    )


class PortfolioAdaptiveRiskTests(unittest.TestCase):
    def test_performance_multiplier_boosts_on_strong_results(self):
        cfg = _build_cfg()
        trades = [{"final_pnl": 120}] * 8 + [{"final_pnl": -40}] * 2
        pm = PortfolioManager(cfg, logger=SimpleNamespace(info=lambda *a, **k: None), db_manager=_FakeDB(trades))
        mult = pm._compute_performance_risk_multiplier()
        self.assertGreater(mult, Decimal("1.0"))

    def test_performance_multiplier_cuts_on_weak_results(self):
        cfg = _build_cfg()
        trades = [{"final_pnl": -120}] * 8 + [{"final_pnl": 20}] * 2
        pm = PortfolioManager(cfg, logger=SimpleNamespace(info=lambda *a, **k: None), db_manager=_FakeDB(trades))
        mult = pm._compute_performance_risk_multiplier()
        self.assertLess(mult, Decimal("1.0"))

    def test_volatility_multiplier_reduces_when_atr_is_high(self):
        cfg = _build_cfg()
        pm = PortfolioManager(cfg, logger=SimpleNamespace(info=lambda *a, **k: None), db_manager=_FakeDB([]))
        candle = pd.Series({"atr_pct": 6.0})
        mult = pm._compute_volatility_risk_multiplier(candle)
        self.assertLess(mult, Decimal("1.0"))

    def test_volatility_multiplier_increases_when_atr_is_low(self):
        cfg = _build_cfg()
        pm = PortfolioManager(cfg, logger=SimpleNamespace(info=lambda *a, **k: None), db_manager=_FakeDB([]))
        candle = pd.Series({"atr_pct": 1.0})
        mult = pm._compute_volatility_risk_multiplier(candle)
        self.assertGreater(mult, Decimal("1.0"))


if __name__ == "__main__":
    unittest.main()

