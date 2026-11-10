# Decision Topology

This is the current external cognition map for Elara's trading decision path. It exists so the flow can be reviewed without loading the whole codebase into working memory.

Generate the Mermaid source with:

```powershell
python scripts/render_decision_topology.py
```

```mermaid
flowchart LR
  market["MarketState + Indicators"]
  context["ActionContext + Config Snapshot"]
  portfolio["portfolio: portfolio"]
  attention["attention: attention"]
  objective["objective: objective"]
  temporal["temporal: temporal"]
  strategy["strategy: signal"]
  risk["risk: risk_gate"]
  runtime["runtime: runtime_mode"]
  governance["governance: governance"]
  reasoning["reasoning: reasoning"]
  envelope["DecisionEnvelope + Event Ledger"]
  market -->|price + indicators| strategy
  market -->|allocation context| portfolio
  context -->|health + dependency state| runtime
  context -->|operator intent| objective
  portfolio -->|depends on| attention
  portfolio -->|depends on| objective
  runtime -->|depends on| objective
  portfolio -->|depends on| temporal
  strategy -->|depends on| risk
  strategy -->|depends on| runtime
  runtime -->|depends on| governance
  strategy -->|depends on| reasoning
  risk -->|depends on| reasoning
  runtime -->|depends on| reasoning
  governance -->|depends on| reasoning
  reasoning -->|final explainable decision| envelope
  classDef required fill:#e8f7ef,stroke:#1f7a4d,color:#103322;
  class strategy,reasoning required;
  classDef optional fill:#f5f7fb,stroke:#64748b,color:#1f2937;
  class portfolio,attention,objective,temporal,risk,runtime,governance optional;
```

## Reading Rule

The diagram is not a feature map. It is a responsibility map.

If a subsystem changes the final action, execution eligibility, allocation, runtime mode, or governance outcome, it must show up as one of:

- a pipeline trace step
- a reasoning component
- a lifecycle event
- an explicit constraint

Hidden mutation is treated as architectural debt.
