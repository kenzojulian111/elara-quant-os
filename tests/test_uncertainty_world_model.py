import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.data_contracts import ReasoningComponent
from trading_core.intelligence.bayesian import BayesianSignalModel
from trading_core.intelligence.uncertainty import UncertaintyAggregator, UncertaintyComponent
from trading_core.world_model import MacroCausalEvidence, default_macro_world_model


class UncertaintyWorldModelTests(unittest.TestCase):
    def test_uncertainty_profile_tracks_sources_not_only_total(self):
        profile = UncertaintyAggregator().combine(
            (
                UncertaintyComponent("posterior_variance", 0.25, 1.0, "wide_distribution"),
                UncertaintyComponent("evidence_conflict", 0.70, 0.8, "signals_disagree"),
            ),
            epistemic=0.25,
            aleatoric=0.15,
            conflict=0.70,
            data_quality=0.20,
        )

        rendered = profile.to_dict()
        self.assertGreater(profile.total, profile.epistemic)
        self.assertEqual(2, len(rendered["components"]))
        self.assertIn("uncertainty_total", rendered["reasons"][-1])

    def test_bayesian_posterior_exposes_uncertainty_profile(self):
        posterior = BayesianSignalModel().infer(
            (
                ReasoningComponent("strategy", "trend", 0.84, 0.45, "support"),
                ReasoningComponent("runtime", "stale", 0.90, 0.20, "block"),
            ),
            prior=0.5,
        )

        payload = posterior.to_dict()
        self.assertIn("uncertainty_profile", payload)
        self.assertGreater(payload["uncertainty_profile"]["total"], 0.0)
        self.assertGreater(posterior.conflict, 0.0)

    def test_macro_causal_graph_infers_risk_off_state(self):
        state = default_macro_world_model().infer(
            MacroCausalEvidence(
                rates_pressure=0.90,
                liquidity=0.20,
                risk_sentiment=0.20,
                inflation_pressure=0.80,
                dollar_strength=0.80,
            )
        )

        self.assertEqual("risk_off", state.risk_state)
        self.assertEqual("trend_down", state.trend_state)
        self.assertTrue(state.active_paths)
        self.assertGreater(state.uncertainty.total, 0.0)

    def test_macro_causal_graph_infers_risk_on_state(self):
        state = default_macro_world_model().infer(
            MacroCausalEvidence(
                rates_pressure=0.20,
                liquidity=0.85,
                risk_sentiment=0.85,
                inflation_pressure=0.20,
                dollar_strength=0.20,
            )
        )

        self.assertEqual("risk_on", state.risk_state)
        self.assertEqual("trend_up", state.trend_state)
        self.assertGreater(state.beliefs["crypto_risk"], 0.5)


if __name__ == "__main__":
    unittest.main()
