# Complexity Governance

Elara is now large enough that the main risk is not only missing features. The main risk is uncontrolled complexity growth.

This document defines the guardrail used by `ComplexityGovernanceChecker`.

## Purpose

The checker is not a maturity badge. It is an entropy detector.

It answers:

- Is config growth still bounded?
- Are architecture docs keeping up with subsystem growth?
- Are internal protocol contracts present?
- Are replay and event foundations still intact?
- Which roadmap frontier areas are still honestly immature?

## Run

```powershell
python scripts/check_complexity_governance.py
python scripts/check_complexity_governance.py --json
python scripts/check_complexity_governance.py --fail-on-critical
```

`scripts/quality_gate.ps1` runs the checker with `--fail-on-critical`.

Warnings are allowed because Elara intentionally has known future gaps. Critical findings mean a current governance contract broke.

## Domains

### Config Surface

Tracks:

- root `.env.example` key count
- presence of `config/*.env.example`
- config registry and Pydantic Settings adapter

Large root env surface is a warning. The root `.env.example` should stay a small
bootstrap file; detailed defaults belong in `config/*.env.example`. Missing
config governance contracts are critical.

### Documentation Structure

Tracks required architecture and roadmap docs:

- `CORE_IDENTITY.md`
- `ELARA_THEORY.md`
- `docs/architecture_doctrine.md`
- `docs/config_governance.md`
- `docs/cognitive_coherence.md`
- `docs/cognition_mesh.md`
- `docs/decision_flow/decision_topology.md`
- `docs/decision_flow/event_ledger_contract.md`
- `docs/decision_flow/event_sourcing.md`
- `docs/decision_flow/lifecycle_events.md`
- `docs/probabilistic_cognition.md`
- `docs/protocols/core_contracts.md`
- `docs/replay_native_evaluation.md`
- `docs/system/architecture_heatmap.md`
- `docs/system/cognitive_boundaries.md`
- `docs/subsystems/constitution.md`
- `docs/runtime/causal_analysis.md`
- `docs/runtime/state_inspection.md`
- `docs/observability_q1.md`
- `docs/year1_completion.md`
- `docs/year1_q1_q3_execution.md`
- `docs/year1_q4_year2_q4_execution.md`

Missing docs are critical because undocumented complexity becomes operational debt.

### Protocol Formalization

Tracks internal contracts:

- config governance
- trading data contracts
- frozen core platform contracts
- event types
- event stream and ledger
- event-sourced decision writer
- decision lifecycle event projection
- decision topology contract
- replay-native evaluator
- confidence propagation and uncertainty calibration
- runtime causal analysis
- subsystem constitution
- architecture heatmap
- runtime capability negotiation
- service status contracts

It also tracks broad `Any` / loose `dict` usage as a warning, not a failure. The goal is gradual typed-contract migration.

### Replay And Event Maturity

Tracks:

- market replay
- fault injection
- macro shock simulation
- scenario runner

These are critical Year 1 foundations for debugging, replay, and survivability.

### Frontier Gap Tracker

Tracks roadmap areas that are not mature yet:

- probabilistic intelligence
- event-driven maturity
- Rust runtime
- replay and simulation depth
- distributed cognition
- world modeling
- blockchain intelligence

These are warnings by design. They should stay visible without pretending they are complete.

Probabilistic intelligence is promoted only when it has posterior inference, first-class uncertainty profiling, and causal graph propagation. World modeling remains a warning until causal macro graph work is paired with capital-flow simulation and broader cross-market state.

### Cognitive Coherence

`CognitiveCoherenceChecker` is a companion guardrail for explainable decision flow. It verifies that cognitive layers have explicit owners and that final decisions close with a `reasoning` trace carrying probabilistic context.

`DecisionTopology` renders the current cognition graph as Mermaid so operators can review responsibility flow without reading the entire orchestration stack. `decision_lifecycle_events()` projects decision envelopes into deterministic event-sourcing records for replay and dashboards.

`validate_core_contracts()` freezes core decision data shapes as platform protocol. `SubsystemConstitutionChecker` keeps subsystem authority, forbidden actions, state ownership, produced events, consumed events, and failure semantics explicit.

## Interpretation

`critical`:
Current architectural contract is broken.

`warning`:
Known entropy or roadmap maturity gap exists.

`ok`:
No active finding in that domain.

## Rule

Do not add a new subsystem unless at least one of these is true:

- it reduces existing complexity
- it replaces a weaker subsystem
- it adds a missing contract required by the roadmap
- it improves replayability, observability, recoverability, or safety
