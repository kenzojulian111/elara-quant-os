# Core Platform Contracts

Elara's decision contracts are now treated as platform protocol, not casual implementation classes.

Run:

```powershell
python scripts/check_core_contracts.py
python scripts/check_core_contracts.py --json
python scripts/check_core_contracts.py --fail-on-critical
```

## Frozen Version

`elara.platform.protocol.v1`

## Frozen Contracts

- `DecisionTraceStep`
- `ReasoningComponent`
- `ReasonedDecision`
- `GovernanceDecision`
- `ExecutionPlan`
- `DecisionEnvelope`
- `ObjectiveProfile`
- `StrategyObjectiveBias`
- `TemporalContext`
- `StrategyTemporalBias`
- `PortfolioDecisionPlan`
- `RuntimeModeAssessment`

## Change Rule

A field change in these contracts is a protocol change.

Allowed:

- additive fields with explicit migration note
- new contract version
- compatibility adapter
- replay test update

Not allowed:

- silent field removal
- semantic repurposing of an existing field
- changing approval or execution semantics without a governance note

## Reason

Decision replay, lifecycle events, topology visualization, and governance audits all depend on these shapes staying predictable.
