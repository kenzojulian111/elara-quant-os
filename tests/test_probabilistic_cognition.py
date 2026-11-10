import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading_core.data_contracts import DecisionEnvelope, ReasonedDecision, ReasoningComponent
from trading_core.intelligence import (
    AdaptiveUncertaintyCalibrator,
    CalibrationObservation,
    ConfidencePropagationEngine,
    ProbabilisticDecisionEngine,
)


class ProbabilisticCognitionTests(unittest.TestCase):
    def _components(self):
        return (
            ReasoningComponent(
                source="strategy",
                label="enter_long",
                confidence=0.82,
                weight=0.45,
                verdict="support",
                reasons=("trend_alignment",),
            ),
            ReasoningComponent(
                source="runtime",
                label="degraded",
                confidence=0.45,
                weight=0.10,
                verdict="degrade",
                reasons=("support_dependency_down=redis",),
            ),
        )

    def test_confidence_propagation_separates_confidence_from_uncertainty(self):
        propagation = ConfidencePropagationEngine().propagate(self._components(), base_confidence=0.82)

        self.assertLess(propagation.propagated_confidence, 0.82)
        self.assertGreater(propagation.uncertainty, 0.0)
        self.assertIn("runtime", {item.source for item in propagation.contributions})

    def test_adaptive_uncertainty_calibration_raises_uncertainty_under_bad_runtime(self):
        propagation = ConfidencePropagationEngine().propagate(self._components(), base_confidence=0.82)
        calibrator = AdaptiveUncertaintyCalibrator(
            observations=(CalibrationObservation(predicted_probability=0.8, outcome=0.0, weight=1.0),)
        )

        calibrated = calibrator.calibrate(
            probability=0.8,
            raw_uncertainty=0.10,
            propagation=propagation,
            runtime_mode="safe",
        )

        self.assertGreater(calibrated.calibrated_uncertainty, 0.10)
        self.assertGreater(calibrated.runtime_penalty, 0.0)
        self.assertGreater(calibrated.calibration_error, 0.0)

    def test_probabilistic_engine_includes_propagation_and_calibration(self):
        reasoned = ReasonedDecision(
            action="enter_long",
            approved=True,
            confidence=0.78,
            summary="enter long",
            explanation="trend supports entry",
            reasons=("trend_alignment",),
            components=self._components(),
        )
        envelope = DecisionEnvelope(
            trace_id="trace-1",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="ready",
            decision="enter_long",
            confidence=0.82,
            reasoned_decision=reasoned,
        )

        assessment = ProbabilisticDecisionEngine().assess(envelope)
        scores = ProbabilisticDecisionEngine().enrich_envelope_scores(envelope)

        self.assertGreater(assessment.probability, 0.0)
        self.assertGreaterEqual(assessment.uncertainty, assessment.raw_uncertainty)
        self.assertIn("confidence_propagation", scores)
        self.assertIn("uncertainty_calibration", scores)


if __name__ == "__main__":
    unittest.main()
