# Runtime State Inspection

Runtime state should be inspectable from one mental model, even when the data comes from several places.

## Minimum Inspection Surface

An operator should be able to answer:

- What runtime mode is active?
- Which dependencies are stale, degraded, or down?
- Are new entries allowed?
- Are exits still allowed?
- Which governance mode is active?
- Which strategy or symbol is blocked?
- What config hash or profile is active?
- What was the latest decision trace id?

## State Groups

Core:

- process liveness
- database readiness
- Redis/event bus status
- internal scheduler health

Trading:

- open positions
- pending execution plans
- last trade decision per symbol
- idempotency cache status

Governance:

- operator permission
- runtime mode
- kill switch state
- dependency impacts

Observability:

- latest trace id
- latest decision lifecycle events
- queue depth
- stale heartbeat age

## Rule

If state can change behavior, it must be visible through health, readiness, trace, event, or audit output.
