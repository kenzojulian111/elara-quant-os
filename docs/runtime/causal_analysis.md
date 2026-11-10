# Runtime Causal Analysis

Runtime mode is now analyzable as a causal input to trading decisions.

`RuntimeCausalAnalyzer` turns `RuntimeModeAssessment` into findings:

- cause
- effect
- severity
- confidence
- reasons

Example:

```text
exchange:down -> core_market_access -> high severity
```

## Rule

Runtime health must not be treated as a passive metric. It changes cognition, governance, and compute routing.

If runtime mode changes a decision, the cause should be explainable.
