# Cognitive Coherence

Elara's largest scaling risk is not raw feature count. It is losing the ability to explain why a trade decision happened after many cognition layers have touched it.

The cognitive coherence contract keeps the decision path bounded, traceable, and auditable.

## Purpose

The checker answers:

- Does every cognitive layer have an owner module?
- Are layer responsibilities explicit enough to avoid hidden coupling?
- Does a decision envelope carry a trace id and pipeline trace?
- Does the strategy signal appear before final reasoning?
- Does final reasoning close the trace?
- Are probabilistic explainability fields attached to the reasoned decision?
- Are approved entry decisions backed by an execution plan?
- Does governance evaluate after runtime mode context is known?

## Layers

`CognitiveCoherenceChecker` tracks these layer responsibilities:

- `portfolio`: allocation and concentration posture
- `attention`: compute focus under runtime pressure
- `objective`: operator and runtime intent
- `temporal`: short-horizon memory and regime persistence
- `strategy`: raw strategy signal
- `risk`: sizing, drawdown, and execution guardrails
- `runtime`: dependency and health state as decision input
- `governance`: operator and runtime permission gates
- `reasoning`: final explainable probabilistic synthesis

The layer list is intentionally conservative. Optional layers can be absent from a simple decision, but if a layer is traced it should be represented in the final reasoning components when that layer has a component contract.

## Final Reasoning Trace

`DecisionReasoner` now appends a final `reasoning` pipeline step. This step summarizes:

- final action
- approval state
- probability
- uncertainty
- expected utility
- cognitive mode

If a decision is synthesized more than once, older `reasoning` trace steps are replaced so the final trace always closes with the current synthesis.

## Run

```powershell
python scripts/check_cognitive_coherence.py
python scripts/check_cognitive_coherence.py --json
python scripts/check_cognitive_coherence.py --fail-on-critical
```

`scripts/quality_gate.ps1` runs the checker with `--fail-on-critical`.

## Interpretation

`critical`:
The decision cannot be trusted as an explainable trading artifact.

`warning`:
The decision is usable, but its explainability or observability is weaker than the target contract.

`ok`:
The decision or static architecture satisfies the current coherence contract.
