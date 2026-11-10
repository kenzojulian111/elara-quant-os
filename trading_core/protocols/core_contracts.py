from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Iterable, Mapping

from trading_core.data_contracts import (
    DecisionEnvelope,
    DecisionTraceStep,
    ExecutionPlan,
    GovernanceDecision,
    ReasonedDecision,
    ReasoningComponent,
)
from trading_core.orchestration.objective_engine import ObjectiveProfile, StrategyObjectiveBias
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.temporal_reasoner import StrategyTemporalBias, TemporalContext
from utils.health import RuntimeModeAssessment


CORE_CONTRACT_VERSION = "elara.platform.protocol.v1"


@dataclass(frozen=True)
class PlatformContract:
    contract_id: str
    version: str
    owner: str
    type_ref: type
    field_names: tuple[str, ...]
    stability: str = "frozen"

    def actual_field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in fields(self.type_ref))

    def to_dict(self) -> Mapping[str, object]:
        return {
            "contract_id": self.contract_id,
            "version": self.version,
            "owner": self.owner,
            "type": f"{self.type_ref.__module__}.{self.type_ref.__name__}",
            "field_names": tuple(self.field_names),
            "stability": self.stability,
        }


@dataclass(frozen=True)
class CoreContractReport:
    version: str
    status: str
    checked: int
    mismatches: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Mapping[str, object]:
        return {
            "version": self.version,
            "status": self.status,
            "checked": int(self.checked),
            "mismatches": tuple(self.mismatches),
        }


def build_core_contract_registry() -> tuple[PlatformContract, ...]:
    return (
        PlatformContract(
            "decision_trace_step",
            CORE_CONTRACT_VERSION,
            "decision_core",
            DecisionTraceStep,
            ("step", "status", "reasons", "duration_ms", "details"),
        ),
        PlatformContract(
            "reasoning_component",
            CORE_CONTRACT_VERSION,
            "decision_reasoner",
            ReasoningComponent,
            ("source", "label", "confidence", "weight", "verdict", "reasons", "details"),
        ),
        PlatformContract(
            "reasoned_decision",
            CORE_CONTRACT_VERSION,
            "decision_reasoner",
            ReasonedDecision,
            (
                "action",
                "approved",
                "confidence",
                "summary",
                "explanation",
                "reasons",
                "constraints",
                "regime",
                "risk_score",
                "probability",
                "uncertainty",
                "expected_utility",
                "cognitive_mode",
                "component_scores",
                "components",
            ),
        ),
        PlatformContract(
            "governance_decision",
            CORE_CONTRACT_VERSION,
            "decision_governor",
            GovernanceDecision,
            ("approved", "mode", "reason", "reasons", "severity", "runtime_mode", "runtime_reasons"),
        ),
        PlatformContract(
            "execution_plan",
            CORE_CONTRACT_VERSION,
            "decision_core",
            ExecutionPlan,
            (
                "strategy_id",
                "symbol",
                "direction",
                "entry_price",
                "size_pct",
                "stop_loss",
                "take_profit",
                "trace_id",
                "decision_reasons",
                "risk_reason",
                "execution_id",
            ),
        ),
        PlatformContract(
            "decision_envelope",
            CORE_CONTRACT_VERSION,
            "decision_core",
            DecisionEnvelope,
            (
                "trace_id",
                "strategy_id",
                "symbol",
                "status",
                "decision",
                "confidence",
                "reasons",
                "pipeline_trace",
                "allocation_weight",
                "current_drawdown_pct",
                "candidate",
                "risk",
                "governance",
                "execution_plan",
                "reasoned_decision",
            ),
        ),
        PlatformContract(
            "objective_profile",
            CORE_CONTRACT_VERSION,
            "objective_engine",
            ObjectiveProfile,
            ("objective", "requested_objective", "confidence_floor", "reasons"),
        ),
        PlatformContract(
            "strategy_objective_bias",
            CORE_CONTRACT_VERSION,
            "objective_engine",
            StrategyObjectiveBias,
            ("strategy_id", "confidence_multiplier", "block_new_entries", "reasons"),
        ),
        PlatformContract(
            "temporal_context",
            CORE_CONTRACT_VERSION,
            "temporal_reasoner",
            TemporalContext,
            (
                "symbol",
                "current_regime",
                "dominant_regime",
                "regime_streak",
                "regime_persistence",
                "recent_regimes",
                "reasons",
                "strategy_biases",
            ),
        ),
        PlatformContract(
            "strategy_temporal_bias",
            CORE_CONTRACT_VERSION,
            "temporal_reasoner",
            StrategyTemporalBias,
            ("strategy_id", "confidence_multiplier", "block_new_entries", "loss_streak", "recent_outcomes", "reasons"),
        ),
        PlatformContract(
            "portfolio_decision_plan",
            CORE_CONTRACT_VERSION,
            "portfolio_brain",
            PortfolioDecisionPlan,
            (
                "symbol",
                "market_regime",
                "selected_ids",
                "blocked_ids",
                "suppressed_ids",
                "allocations",
                "review_status",
                "reasons",
            ),
        ),
        PlatformContract(
            "runtime_mode_assessment",
            CORE_CONTRACT_VERSION,
            "runtime_health",
            RuntimeModeAssessment,
            (
                "mode",
                "reasons",
                "allow_new_entries",
                "allow_background_tasks",
                "dependency_states",
                "dependency_impacts",
                "metrics",
            ),
        ),
    )


def validate_core_contracts(
    registry: Iterable[PlatformContract] | None = None,
) -> CoreContractReport:
    contracts = tuple(registry or build_core_contract_registry())
    mismatches: list[str] = []
    for contract in contracts:
        actual = contract.actual_field_names()
        if actual != contract.field_names:
            mismatches.append(
                f"{contract.contract_id}: expected={contract.field_names} actual={actual}"
            )
    return CoreContractReport(
        version=CORE_CONTRACT_VERSION,
        status="critical" if mismatches else "ok",
        checked=len(contracts),
        mismatches=tuple(mismatches),
    )
