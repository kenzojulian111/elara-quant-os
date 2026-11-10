# Core Identity

Elara is a trading decision platform.

The system exists to:

- turn market data into governed trading decisions
- keep risk controls, runtime mode, and operator state explicit
- preserve safe exits when the system is degraded or dependencies are failing

The system does not optimize for being a general assistant, IoT brain, or life manager first.

The architecture doctrine for future work lives in `docs/architecture_doctrine.md`. If a feature conflicts with that doctrine, the feature must be reduced, delayed, or isolated behind an explicit experimental boundary.

The MVP scope and acceptance gates live in `docs/mvp_definition.md`. That
document is the release contract: Elara is not an MVP until every gate passes.

## Scope Tiers

`CORE`

- `trading_core/`
- decision engine and orchestration
- risk engine and governor
- trader gateway and status contracts

`SUPPORT`

- PostgreSQL state and audit trail
- Redis event bus and command transport
- screeners and market data ingestion
- primary operator interface: web

`EXPERIMENTAL`

- voice interface
- presence service
- IoT and MQTT device control
- markdown skill routing and feedback routing
- secondary assistant-style knowledge surfaces

## Activation Rule

- `CORE` must remain understandable and test-covered.
- `SUPPORT` may degrade runtime mode, but should not silently rewrite core behavior.
- `EXPERIMENTAL` is opt-in only and must stay off by default.
- Experimental failure must not block core trading decision paths.

## Operating Rule

When priorities conflict, Elara chooses:

1. predictable trading behavior
2. safe failure handling
3. lower blast radius
4. fewer active subsystems

## Architectural Direction

Elara grows by strengthening:

- governance philosophy
- separation of responsibility
- runtime thinking
- operational safety
- observability awareness
- decision abstraction
- explainable decision flow
- layered orchestration

Elara does not grow by adding unbounded subsystems whose behavior cannot be observed, explained, or safely degraded.

