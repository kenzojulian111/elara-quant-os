# Year 1 Q1-Q3 Execution Baseline

This document records the repo-ready foundation for Year 1 Quarter 1 through Quarter 3 of `roadmap.codex`.

## Q1: Infrastructure Stability And Observability

Goal:

> Create strong operational visibility and runtime reliability.

Implemented baseline:

- Core `/health/live`, `/health/ready`, `/health/deep`.
- Core and trader Prometheus metrics.
- Grafana, Prometheus, Loki, Promtail, and Tempo compose profile.
- Structured JSON log context with correlation and trace IDs.
- Runtime health scoring, dependency readiness, trader stale status metrics.
- Profiling helper for py-spy, Scalene, and Memray.

Primary anchors:

- `docs/observability_q1.md`
- `deploy/observability/`
- `utils/observability.py`
- `runtime/interfaces/web_factory.py`
- `skills/advanced/trading/ipc_server.py`

## Q2: Event-Driven Architecture And Low Latency Runtime

Goal:

> Transform Elara from semi-service orchestration into a more event-driven, replayable, low-latency-ready system.

Implemented baseline:

- Append-only event stream with replay cursors and hash-chain verification.
- Subscriber backpressure policy with bounded in-memory queues.
- Fast JSON adapter with optional `orjson` / `msgspec` acceleration.
- Market stream normalizer and fanout with drop policy and critical bypass.
- Columnar data adapter that prefers Polars, falls back to pandas, then pure Python.
- Runtime capability negotiation contracts for core/trader compatibility.

Primary anchors:

- `trading_core/coordination/event_stream.py`
- `trading_core/coordination/event_ledger.py`
- `trading_core/market/streaming.py`
- `trading_core/market/columnar.py`
- `utils/fast_json.py`
- `runtime/capabilities.py`

Rust runtime note:

The roadmap says Python should decide and Rust should execute low-latency ingestion/execution later. This baseline does not fake a Rust rewrite. It creates the contracts Rust will need: event records, streaming normalization, replay cursors, capability negotiation, and serialization boundaries.

## Q3: Decision Intelligence And Regime Awareness

Goal:

> Improve decision quality, adaptability, and explainability without adding hype-driven complexity.

Implemented baseline:

- Regime detector for trend/range/transition, volatility, liquidity, and confidence.
- Probabilistic decision assessment with probability, uncertainty, and expected utility.
- Bayesian evidence fusion now turns reasoning components into a posterior with variance, effective sample size, and conflict tracking.
- Uncertainty is now represented as a profile with epistemic, aleatoric, conflict, data-quality, and component-level contributors.
- Causal graph inference propagates macro/liquidity/risk-sentiment evidence across explicit causal edges.
- Decision reasoner now enriches `ReasonedDecision` with probability fields.
- Cognitive runtime modes: balanced, defensive, opportunistic, crisis, recovery.
- Decision explanation report for forensic reconstruction.
- Contextual risk sizing for uncertainty, volatility, and liquidity.

Primary anchors:

- `trading_core/intelligence/regime.py`
- `trading_core/intelligence/bayesian.py`
- `trading_core/intelligence/uncertainty.py`
- `trading_core/intelligence/causal_graph.py`
- `trading_core/intelligence/probabilistic.py`
- `trading_core/intelligence/cognitive_modes.py`
- `trading_core/intelligence/explainability.py`
- `trading_core/orchestration/decision_reasoner.py`
- `trading_core/risk/risk_engine.py`

## Completion Meaning

This baseline means Q1-Q3 roadmap goals now have concrete code, docs, tests, and interfaces.

It does not mean Elara has production Kafka/NATS, Rust execution, full Bayesian inference, or industrial ML yet. Those should be added only after these contracts survive simulation, replay, and operational testing.

The current safe next step is:

1. Run shadow decisions through the event stream.
2. Store decision explanations for replay.
3. Feed regime assessment into portfolio and governance layers.
4. Promote capability negotiation into core/trader startup readiness.
5. Add external event bus adapters behind the same event stream contract.
