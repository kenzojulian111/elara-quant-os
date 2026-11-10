# Elara Architecture Doctrine

This document turns `roadmap.codex` into an engineering contract. It exists to keep Elara from drifting into a generic AI assistant, a black-box trader, or a pile of disconnected subsystems.

Elara's architectural identity is:

> Governed Autonomous Trading Intelligence System

The system should become more capable only when it also becomes more deterministic, observable, explainable, and survivable.

## Hardest Parts

The hardest areas are not the ones with the most code. They are the ones where unclear responsibility creates impossible runtime states.

1. Layered orchestration

   Hardest overall. Portfolio, attention, objective, temporal memory, strategy, risk, runtime, governance, and execution all influence one decision. If this is not explicit, Elara becomes a chain of hidden vetoes.

2. Explainable decision flow

   Hard because every trade needs a forensic path: signal, context, risk, governance, runtime, final reason, and execution result. A final `BUY` or `HOLD` is not enough.

3. Runtime thinking

   Hard because distributed systems lie. A service can be alive but unusable, connected but stale, or healthy while producing unsafe decisions.

4. Operational safety

   Hard because safety must survive retries, restarts, partial failures, operator commands, stale heartbeats, and exchange ambiguity.

5. Governance philosophy

   Hard culturally. Developers naturally add capability; governance intentionally removes permission when context becomes unsafe.

The easier areas are architectural direction and separation of responsibility, but only if they are written down and enforced during reviews.

## Architectural Direction

Elara is a trading decision platform first.

It should optimize for:

- predictable trading behavior
- bounded autonomy
- explicit runtime state
- explainable decisions
- safe failure handling
- lower blast radius
- fewer active subsystems

It should not optimize for:

- generic assistant breadth
- theatrical autonomy
- LLM-first decision making
- uncontrolled self-improvement
- feature count as a proxy for intelligence

When a roadmap item conflicts with survivability, survivability wins.

## Governance Philosophy

Governance is not an accessory after strategy. It is part of the decision itself.

Governance owns:

- whether new entries are allowed
- whether exits remain allowed during degraded modes
- whether operator state blocks risk-taking
- whether runtime mode changes decision permission
- whether market regime, liquidity, volatility, or dependency health makes a signal unsafe

Governance must be able to veto profitable-looking decisions.

Governance must not silently mutate strategy output. It should return an explicit approval, mode, reason, severity, runtime mode, and runtime reasons.

Current anchor:

- `trading_core/orchestration/decision_governor.py`
- `trading_core/data_contracts.py::GovernanceDecision`

## Separation Of Responsibility

Elara uses separate responsibilities to reduce blast radius.

Core service:

- operator interface
- skill runtime
- health and readiness endpoints
- command submission
- status visibility

Trader service:

- trading daemon lifecycle
- exchange connectivity
- command processing
- execution idempotency
- trader heartbeat/status publishing

Trading core:

- market state contracts
- strategies
- risk decisions
- portfolio decisions
- decision envelopes
- explainable reasoning
- shadow/simulation orchestration

Infrastructure:

- Redis transport
- PostgreSQL persistence
- Prometheus metrics
- Grafana dashboards
- Loki logs
- Tempo traces

Boundary rule:

- Core may request a trade command, but must not bypass trader execution safeguards.
- Trader may execute, but must not invent strategic justification outside the decision contract.
- Trading core may reason, but must not own deployment, secrets, or operator UI.
- Observability may measure all layers, but must not become a control plane.

## Runtime Thinking

Runtime state is not the same thing as process state.

Elara runtime state should answer:

- Are dependencies usable?
- Are data feeds fresh?
- Is the trader heartbeat fresh?
- Is Redis alive and operational?
- Is DB access healthy enough for the requested path?
- Are queues growing?
- Is the runtime allowed to create new entries?
- Are exits still permitted?

Runtime modes:

- `full`: normal operation
- `degraded`: observation and protective behavior continue; new entries may be blocked
- `safe`: no new risk; exits and operator controls remain available
- `recovery`: controlled transition back toward full operation

