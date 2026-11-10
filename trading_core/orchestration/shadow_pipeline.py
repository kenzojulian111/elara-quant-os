from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Dict, List

from trading_core.coordination.action_context import ActionContext
from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.event_types import ATTENTION_EVENT, DECISION_EVENT, GOVERNANCE_EVENT, MARKET_DATA_EVENT, META_EVENT, OBJECTIVE_EVENT, PORTFOLIO_EVENT, REFLECTION_EVENT, RISK_EVENT, TEMPORAL_EVENT
from trading_core.coordination.idempotency import IdempotencyCache
from trading_core.data_contracts import DecisionTraceStep, MarketState
from trading_core.orchestration.attention_engine import AttentionEngine
from trading_core.execution.adapter import ExecutionAdapter, ShadowExecutionAdapter, ExecutionReceipt
from trading_core.indicators.basic import IndicatorCalculator
from trading_core.orchestration.decision_core import DecisionCore, DecisionCoreConfig
from trading_core.orchestration.decision_governor import DecisionGovernor, GovernanceContext
from trading_core.orchestration.decision_reasoner import DecisionReasoner
from trading_core.orchestration.live_pipeline import LivePipelineConfig, LiveTradingPipeline
from trading_core.orchestration.objective_engine import ObjectiveEngine
from trading_core.orchestration.portfolio_brain import PortfolioBrain
from trading_core.orchestration.temporal_reasoner import TemporalReasoner
from trading_core.strategy.base import PositionState, Strategy
from trading_core.strategy.factory import StrategyFactory
from utils.health import DependencyCircuitBreaker, assess_runtime_mode, snapshot as health_snapshot


@dataclass
class ShadowPipelineConfig:
    risk_per_trade_pct: float = 0.01
    max_risk_per_trade_pct: float = 0.02
    max_drawdown_pct: float = 15.0
    preferred_objective: str = "adaptive"
    ledger_path: str = "runtime/shadow_ledger.jsonl"
    source: str = "shadow_pipeline"
    idempotency_ttl_sec: int = 600
    dependency_circuit_fail_threshold: int = 2
    dependency_circuit_cooldown_sec: int = 120


