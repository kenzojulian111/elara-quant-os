import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.CryptoScreenerSkill import CryptoScreenerSkill


class CryptoScreenerMathTests(unittest.TestCase):
    def setUp(self):
        self.skill = CryptoScreenerSkill()

    def test_quality_metrics_positive_trend(self):
        idx = pd.date_range("2025-01-01", periods=120, freq="h")
        close = np.linspace(100, 140, 120)
        df = pd.DataFrame(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": np.linspace(1000, 2000, 120),
                "quote_volume": close * np.linspace(1000, 2000, 120),
            },
            index=idx,
        )
        m = self.skill._compute_quality_metrics(df)
        self.assertGreater(m["trend_eff20"], 0.5)
        self.assertGreater(m["sharpe20"], 0.0)
        self.assertGreaterEqual(m["max_dd20_pct"], 0.0)

    def test_beta_alpha_and_position_size(self):
        idx = pd.date_range("2025-01-01", periods=160, freq="h")
        bench = np.linspace(100, 120, 160)
        sym = (bench * 1.4) + np.linspace(0, 8, 160)
        sym_df = pd.DataFrame({"close": sym}, index=idx)
        bench_df = pd.DataFrame({"close": bench}, index=idx)
        ba = self.skill._compute_beta_alpha(sym_df, bench_df, bars=80)
        self.assertGreater(ba["beta"], 0.5)

        with patch("skills.CryptoScreenerSkill.Config.CRYPTO_RISK_BUDGET_USDT", 100.0, create=True), \
             patch("skills.CryptoScreenerSkill.Config.CRYPTO_MAX_POSITION_NOTIONAL_USDT", 600.0, create=True), \
             patch("skills.CryptoScreenerSkill.Config.CRYPTO_MAX_LIQUIDITY_SHARE", 0.002, create=True):
            pos = self.skill._recommend_position_size(entry=100.0, stop=95.0, vol_24h_quote=50000.0)
        self.assertGreater(pos["size"], 0.0)
        self.assertLessEqual(pos["notional"], 600.0 + 1e-6)

    def test_probabilistic_ev_improves_with_better_signal(self):
        weak = self.skill._compute_win_prob_and_ev(
            score=45.0,
            rr=1.1,
            sharpe20=-0.4,
            sortino20=-0.2,
            max_dd20_pct=16.0,
            entry=100.0,
            stop=95.0,
            tp1=106.0,
        )
        strong = self.skill._compute_win_prob_and_ev(
            score=82.0,
            rr=1.9,
            sharpe20=1.5,
            sortino20=2.0,
            max_dd20_pct=5.0,
            entry=100.0,
            stop=95.0,
            tp1=112.0,
        )
        self.assertGreater(strong[0], weak[0])  # win_prob
        self.assertGreater(strong[2], weak[2])  # ev_r

    def test_hit_prob_and_kelly_bounds(self):
        idx = pd.date_range("2025-01-01", periods=200, freq="h")
        close = np.linspace(100, 160, 200)
        df = pd.DataFrame({"close": close}, index=idx)
        prob = self.skill._estimate_hit_prob(df["close"], entry=130.0, stop=120.0, tp=150.0, lookback=120)
        self.assertGreaterEqual(prob, 0.02)
        self.assertLessEqual(prob, 0.98)
        kelly = self.skill._calc_kelly_fraction(0.62, 2.2)
        self.assertGreater(kelly, 0.0)

    def test_event_blackout_reason_matches_window(self):
        self.skill.event_blackout_enabled = True
        self.skill._event_blackout_windows = {
            "BTC/USDT": [(pd.Timestamp("2025-03-01"), pd.Timestamp("2025-03-03"), "macro_event")]
        }
        reason = self.skill._event_blackout_reason("BTC/USDT", pd.Timestamp("2025-03-02"))
        self.assertEqual(reason, "macro_event")


if __name__ == "__main__":
    unittest.main()

