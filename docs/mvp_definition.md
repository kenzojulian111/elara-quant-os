# Elara MVP Definition and Scope Freeze

## Product Definition

> **Elara MVP is a governed trading decision system that accepts reliable market
> data, produces a trading decision, passes it through risk and governance,
> executes it in paper mode, and stores an explainable audit trail that can be
> replayed.**

This document is the source of truth for MVP scope. A feature is not MVP work
unless it directly closes one of the gates below or is required to operate a
gate safely. The core trading path must remain understandable and test-covered;
experimental systems are opt-in and must not block it.

## Required Decision Path

```text
Market Data
  -> StrategyDecision
  -> TradeCandidate
  -> RiskDecision
  -> GovernanceDecision
  -> ReasonedDecision
  -> ExecutionPlan
  -> Paper Execution
  -> Audit + Replay
```

`DecisionEnvelope` transports the decision through this path. Every decision,
including holds and vetoes, has a stable `trace_id`; every execution also needs
a stable persistent execution/idempotency key.

An `ExecutionPlan` is executable only after both governance and reasoning have
approved it. An earlier, risk-approved order shape is a *proposed plan*, not a
plan that may reach an adapter.

## Definition of Done

Elara may be called MVP only when all items pass in a reproducible local paper
run and their tests pass.

- [ ] Market data is normalized, validated for freshness, and passed into the decision pipeline.
- [ ] One explicitly selected strategy can produce a decision from that data.
- [ ] Risk evaluates every actionable candidate and blocks unsafe candidates.
- [ ] Governance evaluates every decision and vetoes unsafe entries while preserving permitted exits.
- [ ] A `ReasonedDecision` explains the final result, including every veto/constraint.
- [ ] An approved decision creates an `ExecutionPlan` and paper execution records a receipt.
- [ ] Execution is idempotent across duplicate delivery and process restart, using a persistent key.
- [ ] Every decision and execution has an ID that links market input, decision, and receipt.
- [ ] Audit events are durable, ordered, and integrity-verified.
- [ ] A decision can be replayed from its audit data and its lifecycle can be verified.
- [ ] The web UI shows recent decisions, their status, reasons, IDs, and execution result.
- [ ] Automated tests cover the complete approved path plus risk, governance, data-freshness, execution, and persistence failures.

## Current Classification

### In MVP

- `trading_core/data_contracts.py`: market, decision, risk, governance, reasoned-decision, and execution contracts.
- `trading_core/market/streaming.py` and replay loaders: normalized market-state inputs.
- One strategy from `trading_core/strategy/` (choose and configure one for the first paper run).
- `trading_core/risk/risk_engine.py` and `trading_core/orchestration/decision_governor.py`.
- `trading_core/orchestration/decision_core.py`, `decision_reasoner.py`, and a deliberately slim orchestration path.
- `trading_core/coordination/{action_context,idempotency,event_ledger,event_stream,event_sourcing}.py`.
- Paper execution adapter, audit/replay read model, and primary web decision/status view.
- Tests that prove the gates above.

### Supports MVP

- Redis/PostgreSQL transport and persistence, runtime health, config profiles, structured logging, metrics, and deployment files.
- `portfolio_brain.py` only as the minimal strategy selector/allocation dependency required by the selected strategy.
- `market/replay_engine.py`, `evaluation/replay_evaluator.py`, and `coordination/snapshot_replay.py` as verification tools.
- Authentication and the web shell only to the extent needed to expose the operator decision/status view.

### Deferred Until After MVP

- Multi-strategy portfolio intelligence, attention, objective, temporal, meta-cognition, and advanced cognition layers.
- Research automation, ML training, strategy generation/libraries, simulations beyond the MVP replay fixture, and world/causal/narrative models.
- Blockchain/DeFi, institutional platform, Rust gateway, advanced smart routing, and live exchange execution.
- Voice, Telegram, IoT/device control, life management, generic LLM assistant/skills, knowledge graph, and presence/home automation.
- Advanced dashboards, long-horizon roadmap readiness work, and optional observability integrations not needed for the paper run.

Deferred modules may stay in the repository, but they must be disabled by default and must not become a required dependency of the MVP path.

## Gap Register (as of 2026-08-14)

| Gate | Evidence present | Remaining MVP work |
| --- | --- | --- |
| Contracts, strategy, risk, governance, explanation | Implemented and unit-tested | Make the selected strategy/config explicit in the MVP run. |
| Market data | Normalizer and replay engines exist | Add the production-like paper-run ingress, validation, and freshness failure path. |
| Correct ordering | `PaperTradingPipeline` clears any proposed plan before governance/reasoning | Add an end-to-end assertion that blocked decisions never expose a plan. |
| Paper execution | `PaperExecutionAdapter` records deterministic `paper_filled` receipts | Connect the paper pipeline to the selected market-data ingress. |
| Idempotency | Paper receipts are keyed by durable `execution_id` | Add the paper-run duplicate-delivery and restart acceptance test. |
| Audit and replay | Hash-chained ledger stores market input, decision, risk, governance, and receipt; `PaperAuditRepository` replays by trace ID | Bind the replay result to the web decision view. |
| Web UI | `/api/trading/decisions` uses paper audit rows when present; replay is available at `/api/trading/decisions/<trace_id>/replay` | Add trace/execution identifiers to the decision-table rendering. |
| End-to-end tests | Strong component tests exist | Add one small happy-path test and the required failure-path matrix for the actual paper runner. |

## Frozen Sequence

1. Create one minimal, runnable paper-decision service/command using one strategy and one market-data source or deterministic fixture.
2. Make final approval precede executable-plan creation; add durable paper execution and persistent idempotency.
3. Persist the linked lifecycle, expose replay by decision ID, and bind the web view to it.
4. Add the gate tests and a documented acceptance command.
5. Freeze MVP after every gate is green. Only then evaluate shadow/live progression and deferred features.

## Change Control

Any pull request that adds a feature must state one of: `MVP`, `supports MVP`,
or `deferred`. Deferred work cannot change the default MVP runtime dependency
graph. A new subsystem needs an explicit reason it closes an MVP gate; otherwise
it stays deferred.