class ShadowTradingPipeline:
    def __init__(
        self,
        portfolio_brain: PortfolioBrain,
        config: ShadowPipelineConfig | None = None,
        *,
        execution_adapter: ExecutionAdapter | None = None,
    ):
        self.portfolio_brain = portfolio_brain
        self.config = config or ShadowPipelineConfig()
        self.ledger = EventLedger(self.config.ledger_path)
        self.idempotency = IdempotencyCache(self.config.idempotency_ttl_sec)
        self.execution = execution_adapter or ShadowExecutionAdapter(self.ledger)
        self.factory = StrategyFactory()
        self.indicators = IndicatorCalculator()
        self.positions: Dict[str, PositionState] = {}
        self.strategies: Dict[str, Strategy] = {}
        self.live_pipeline = LiveTradingPipeline(
            portfolio_brain,
            LivePipelineConfig(
                max_risk_per_trade_pct=self.config.max_risk_per_trade_pct,
                max_drawdown_pct=self.config.max_drawdown_pct,
            ),
        )
        self.decision_core = DecisionCore(
            self.live_pipeline,
            DecisionCoreConfig(
                risk_per_trade_pct=self.config.risk_per_trade_pct,
            ),
        )
        self.governor = DecisionGovernor()
        self.reasoner = DecisionReasoner()
        self.attention = AttentionEngine()
        self.objective = ObjectiveEngine()
        self.temporal = TemporalReasoner()
        self.dependency_circuit = DependencyCircuitBreaker(
            fail_threshold=self.config.dependency_circuit_fail_threshold,
            cooldown_sec=self.config.dependency_circuit_cooldown_sec,
        )

    def process_state(
        self,
        state: MarketState,
        *,
        current_drawdown_pct: float = 0.0,
        operator_allows_trading: bool = True,
        operator_reason: str = "",
        system_health_score: float = 100.0,
        dependency_states: Dict[str, str] | None = None,
    ) -> List[ExecutionReceipt]:
        context = ActionContext.create(self.config.source)
        self.ledger.append(
            MARKET_DATA_EVENT,
            {
                "action_id": context.action_id,
                "timestamp": state.ts.isoformat(),
                "symbol": state.symbol,
                "open": state.open,
                "high": state.high,
                "low": state.low,
                "close": state.close,
                "volume": state.volume,
            },
            correlation_id=context.action_id,
            source=self.config.source,
        )
        indicators = self.indicators.update(state)
        receipts: List[ExecutionReceipt] = []
        dependency_observation = self.dependency_circuit.observe(dict(dependency_states or {}))
        governance_context = GovernanceContext(
            operator_allows_trading=operator_allows_trading,
            operator_reason=operator_reason,
            system_health_score=system_health_score,
            dependency_states=dict(dependency_observation.states),
        )
        runtime_assessment = assess_runtime_mode(
            dependency_states=governance_context.dependency_states,
            metrics=health_snapshot(),
            system_health_score=system_health_score,
        )
        runtime_reasons = self._merge_reasons(
            dependency_observation.reasons,
            runtime_assessment.reasons,
        )
        runtime_assessment = replace(runtime_assessment, reasons=runtime_reasons)
        governance_context = replace(
            governance_context,
            runtime_mode=runtime_assessment.mode,
            runtime_reasons=runtime_reasons,
        )
        self.ledger.append_dataclass(META_EVENT, runtime_assessment)
        portfolio_plan = None
        build_decision_plan = getattr(self.portfolio_brain, "build_decision_plan", None)
        if callable(build_decision_plan):
            portfolio_plan = build_decision_plan(state)
            allocations = list(portfolio_plan.allocations)
            self.ledger.append_dataclass(PORTFOLIO_EVENT, portfolio_plan)
        else:
            allocations = self.portfolio_brain.build_allocation(state)
        attention_profile = self.attention.assess(
            state=state,
            runtime_assessment=runtime_assessment,
            portfolio_plan=portfolio_plan,
        )
        attention_profile, allocations = self.attention.focus_allocations(
            allocations=allocations,
            positions=self.positions,
            profile=attention_profile,
        )
        self.ledger.append_dataclass(ATTENTION_EVENT, attention_profile)
        temporal_context = self.temporal.observe_state(
            state,
            portfolio_plan=portfolio_plan,
        )
        self.ledger.append_dataclass(TEMPORAL_EVENT, temporal_context)
        objective_profile = self.objective.assess(
            preferred_objective=self.config.preferred_objective,
            current_drawdown_pct=current_drawdown_pct,
            runtime_assessment=runtime_assessment,
            portfolio_plan=portfolio_plan,
        )
        self.ledger.append_dataclass(OBJECTIVE_EVENT, objective_profile)

        for allocation in allocations:
            strategy = self._get_strategy(allocation.strategy_id)
            if not strategy:
                continue
            position = self.positions.get(allocation.strategy_id, PositionState())
            temporal_bias = self.temporal.assess_strategy(
                allocation.strategy_id,
                getattr(strategy, "profile", None),
                temporal_context,
            )
            active_position = position
            position, exit_reason, exit_price = self._apply_stop_take(position, state)
            if exit_reason:
                self._record_trade_outcome(
                    allocation.strategy_id,
                    active_position,
                    exit_price,
                    exit_reason,
                )
                self.positions[allocation.strategy_id] = position
                self._log_reflection(context, allocation.strategy_id, state, exit_reason)
                continue

            envelope = self.decision_core.evaluate(
                context=context,
                state=state,
                strategy_id=allocation.strategy_id,
                strategy=strategy,
                indicators=indicators,
                position=position,
                allocation_weight=allocation.weight,
                current_drawdown_pct=current_drawdown_pct,
            )
            # Risk may shape a proposed order, but no executable plan may be
            # exposed before governance and final reasoning approve it.
            envelope = replace(envelope, execution_plan=None)
            objective_bias = self.objective.assess_strategy(
                strategy_profile=getattr(strategy, "profile", None),
                objective_profile=objective_profile,
                decision_action=envelope.decision,
                decision_confidence=envelope.confidence,
                allocation_weight=allocation.weight,
                market_regime=getattr(portfolio_plan, "market_regime", ""),
            )
            governance = self.governor.assess(
                envelope=envelope,
                state=state,
                strategy_profile=getattr(strategy, "profile", None),
                context=governance_context,
            )
            envelope = replace(
                envelope,
                governance=governance,
                pipeline_trace=envelope.pipeline_trace
                + (
                    DecisionTraceStep(
                        step="attention",
                        status=attention_profile.mode,
                        reasons=attention_profile.reasons,
                        details={
                            "priority": attention_profile.priority,
                            "focused_strategy_ids": attention_profile.focused_strategy_ids,
                            "forced_strategy_ids": attention_profile.forced_strategy_ids,
                            "skipped_strategy_ids": attention_profile.skipped_strategy_ids,
                            "max_new_entry_strategies": attention_profile.max_new_entry_strategies,
                        },
                    ),
                    DecisionTraceStep(
                        step="objective",
                        status="blocked" if objective_bias.block_new_entries else "degraded" if objective_bias.confidence_multiplier < 1.0 else "aligned",
                        reasons=self._merge_reasons(objective_profile.reasons, objective_bias.reasons),
                        details={
                            "objective": objective_profile.objective,
                            "requested_objective": objective_profile.requested_objective,
                            "confidence_floor": objective_profile.confidence_floor,
                            "confidence_multiplier": objective_bias.confidence_multiplier,
                        },
                    ),
                    DecisionTraceStep(
                        step="temporal",
                        status="blocked" if temporal_bias.block_new_entries else "degraded" if temporal_bias.confidence_multiplier < 1.0 else "stable",
                        reasons=self._merge_reasons(temporal_context.reasons, temporal_bias.reasons),
                        details={
                            "current_regime": temporal_context.current_regime,
                            "dominant_regime": temporal_context.dominant_regime,
                            "regime_streak": temporal_context.regime_streak,
                            "regime_persistence": temporal_context.regime_persistence,
                            "loss_streak": temporal_bias.loss_streak,
                            "recent_outcomes": temporal_bias.recent_outcomes,
                            "confidence_multiplier": temporal_bias.confidence_multiplier,
                        },
                    ),
                    DecisionTraceStep(
                        step="runtime_mode",
                        status=runtime_assessment.mode,
                        reasons=runtime_assessment.reasons,
                        details={
                            "allow_new_entries": runtime_assessment.allow_new_entries,
                            "allow_background_tasks": runtime_assessment.allow_background_tasks,
                            "dependency_impacts": runtime_assessment.dependency_impacts,
                        },
                    ),
                    DecisionTraceStep(
                        step="governance",
                        status="approved" if governance.approved else "blocked",
                        reasons=governance.reasons or (governance.reason,),
                        details={
                            "governance_mode": governance.mode,
                            "runtime_mode": governance.runtime_mode,
                        },
                    ),
                ),
            )
            if not governance.approved and envelope.decision in {"enter_long", "enter_short"}:
                envelope = replace(
                    envelope,
                    status="blocked_governor",
                    reasons=self._merge_reasons(
                        envelope.reasons,
                        governance.reasons or (governance.reason,),
                    ),
                )
            envelope = self.reasoner.synthesize(
                envelope,
                portfolio_plan=portfolio_plan,
                objective_profile=objective_profile,
                objective_bias=objective_bias,
                temporal_context=temporal_context,
                temporal_bias=temporal_bias,
                runtime_assessment=runtime_assessment,
                governance=governance,
            )
            if (
                envelope.reasoned_decision
                and not envelope.reasoned_decision.approved
                and governance.approved
                and envelope.status == "ready"
            ):
                envelope = replace(
                    envelope,
                    status="blocked_reasoning",
                    reasons=self._merge_reasons(
                        envelope.reasons,
                        envelope.reasoned_decision.constraints,
                        ("reasoned_decision_veto",),
                    ),
                )
            if (
                envelope.status == "ready"
                and envelope.candidate is not None
                and envelope.risk is not None
                and governance.approved
                and envelope.reasoned_decision is not None
                and envelope.reasoned_decision.approved
                and envelope.reasoned_decision.action == envelope.decision
            ):
                proposed_plan = self.decision_core._build_plan(
                    envelope.candidate,
                    envelope.risk,
                    envelope.trace_id,
                    envelope.reasons,
                )
                if proposed_plan is not None:
                    envelope = replace(
                        envelope,
                        execution_plan=replace(
                            proposed_plan,
                            execution_id=self._execution_id(state, allocation.strategy_id, envelope.decision),
                        ),
                    )
            self.ledger.append_dataclass(
                DECISION_EVENT,
                self.decision_core.to_decision_record(envelope, context),
            )
            if envelope.risk is not None:
                self.ledger.append_dataclass(RISK_EVENT, envelope.risk)
            self.ledger.append_dataclass(GOVERNANCE_EVENT, governance)

            if envelope.status == "exit_requested" and position.direction and governance.approved:
                self._record_trade_outcome(
                    allocation.strategy_id,
                    position,
                    state.close,
                    ",".join(envelope.reasons) or "strategy_exit",
                )
                self.positions[allocation.strategy_id] = PositionState()
                self._log_reflection(
                    context,
                    allocation.strategy_id,
                    state,
                    ",".join(envelope.reasons) or "strategy_exit",
                )
                continue

            if envelope.execution_plan is None or not governance.approved:
                self.positions[allocation.strategy_id] = position
                continue
            if envelope.reasoned_decision and (
                not envelope.reasoned_decision.approved
                or envelope.reasoned_decision.action != envelope.decision
            ):
                self.positions[allocation.strategy_id] = position
                continue

            if self.idempotency.seen(self._decision_key(state, allocation.strategy_id, envelope.decision)):
                continue

            receipt = self.execution.execute(envelope.execution_plan, context)
            receipts.append(receipt)
            self.positions[allocation.strategy_id] = PositionState(
                direction=envelope.execution_plan.direction,
                entry_price=envelope.execution_plan.entry_price,
                stop_loss=envelope.execution_plan.stop_loss,
                take_profit=envelope.execution_plan.take_profit,
                units=None,
            )

        return receipts

    def _get_strategy(self, strategy_id: str) -> Strategy | None:
        if strategy_id in self.strategies:
            return self.strategies[strategy_id]
        profile = self.portfolio_brain.library.profiles.get(strategy_id)
        if not profile:
            return None
        strategy = self.factory.create(profile)
        self.strategies[strategy_id] = strategy
        return strategy

    def _apply_stop_take(self, position: PositionState, state: MarketState) -> tuple[PositionState, str | None, float | None]:
        if not position.direction:
            return position, None, None
        if position.direction == "long":
            if position.stop_loss is not None and state.low <= position.stop_loss:
                return PositionState(), "stop_loss_hit", float(position.stop_loss)
            if position.take_profit is not None and state.high >= position.take_profit:
                return PositionState(), "take_profit_hit", float(position.take_profit)
        if position.direction == "short":
            if position.stop_loss is not None and state.high >= position.stop_loss:
                return PositionState(), "stop_loss_hit", float(position.stop_loss)
            if position.take_profit is not None and state.low <= position.take_profit:
                return PositionState(), "take_profit_hit", float(position.take_profit)
        return position, None, None

    def _decision_key(self, state: MarketState, strategy_id: str, action: str) -> str:
        return f"{strategy_id}:{state.ts.isoformat()}:{action}"

    def _execution_id(self, state: MarketState, strategy_id: str, action: str) -> str:
        key = self._decision_key(state, strategy_id, action)
        return f"paper-{sha256(key.encode('utf-8')).hexdigest()[:24]}"

    def _record_trade_outcome(
        self,
        strategy_id: str,
        position: PositionState,
        exit_price: float | None,
        reason: str,
    ) -> None:
        if not position.direction or position.entry_price in (None, 0, 0.0) or exit_price is None:
            return
        entry_price = float(position.entry_price)
        exit_value = float(exit_price)
        direction = 1.0 if str(position.direction) == "long" else -1.0
        pnl_pct = ((exit_value - entry_price) / entry_price) * direction
        self.temporal.record_trade_outcome(
            strategy_id,
            pnl_pct=pnl_pct,
            reason=reason,
        )

    def _log_reflection(self, context: ActionContext, strategy_id: str, state: MarketState, reason: str) -> None:
        self.ledger.append(
            REFLECTION_EVENT,
            {
                "action_id": context.action_id,
                "strategy_id": strategy_id,
                "symbol": state.symbol,
                "reason": reason,
                "timestamp": state.ts.isoformat(),
            },
        )

    @staticmethod
    def _merge_reasons(*groups) -> tuple[str, ...]:
        seen = set()
        out: list[str] = []
        for group in groups:
            for item in group or ():
                text = str(item or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                out.append(text)
        return tuple(out)

