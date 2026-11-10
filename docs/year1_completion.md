# Year 1 Completion Gate

This document narrows the roadmap focus to Year 1 only.

Year 1 target:

> Build a stable, observable, modular, event-driven, scalable trading intelligence architecture.

Not included in Year 1 completion:

- fully deployed Kafka/NATS/Redpanda
- Rust execution runtime
- unrestricted self-improvement
- heavyweight autonomous research systems
- Year 2 knowledge graph / distributed cognition deployment

Those remain promotion paths after the Year 1 base proves reliable.

## Q1: Infrastructure Stability And Observability

Goal:

> Create strong operational visibility and runtime reliability.

Definition of done:

- core liveness, readiness, and deep health endpoints exist
- core and trader expose Prometheus metrics
- structured logs carry correlation and trace IDs
- runtime health reports dependency, stale status, and sanity state
- profiling workflow exists for CPU, async, and memory bottlenecks
- deployable Prometheus, Grafana, Loki, Promtail, and Tempo configs exist

Primary status:

- `docs/observability_q1.md`
- `deploy/observability/`
- `utils/observability.py`

## Q2: Event-Driven Architecture And Low-Latency Runtime Foundations

Goal:

> Transform Elara from semi-service orchestration into an event-driven, replayable, low-latency-ready system.

Definition of done:

- append-only event stream exists
- replay cursor exists
- event hash-chain verification exists
- market stream normalization exists
- bounded queue/backpressure policy exists
- serialization boundary exists
- runtime capability negotiation exists

Primary status:

- `trading_core/coordination/event_stream.py`
- `trading_core/market/streaming.py`
- `trading_core/market/columnar.py`
- `utils/fast_json.py`
- `runtime/capabilities.py`

## Q3: Decision Intelligence And Regime Awareness

Goal:

> Improve decision quality and adaptability.

Definition of done:

- regime detection exists
- probabilistic decision assessment exists
- Bayesian evidence fusion tracks posterior probability, variance, uncertainty, and component conflict
- uncertainty is represented as a first-class profile rather than a single opaque scalar
- causal graph inference exists for explicit probabilistic propagation across macro and market assumptions
- reasoned decisions include probability, uncertainty, and expected utility
- cognitive runtime modes exist
- explainable decision report exists
- contextual risk sizing accounts for uncertainty, volatility, and liquidity

Primary status:

- `trading_core/intelligence/regime.py`
- `trading_core/intelligence/bayesian.py`
- `trading_core/intelligence/uncertainty.py`
- `trading_core/intelligence/causal_graph.py`
- `trading_core/world_model/causal_macro_graph.py`
- `trading_core/intelligence/probabilistic.py`
- `trading_core/intelligence/cognitive_modes.py`
- `trading_core/intelligence/explainability.py`
- `trading_core/orchestration/decision_reasoner.py`
- `trading_core/risk/risk_engine.py`

## Q4: Memory, Simulation, And Lightweight Machine Learning

Goal:

> Create adaptive learning capability without unrestricted self-modification.

Definition of done:

- operational memory exists with temporal decay and confidence weighting
- simulation fault injection exists
- macro shock projection exists
- lightweight ML abstraction exists without forcing heavy dependencies
- portfolio intelligence detects concentration and correlation clusters

Primary status:

- `trading_core/memory/operational_memory.py`
- `trading_core/simulation/fault_injection.py`
- `trading_core/simulation/macro.py`
- `trading_core/ml/lightweight.py`
- `trading_core/portfolio/intelligence.py`

## Readiness Check

Use the code-level gate:

```python
from trading_core.roadmap import Year1ReadinessChecker

report = Year1ReadinessChecker(".").build_report()
assert report.ready, report.to_dict()
```

Related tests:

- `tests/test_year1_readiness.py`
- `tests/test_year1_q2_event_runtime.py`
- `tests/test_year1_q3_decision_intelligence.py`
- `tests/test_year1_q4_memory_simulation.py`
