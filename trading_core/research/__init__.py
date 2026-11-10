"""Research, source verification, and multi-source fusion."""

from trading_core.research.autonomous import ResearchClaim, ResearchSynthesizer, SourceAssessment
from trading_core.research.fusion import FusionInput, SituationAssessment, MultiSourceFusion
from trading_core.research.evaluation_loop import ReplayNativeResearchEvaluator, ResearchCandidate, ResearchEvaluation

__all__ = [
    "FusionInput",
    "MultiSourceFusion",
    "ResearchClaim",
    "ResearchSynthesizer",
    "SituationAssessment",
    "SourceAssessment",
    "ReplayNativeResearchEvaluator",
    "ResearchCandidate",
    "ResearchEvaluation",
]
