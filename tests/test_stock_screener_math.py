import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.StockScreenerSkill import StockScreenerSkill


class StockScreenerMathTests(unittest.TestCase):
    def setUp(self):
        self.skill = StockScreenerSkill()

    def test_quality_metrics_detect_positive_trend(self):
        idx = pd.date_range("2025-01-01", periods=40, freq="D")
        close = np.linspace(100, 140, 40)
        df = pd.DataFrame(
            {
                "open": close - 1,
                "high": close + 1,
                "low": close - 2,
                "close": close,
                "volume": np.linspace(1_000_000, 1_400_000, 40),
            },
            index=idx,
        )
        m = self.skill._compute_quality_metrics(df)
        self.assertGreater(m["trend_eff20"], 0.5)
        self.assertGreaterEqual(m["max_dd20_pct"], 0.0)

    def test_recommend_position_size_respects_notional_cap(self):
        with patch("skills.StockScreenerSkill.Config.STOCK_RISK_BUDGET_IDR", 100000.0, create=True), \
             patch("skills.StockScreenerSkill.Config.STOCK_MAX_POSITION_NOTIONAL_IDR", 150000.0, create=True):
            shares, notional, risk_per_share = self.skill._recommend_position_size(1000.0, 950.0)
        self.assertEqual(risk_per_share, 50.0)
        self.assertEqual(shares, 150)
        self.assertEqual(notional, 150000.0)

    def test_beta_alpha_and_conviction_weight(self):
        idx = pd.date_range("2025-01-01", periods=80, freq="D")
        bench = np.linspace(100, 120, 80)
        sym = (bench * 1.2) + np.linspace(0, 5, 80)
        sym_df = pd.DataFrame({"close": sym}, index=idx)
        bench_df = pd.DataFrame({"close": bench}, index=idx)

        ba = self.skill._compute_beta_alpha(sym_df, bench_df, bars=60)
        self.assertGreater(ba["beta"], 0.5)
        self.assertIsInstance(ba["alpha_pct"], float)

        low = self.skill._conviction_weight(40.0, -0.2, 12.0)
        high = self.skill._conviction_weight(85.0, 1.8, 3.0)
        self.assertGreater(high, low)

    def test_probabilistic_ev_improves_with_better_signal(self):
        weak = self.skill._compute_win_prob_and_ev(
            score=48.0,
            rr=1.2,
            sharpe20=-0.3,
            max_dd20_pct=14.0,
            entry=1000.0,
            stop=960.0,
            tp1=1060.0,
        )
        strong = self.skill._compute_win_prob_and_ev(
            score=86.0,
            rr=2.0,
            sharpe20=1.7,
            max_dd20_pct=4.0,
            entry=1000.0,
            stop=960.0,
            tp1=1120.0,
        )
        self.assertGreater(strong[0], weak[0])  # win_prob
        self.assertGreater(strong[2], weak[2])  # ev_r

    def test_hit_prob_and_kelly_bounds(self):
        idx = pd.date_range("2025-02-01", periods=80, freq="D")
        close = np.linspace(100, 130, 80)
        df = pd.DataFrame({"close": close}, index=idx)
        prob = self.skill._estimate_hit_prob(df["close"], entry=120.0, stop=110.0, tp=135.0, lookback=40)
        self.assertGreaterEqual(prob, 0.02)
        self.assertLessEqual(prob, 0.98)
        kelly = self.skill._calc_kelly_fraction(0.6, 2.0)
        self.assertGreater(kelly, 0.0)

    def test_event_blackout_reason_matches_window(self):
        self.skill.event_blackout_enabled = True
        self.skill._event_blackout_windows = {
            "BBCA.JK": [(pd.Timestamp("2025-02-10"), pd.Timestamp("2025-02-12"), "earnings")]
        }
        reason = self.skill._event_blackout_reason("BBCA.JK", pd.Timestamp("2025-02-11"))
        self.assertEqual(reason, "earnings")


if __name__ == "__main__":
    unittest.main()

