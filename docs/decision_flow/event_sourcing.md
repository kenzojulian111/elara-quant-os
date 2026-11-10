# Event-Sourced Decisions

Elara now has a side-effect-safe event-sourcing adapter for decision envelopes.

`DecisionEventSourcingWriter` projects a `DecisionEnvelope` into lifecycle events and writes them into `EventStream`.

This gives replay tooling a deterministic event timeline without forcing the live shadow pipeline to change behavior immediately.

## Flow

```text
DecisionEnvelope
-> decision_lifecycle_events()
-> DecisionEventSourcingWriter
-> EventStream(stream="decision_lifecycle")
-> ReplayDecisionEvaluator
```

## Replay Requirements

Every replayed decision timeline should include:

- `SIGNAL_RECEIVED`
- `REASONING_SYNTHESIZED`
- at least one terminal event such as `EXECUTION_APPROVED`, `DECISION_BLOCKED`, `GOVERNANCE_BLOCKED`, or `RISK_SUPPRESSED`

## Rule

Replay-native cognition means final state is not enough. The event order must explain how the system got there.
