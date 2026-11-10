from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import DecisionEnvelope, ReasoningComponent
from trading_core.intelligence.calibration import AdaptiveUncertaintyCalibrator, UncertaintyCalibration
from trading_core.intelligence.bayesian import BayesianPosterior, BayesianSignalModel
from trading_core.intelligence.confidence import ConfidencePropagation, ConfidencePropagationEngine


@dataclass(frozen=True)
class ProbabilityAssessment:
    action: str
    probability: float
    uncertainty: float
    expected_utility: float
    propagated_confidence: float = 0.0
    raw_uncertainty: float = 0.0
    posterior: BayesianPosterior | None = None
    confidence_propagation: ConfidencePropagation | None = None
    uncertainty_calibration: UncertaintyCalibration | None = None
    probability_model: str = "bayesian_beta"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbabilisticDecisionConfig:
    base_prior: float = 0.5
    risk_penalty: float = 0.25
    uncertainty_floor: float = 0.05


class ProbabilisticDecisionEngine:
    def __init__(
        self,
        config: ProbabilisticDecisionConfig | None = None,
        bayesian_model: BayesianSignalModel | None = None,
        propagation_engine: ConfidencePropagationEngine | None = None,
        uncertainty_calibrator: AdaptiveUncertaintyCalibrator | None = None,
    ):
        self.config = config or ProbabilisticDecisionConfig()
        self.bayesian_model = bayesian_model or BayesianSignalModel()
        self.propagation_engine = propagation_engine or ConfidencePropagationEngine()
        self.uncertainty_calibrator = uncertainty_calibrator or AdaptiveUncertaintyCalibrator()

    def assess(self, envelope: DecisionEnvelope) -> ProbabilityAssessment:
        reasoned = envelope.reasoned_decision
        action = reasoned.action if reasoned else envelope.decision
        components = tuple(reasoned.components if reasoned else ())
        prior = self._prior(envelope)
        propagation = self.propagation_engine.propagate(components, base_confidence=prior)
        posterior = self.bayesian_model.infer(components, prior=prior)
        probability = posterior.probability
        risk_penalty = self._risk_penalty(envelope)
        expected_utility = self._expected_utility(probability, risk_penalty)
        raw_uncertainty = max(float(self.config.uncertainty_floor), float(posterior.uncertainty))
        calibration = self.uncertainty_calibrator.calibrate(
            probability=probability,
            raw_uncertainty=raw_uncertainty,
            propagation=propagation,
            runtime_mode=self._runtime_mode(envelope),
        )
        uncertainty = max(float(self.config.uncertainty_floor), float(calibration.calibrated_uncertainty))
        reasons = (
            *posterior.reasons,
            *propagation.reasons,
            *calibration.reasons,
            f"probability={probability:.4f}",
            f"uncertainty={uncertainty:.4f}",
            f"expected_utility={expected_utility:.4f}",
        )
        if envelope.risk and not envelope.risk.approved:
            reasons = (*reasons, "risk_rejection_dominates_probability")
        return ProbabilityAssessment(
            action=action,
            probability=probability,
            uncertainty=uncertainty,
            expected_utility=expected_utility,
            propagated_confidence=propagation.propagated_confidence,
            raw_uncertainty=raw_uncertainty,
            posterior=posterior,
            confidence_propagation=propagation,
            uncertainty_calibration=calibration,
            probability_model="bayesian_beta",
            reasons=reasons,
        )

    def enrich_envelope_scores(self, envelope: DecisionEnvelope) -> dict[str, object]:
        assessment = self.assess(envelope)
        scores = dict(getattr(envelope.reasoned_decision, "component_scores", {}) or {})
        scores.update(
            {
                "probability": round(float(assessment.probability), 4),
                "uncertainty": round(float(assessment.uncertainty), 4),
                "expected_utility": round(float(assessment.expected_utility), 4),
                "propagated_confidence": round(float(assessment.propagated_confidence), 4),
                "raw_uncertainty": round(float(assessment.raw_uncertainty), 4),
                "probability_model": assessment.probability_model,
                "bayesian_posterior": assessment.posterior.to_dict() if assessment.posterior else {},
                "confidence_propagation": assessment.confidence_propagation.to_dict()
                if assessment.confidence_propagation
                else {},
                "uncertainty_calibration": assessment.uncertainty_calibration.to_dict()
                if assessment.uncertainty_calibration
                else {},
            }
        )
        return scores

    def _prior(self, envelope: DecisionEnvelope) -> float:
        confidence = float(envelope.confidence or self.config.base_prior)
        if envelope.decision in {"hold", "wait", "skip"}:
            return min(confidence, self.config.base_prior)
        return self._clamp(confidence or self.config.base_prior)

    def _component_probability(self, components: tuple[ReasoningComponent, ...], fallback: float) -> float:
        if not components:
            return self._clamp(float(fallback or self.config.base_prior))
        total_weight = sum(max(0.0, float(component.weight)) for component in components)
        if total_weight <= 0:
            return self._clamp(float(fallback or self.config.base_prior))
        support = 0.0
        for component in components:
            confidence = float(component.confidence)
            if component.verdict == "block":
                confidence *= 0.15
            elif component.verdict == "degrade":
                confidence *= 0.55
            support += confidence * float(component.weight)
        return self._clamp(support / total_weight)

    def _risk_penalty(self, envelope: DecisionEnvelope) -> float:
        if envelope.risk is None:
            return 0.0
        if not envelope.risk.approved:
            return 1.0
        return min(float(self.config.risk_penalty), max(0.0, float(envelope.risk.risk_budget_pct or 0.0) * 5.0))

    @staticmethod
    def _runtime_mode(envelope: DecisionEnvelope) -> str:
        governance = envelope.governance
        if governance is not None and governance.runtime_mode:
            return str(governance.runtime_mode)
        reasoned = envelope.reasoned_decision
        scores = dict(getattr(reasoned, "component_scores", {}) or {}) if reasoned else {}
        return str(scores.get("runtime_mode") or "")

    @staticmethod
    def _expected_utility(probability: float, risk_penalty: float) -> float:
        return (float(probability) * 2.0 - 1.0) - float(risk_penalty)

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
