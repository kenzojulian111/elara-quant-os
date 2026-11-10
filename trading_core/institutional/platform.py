from __future__ import annotations

from dataclasses import dataclass, field

from trading_core.cognition import CognitiveOpinion, MultiAgentValidator
from trading_core.execution import SmartExecutionRouter, VenueQuote
from trading_core.governance import GovernanceEngineV2, GovernanceV2Context
from trading_core.portfolio import InstitutionalExposure, InstitutionalPortfolioCognition
from trading_core.simulation import BlackSwanScenario, BlackSwanSimulator
from trading_core.world_model import CapitalFlowObservation, CapitalFlowSimulator


@dataclass(frozen=True)
class InstitutionalDecisionContext:
    symbol: str
    quantity: float
    runtime_mode: str
    uncertainty: float
    signed_artifact: bool
    capital_flows: tuple[CapitalFlowObservation, ...] = field(default_factory=tuple)
    exposures: tuple[InstitutionalExposure, ...] = field(default_factory=tuple)
    venue_quotes: tuple[VenueQuote, ...] = field(default_factory=tuple)
    cognitive_opinions: tuple[CognitiveOpinion, ...] = field(default_factory=tuple)
    stress_loss_by_symbol: dict[str, float] = field(default_factory=dict)
    black_swan: BlackSwanScenario | None = None


@dataclass(frozen=True)
class InstitutionalDecisionReport:
    action: str
    allowed: bool
    confidence: float
    governance_mode: str
    execution_venue: str
    capital_risk_state: str
    hedge_required: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


class InstitutionalFinancialCognition:
    def __init__(
        self,
        *,
        capital_flow: CapitalFlowSimulator | None = None,
        portfolio: InstitutionalPortfolioCognition | None = None,
        governance: GovernanceEngineV2 | None = None,
        validator: MultiAgentValidator | None = None,
        router: SmartExecutionRouter | None = None,
        black_swan: BlackSwanSimulator | None = None,
    ) -> None:
        self.capital_flow = capital_flow or CapitalFlowSimulator()
        self.portfolio = portfolio or InstitutionalPortfolioCognition()
        self.governance = governance or GovernanceEngineV2()
        self.validator = validator or MultiAgentValidator()
        self.router = router or SmartExecutionRouter()
        self.black_swan = black_swan or BlackSwanSimulator()

    def decide(self, context: InstitutionalDecisionContext) -> InstitutionalDecisionReport:
        flow = self.capital_flow.infer(context.capital_flows)
        portfolio = self.portfolio.assess(
            context.exposures,
            stress_loss_by_symbol=context.stress_loss_by_symbol,
        )
        governance = self.governance.evaluate(
            GovernanceV2Context(
                runtime_mode=context.runtime_mode,
                uncertainty=context.uncertainty,
                portfolio_drawdown_probability=portfolio.drawdown_probability,
                signed_artifact=context.signed_artifact,
            )
        )
        validation = self.validator.validate(context.cognitive_opinions)
        route = self.router.route(quantity=context.quantity, quotes=context.venue_quotes)
        shock = self.black_swan.run(context.black_swan) if context.black_swan else None

        blockers: list[str] = []
        if not governance.allowed:
            blockers.extend(governance.reasons)
        if not validation.approved:
            blockers.append("cognitive_consensus_not_approved")
        if not route.allowed:
            blockers.extend(route.reasons)
        if flow.risk_state == "risk_off":
            blockers.append("capital_flow_risk_off")
        if shock and not shock.execution_allowed:
            blockers.append("black_swan_execution_block")

        action = validation.decision if validation.decision else "HOLD"
        allowed = not blockers and action not in {"HOLD", "BLOCK"}
        reasons = (
            *flow.reasons,
            *portfolio.reasons,
            *governance.reasons,
            *validation.contradictions,
            *route.reasons,
            *((shock.reasons if shock else ())),
            *tuple(blockers),
        )
        return InstitutionalDecisionReport(
            action=action if allowed else "HOLD",
            allowed=allowed,
            confidence=validation.confidence,
            governance_mode=governance.mode,
            execution_venue=route.venue,
            capital_risk_state=flow.risk_state,
            hedge_required=portfolio.hedge_required,
            reasons=tuple(dict.fromkeys(reasons)),
        )