Current anchor:

- `core/runtime_state.py`
- `utils/health.py`
- `runtime/interfaces/web_factory.py`
- `docs/observability_q1.md`

## Operational Safety

Operational safety is the default posture when uncertainty rises.

Required safety properties:

- fail fast when required dependencies are unavailable
- preserve exits during degraded conditions
- block duplicate execution across retries and restarts
- prefer explicit no-trade over ambiguous trade
- make command identity persistent
- keep runtime transitions explicit
- archive audit data instead of deleting it blindly

Trade execution must be idempotent by persistent identifiers:

- `execution_id`
- `command_id`
- `decision_id`
- `client_order_id`

Safety rule:

> If Elara cannot prove a new entry is safe, it should not create the entry.

## Observability Awareness

Observability is not a dashboard feature. It is the condition for trusting a distributed trading runtime.

Every important path should emit:

- correlation ID
- trace ID
- runtime mode
- decision ID when applicable
- execution ID when applicable
- latency
- status
- reason for rejection or degradation

The target debugging question is:

> What happened first, what changed state, why did governance allow or block it, and what did execution do?

Current anchor:

- `utils/observability.py`
- `utils/json_logging.py`
- `deploy/observability/`
- `docs/observability_q1.md`

## Decision Abstraction

Decision abstractions are the center of the system.

Strategy output is not a final trade. It is only a proposal.

Required decision layers:

- `StrategyDecision`: what the strategy wants
- `TradeCandidate`: the candidate position
- `RiskDecision`: whether risk allows it
- `GovernanceDecision`: whether system context allows it
- `DecisionEnvelope`: the shared transport across the decision path
- `ReasonedDecision`: the final explainable verdict
- `ExecutionPlan`: the only object allowed to reach execution

Current anchor:

- `trading_core/data_contracts.py`
- `trading_core/orchestration/decision_core.py`
- `trading_core/orchestration/decision_reasoner.py`

Rule:

> No execution path should depend only on a raw strategy signal.

## Explainable Decision Flow

Every decision should be reconstructable.

Minimum explanation chain:

```text
market_state
-> indicators
-> strategy_signal
-> candidate_build
-> risk_gate
-> portfolio_context
-> objective_context
-> temporal_context
-> runtime_mode
-> governance
-> reasoned_decision
-> execution_plan_or_veto
```

Each step should carry:

- step name
- status
- reasons
- duration where useful
- details safe for logs

Final decision should answer:

- What action was requested?
- Was it approved?
- What confidence was assigned?
- What constraints applied?
- Which component supported or blocked the action?
- Which runtime/governance mode was active?

## Layered Orchestration

Layered orchestration should make priority explicit.

Recommended precedence:

1. Runtime safety decides whether the system can reason normally.
2. Portfolio brain decides which strategies deserve attention.
3. Attention engine reduces work during degraded or noisy conditions.
4. Objective engine defines the active mission.
5. Temporal reasoner applies short-horizon memory.
6. Strategy proposes an action.
7. Decision core creates the candidate and risk outcome.
8. Governor applies operator, dependency, market, and runtime gates.
9. Decision reasoner creates the final explainable verdict.
10. Execution adapter executes only approved execution plans.

The orchestrator may sequence layers, but it should not hide their outputs.

Current anchor:

- `trading_core/orchestration/shadow_pipeline.py`
- `trading_core/orchestration/live_pipeline.py`
- `trading_core/orchestration/portfolio_brain.py`
- `trading_core/orchestration/attention_engine.py`
- `trading_core/orchestration/objective_engine.py`
- `trading_core/orchestration/temporal_reasoner.py`

## Review Checklist

Before adding a subsystem, answer:

- Which layer owns this?
- Which contract object carries its output?
- Can it veto execution?
- Can it only advise execution?
- What metric proves it is healthy?
- What event proves it changed state?
- What happens when it is stale?
- What happens when it is slow?
- What happens after restart?
- What is the smallest safe default?

If those answers are unclear, the subsystem is not ready.
