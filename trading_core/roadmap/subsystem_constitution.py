from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class SubsystemConstitution:
    subsystem: str
    purpose: str
    authority: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    owned_state: tuple[str, ...]
    produced_events: tuple[str, ...]
    consumed_events: tuple[str, ...]
    failure_semantics: str
    owner_module: str

    def to_dict(self) -> Mapping[str, object]:
        return {
            "subsystem": self.subsystem,
            "purpose": self.purpose,
            "authority": tuple(self.authority),
            "forbidden_actions": tuple(self.forbidden_actions),
            "owned_state": tuple(self.owned_state),
            "produced_events": tuple(self.produced_events),
            "consumed_events": tuple(self.consumed_events),
            "failure_semantics": self.failure_semantics,
            "owner_module": self.owner_module,
        }


@dataclass(frozen=True)
class SubsystemConstitutionReport:
    status: str
    checked: int
    findings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Mapping[str, object]:
        return {"status": self.status, "checked": int(self.checked), "findings": tuple(self.findings)}


class SubsystemConstitutionChecker:
    def __init__(self, root: str | Path = ".", constitutions: Iterable[SubsystemConstitution] | None = None):
        self.root = Path(root)
        self.constitutions = tuple(constitutions or default_subsystem_constitutions())

    def build_report(self) -> SubsystemConstitutionReport:
        findings: list[str] = []
        for constitution in self.constitutions:
            if not constitution.authority:
                findings.append(f"{constitution.subsystem}:missing_authority")
            if not constitution.forbidden_actions:
                findings.append(f"{constitution.subsystem}:missing_forbidden_actions")
            if not constitution.failure_semantics:
                findings.append(f"{constitution.subsystem}:missing_failure_semantics")
            if not (self.root / constitution.owner_module).exists():
                findings.append(f"{constitution.subsystem}:missing_owner_module:{constitution.owner_module}")
        return SubsystemConstitutionReport(
            status="critical" if findings else "ok",
            checked=len(self.constitutions),
            findings=tuple(findings),
        )


def default_subsystem_constitutions() -> tuple[SubsystemConstitution, ...]:
    return (
        SubsystemConstitution(
            subsystem="portfolio_brain",
            purpose="Select strategy allocation and concentration posture.",
            authority=("select_strategy_ids", "suppress_strategy_ids", "publish_portfolio_plan"),
            forbidden_actions=("submit_execution", "bypass_risk", "mutate_runtime_health"),
            owned_state=("last_portfolio_plan", "strategy_reviews", "allocation_weights"),
            produced_events=("PORTFOLIO_EVENT",),
            consumed_events=("MARKET_STATE", "STRATEGY_PERFORMANCE"),
            failure_semantics="Degrade to empty allocation rather than invent exposure.",
            owner_module="trading_core/orchestration/portfolio_brain.py",
        ),
        SubsystemConstitution(
            subsystem="objective_engine",
            purpose="Translate operator and runtime intent into decision bias.",
            authority=("select_objective_profile", "raise_confidence_floor", "block_new_entries_by_objective"),
            forbidden_actions=("submit_execution", "hide_objective_shift", "mutate_strategy_signal"),
            owned_state=("objective_config",),
            produced_events=("OBJECTIVE_EVENT",),
            consumed_events=("PORTFOLIO_EVENT", "RUNTIME_MODE_ASSESSED"),
            failure_semantics="Fall back to balanced or preserve capital when runtime context is unsafe.",
            owner_module="trading_core/orchestration/objective_engine.py",
        ),
        SubsystemConstitution(
            subsystem="temporal_reasoner",
            purpose="Apply short-horizon memory and regime persistence.",
            authority=("observe_regime", "penalize_loss_streak", "block_temporally_unsafe_entries"),
            forbidden_actions=("submit_execution", "mutate_positions", "erase_prior_reasons"),
            owned_state=("regime_history", "outcome_history"),
            produced_events=("TEMPORAL_EVENT",),
            consumed_events=("MARKET_STATE", "PORTFOLIO_EVENT", "TRADE_OUTCOME"),
            failure_semantics="If memory is insufficient, expose low-context reasons instead of silent confidence boosts.",
            owner_module="trading_core/orchestration/temporal_reasoner.py",
        ),
        SubsystemConstitution(
            subsystem="decision_core",
            purpose="Collect strategy signal, build candidate, and apply risk before execution eligibility.",
            authority=("create_trace_id", "build_trade_candidate", "build_execution_plan"),
            forbidden_actions=("approve_entry_without_risk", "skip_trace", "override_governance"),
            owned_state=("decision_core_config",),
            produced_events=("SIGNAL_RECEIVED", "DECISION_APPROVED", "DECISION_BLOCKED"),
            consumed_events=("MARKET_STATE", "INDICATOR_SNAPSHOT", "POSITION_STATE"),
            failure_semantics="Block invalid candidates and preserve trace reasons.",
            owner_module="trading_core/orchestration/decision_core.py",
        ),
        SubsystemConstitution(
            subsystem="decision_governor",
            purpose="Enforce operator, dependency, market, and runtime permission gates.",
            authority=("approve_entry", "block_entry", "preserve_exit_permission"),
            forbidden_actions=("mutate_raw_signal", "create_execution_plan", "run_before_runtime_mode"),
            owned_state=("governance_config",),
            produced_events=("GOVERNANCE_APPROVED", "GOVERNANCE_BLOCKED"),
            consumed_events=("RUNTIME_MODE_ASSESSED", "DECISION_ENVELOPE"),
            failure_semantics="Fail toward entry block while keeping safe exits possible.",
            owner_module="trading_core/orchestration/decision_governor.py",
        ),
        SubsystemConstitution(
            subsystem="decision_reasoner",
            purpose="Fuse components into final explainable probabilistic decision.",
            authority=("synthesize_final_action", "attach_probability", "close_reasoning_trace"),
            forbidden_actions=("erase_trace_history", "approve_entry_without_execution_plan", "hide_veto_constraints"),
            owned_state=("reasoning_weights",),
            produced_events=("REASONING_SYNTHESIZED",),
            consumed_events=("DECISION_ENVELOPE", "RISK_APPROVED", "GOVERNANCE_APPROVED", "RUNTIME_MODE_ASSESSED"),
            failure_semantics="Expose uncertainty and constraints rather than manufacturing confidence.",
            owner_module="trading_core/orchestration/decision_reasoner.py",
        ),
    )
