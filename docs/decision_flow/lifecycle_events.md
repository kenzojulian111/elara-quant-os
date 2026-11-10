# Decision Lifecycle Events

Elara now exposes a deterministic projection from `DecisionEnvelope` into lifecycle events.

The projection is intentionally side-effect free. It does not change ledger behavior by itself; it defines the event-sourcing contract that the ledger, replay tooling, and dashboards can adopt.

## Event Examples

- `SIGNAL_RECEIVED`
- `ATTENTION_SELECTED`
- `OBJECTIVE_SELECTED`
- `TEMPORAL_CONTEXT_APPLIED`
- `CANDIDATE_BUILT`
- `RISK_APPROVED`
- `RISK_SUPPRESSED`
- `RUNTIME_MODE_ASSESSED`
- `GOVERNANCE_APPROVED`
- `GOVERNANCE_BLOCKED`
- `DECISION_APPROVED`
- `DECISION_BLOCKED`
- `REASONING_SYNTHESIZED`
- `EXECUTION_APPROVED`

## Rule

Every event must keep:

- `trace_id`
- `strategy_id`
- `symbol`
- `sequence`
- `step`
- `status`
- `reasons`

This keeps replay and explanation anchored to the same timeline.
