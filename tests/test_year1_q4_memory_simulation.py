import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import MarketState
from trading_core.memory import OperationalMemoryStore
from trading_core.ml.lightweight import FeatureVector, LightweightEnsemble
from trading_core.portfolio.intelligence import PortfolioIntelligence, PositionExposure
from trading_core.simulation.fault_injection import FaultInjector, FaultRule
from trading_core.simulation.macro import MacroShock, MacroShockSimulator


class Year1Q4MemorySimulationTests(unittest.TestCase):
    def _state(self) -> MarketState:
        return MarketState(
            ts=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            symbol="BTC/USDT",
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
            liquidity="deep",
        )

    def test_operational_memory_search_and_summary_uses_decay(self):
        with tempfile.TemporaryDirectory(prefix="elara_memory_") as tmpdir:
            store = OperationalMemoryStore(os.path.join(tmpdir, "memory.jsonl"), half_life_sec=100)
            old = store.append(scope="strategy", kind="outcome", content="old loss", confidence=1.0, tags=("loss",), ts=0)
            fresh = store.append(scope="strategy", kind="outcome", content="fresh win", confidence=0.8, tags=("win",), ts=100)

            results = store.search(scope="strategy", limit=2, now=100)
            summary = store.summarize(scope="strategy", now=100)

        self.assertEqual(fresh.memory_id, results[0].memory_id)
        self.assertIn("win", summary.top_tags)
        self.assertEqual(2, summary.count)
        self.assertLess(old.score(now=100, half_life_sec=100), fresh.score(now=100, half_life_sec=100))

    def test_fault_injection_applies_slippage_and_liquidity_collapse(self):
        injector = FaultInjector(
            (
                FaultRule("slip", "slippage", severity=1.0),
                FaultRule("thin", "liquidity_collapse", severity=0.8),
            ),
            seed=1,
        )

        result = injector.apply(self._state())

        self.assertFalse(result.dropped)
        self.assertIn("slip", result.applied)
        self.assertAlmostEqual(102.01, result.state.close)
        self.assertAlmostEqual(200.0, result.state.volume)

    def test_macro_shock_projects_market_stress(self):
        projection = MacroShockSimulator().project(
            MacroShock("rates_up", rate_delta_bps=75, liquidity_delta_pct=-20),
            self._state(),
        )

        self.assertLess(projection.expected_price_impact_pct, 0)
        self.assertEqual("thin", projection.expected_liquidity)
        self.assertIn("rate_delta_bps=75.0", projection.reasons)

    def test_lightweight_ensemble_predicts_with_confidence(self):
        model = LightweightEnsemble.from_thresholds(
            [
                {"feature": "trend_strength", "threshold": 0.7, "above_label": "buy", "below_label": "hold"},
                {"feature": "risk", "threshold": 0.5, "above_label": "hold", "below_label": "buy"},
            ]
        )

        prediction = model.predict(FeatureVector({"trend_strength": 0.8, "risk": 0.2}))

        self.assertEqual("buy", prediction.label)
        self.assertGreaterEqual(prediction.confidence, 0.5)

    def test_portfolio_intelligence_detects_concentration_and_correlation(self):
        report = PortfolioIntelligence().assess(
            [
                PositionExposure("BTC", "crypto", 700),
                PositionExposure("ETH", "crypto", 300),
            ],
            returns_by_symbol={"BTC": [1, 2, 3], "ETH": [2, 4, 6]},
        )

        self.assertEqual("BTC", report.concentration_symbol)
        self.assertGreater(report.concentration_pct, 0.5)
        self.assertEqual((("BTC", "ETH"),), report.correlation_clusters)


if __name__ == "__main__":
    unittest.main()
