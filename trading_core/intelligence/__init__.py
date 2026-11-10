"""Decision intelligence foundations for regime, probability, and explanation."""

from trading_core.intelligence.bayesian import BayesianEvidence, BayesianPosterior, BayesianSignalModel
from trading_core.intelligence.causal_graph import (
    CausalEdge,
    CausalGraph,
    CausalInferenceResult,
    CausalNode,
    CausalPathActivation,
    default_macro_causal_graph,
)
from trading_core.intelligence.calibration import (
    AdaptiveUncertaintyCalibrator,
    CalibrationObservation,
    UncertaintyCalibration,
    UncertaintyCalibrationConfig,
)
from trading_core.intelligence.cognitive_modes import CognitiveMode, CognitiveModeEngine
from trading_core.intelligence.confidence import (
    ConfidenceContribution,
    ConfidencePropagation,
    ConfidencePropagationConfig,
    ConfidencePropagationEngine,
)
from trading_core.intelligence.explainability import DecisionExplanation, explain_decision
from trading_core.intelligence.hierarchy import HierarchicalDecision, HierarchicalDecisionSystem, LayerDecision
from trading_core.intelligence.narrative import DecisionNarrative, build_decision_narrative
from trading_core.intelligence.resource_allocation import AdaptiveResourceAllocator, ResourceBudget
from trading_core.intelligence.runtime_causal import RuntimeCausalAnalyzer, RuntimeCausalFinding, RuntimeCausalReport
from trading_core.intelligence.probabilistic import ProbabilityAssessment, ProbabilisticDecisionEngine
from trading_core.intelligence.regime import RegimeAssessment, RegimeDetector
from trading_core.intelligence.uncertainty import (
    UncertaintyAggregator,
    UncertaintyComponent,
    UncertaintyConfig,
    UncertaintyProfile,
)
from trading_core.intelligence.world_model import CausalAssumption, MarketWorldModel, ScenarioProjection
from trading_core.intelligence.xai import Attribution, AttributionReport, ExplainableAttributionEngine

__all__ = [
    "AdaptiveResourceAllocator",
    "Attribution",
    "AttributionReport",
    "AdaptiveUncertaintyCalibrator",
    "BayesianEvidence",
    "BayesianPosterior",
    "BayesianSignalModel",
    "CausalEdge",
    "CausalGraph",
    "CausalInferenceResult",
    "CausalNode",
    "CausalPathActivation",
    "CausalAssumption",
    "CalibrationObservation",
    "CognitiveMode",
    "CognitiveModeEngine",
    "ConfidenceContribution",
    "ConfidencePropagation",
    "ConfidencePropagationConfig",
    "ConfidencePropagationEngine",
    "DecisionExplanation",
    "DecisionNarrative",
    "ExplainableAttributionEngine",
    "HierarchicalDecision",
    "HierarchicalDecisionSystem",
    "LayerDecision",
    "MarketWorldModel",
    "ProbabilityAssessment",
    "ProbabilisticDecisionEngine",
    "RegimeAssessment",
    "RegimeDetector",
    "ResourceBudget",
    "RuntimeCausalAnalyzer",
    "RuntimeCausalFinding",
    "RuntimeCausalReport",
    "ScenarioProjection",
    "UncertaintyAggregator",
    "UncertaintyCalibration",
    "UncertaintyCalibrationConfig",
    "UncertaintyComponent",
    "UncertaintyConfig",
    "UncertaintyProfile",
    "build_decision_narrative",
    "default_macro_causal_graph",
    "explain_decision",
]
