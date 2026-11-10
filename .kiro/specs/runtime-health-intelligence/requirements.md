# Requirements Document

## Introduction

The Runtime Health Intelligence feature enables Elara to detect when its own perception becomes unreliable by monitoring service health, dependency state, data freshness, and connectivity quality. This system provides real-time health scoring and automated governor mode adjustments to prevent trading on degraded or stale data.

Elara is a governed autonomous trading intelligence system with three main components: Core (assistant interface), Trader (execution runtime), and Trading Core (decision engine). The system operates in three governor modes: green (normal operations), yellow (blocks new entries), and red (emergency exits only). Runtime Health Intelligence integrates with the existing observability stack (Prometheus, Grafana, Loki, Tempo) and event bus (Redis with file fallback) to provide continuous health assessment across all trading modes (paper, backtest, live).

## Glossary

- **Health_Monitor**: The subsystem responsible for collecting health metrics and computing health scores
- **Health_Score**: A numeric value (0-100) representing the overall reliability of a monitored component
- **Governor**: The subsystem that controls trading operating modes (green/yellow/red)
- **Dependency**: An external service required for trading operations (Redis, PostgreSQL, exchange APIs)
- **Feed**: A stream of market data from an exchange or data provider
- **Stale_Data**: Market data that exceeds the configured maximum age threshold
- **Heartbeat**: A periodic signal indicating a service or component is operational
- **Degradation**: A measurable decline in service quality or performance
- **Health_Event**: A structured event emitted when health state changes
- **Prometheus**: The metrics collection and storage system
- **Grafana**: The visualization and dashboard system
- **Loki**: The log aggregation system
- **Redis**: The primary event bus and cache layer
- **PostgreSQL**: The primary database for state and audit trails
- **Exchange**: A cryptocurrency or stock trading platform (accessed via ccxt or yfinance)
- **Latency_Threshold**: The maximum acceptable response time for an operation
- **Trading_Mode**: The operational mode (paper, backtest, or live)

## Requirements

### Requirement 1: Service Health Scoring

**User Story:** As an operator, I want Elara to compute health scores for all critical services, so that I can identify degraded components before they cause trading failures.

#### Acceptance Criteria

1. THE Health_Monitor SHALL compute a Health_Score for each monitored service every 10 seconds
2. WHEN a service Health_Score falls below 70, THE Health_Monitor SHALL emit a Health_Event with severity "warning"
3. WHEN a service Health_Score falls below 40, THE Health_Monitor SHALL emit a Health_Event with severity "critical"
4. THE Health_Monitor SHALL compute Health_Score based on response time, error rate, and availability metrics
5. THE Health_Monitor SHALL expose Health_Score values as Prometheus metrics with labels for service name and component type
6. THE Health_Monitor SHALL maintain a 5-minute rolling window of health measurements for score computation

### Requirement 2: Dependency Degradation Detection

**User Story:** As an operator, I want Elara to detect when dependencies degrade, so that trading can be paused before data corruption or execution failures occur.

#### Acceptance Criteria

1. THE Health_Monitor SHALL monitor Redis connection health by measuring command latency and error rate
2. THE Health_Monitor SHALL monitor PostgreSQL connection health by measuring query latency and error rate
3. WHEN Redis command latency exceeds 100ms for 3 consecutive measurements, THE Health_Monitor SHALL mark Redis as degraded
4. WHEN PostgreSQL query latency exceeds 500ms for 3 consecutive measurements, THE Health_Monitor SHALL mark PostgreSQL as degraded
5. WHEN a dependency is marked as degraded, THE Health_Monitor SHALL emit a Health_Event with the dependency name and degradation reason
6. THE Health_Monitor SHALL attempt to reconnect to degraded dependencies every 30 seconds
7. WHEN a degraded dependency recovers, THE Health_Monitor SHALL emit a Health_Event indicating recovery

### Requirement 3: Stale Feed Detection

**User Story:** As an operator, I want Elara to detect stale market data feeds, so that trading decisions are not made on outdated information.

#### Acceptance Criteria

1. THE Health_Monitor SHALL track the timestamp of the most recent data point for each market data feed
2. WHEN a feed has not received data for 60 seconds, THE Health_Monitor SHALL mark the feed as stale
3. WHEN a feed is marked as stale, THE Health_Monitor SHALL emit a Health_Event with the feed identifier and last update timestamp
4. THE Health_Monitor SHALL expose feed staleness duration as a Prometheus metric with labels for feed identifier and symbol
5. WHEN a stale feed receives new data, THE Health_Monitor SHALL mark the feed as active and emit a Health_Event indicating recovery
6. THE Health_Monitor SHALL maintain a staleness threshold configuration per feed type (real-time feeds: 60s, delayed feeds: 300s)

### Requirement 4: Exchange Latency Monitoring

**User Story:** As an operator, I want Elara to monitor exchange API latency, so that execution quality can be assessed and slow exchanges can be avoided.

#### Acceptance Criteria

1. THE Health_Monitor SHALL measure round-trip latency for exchange API requests
2. THE Health_Monitor SHALL compute a 1-minute rolling average of exchange latency per exchange
3. WHEN exchange latency exceeds 2000ms for 5 consecutive requests, THE Health_Monitor SHALL mark the exchange as slow
4. WHEN an exchange is marked as slow, THE Health_Monitor SHALL emit a Health_Event with the exchange name and average latency
5. THE Health_Monitor SHALL expose exchange latency metrics to Prometheus with labels for exchange name and request type
6. THE Health_Monitor SHALL track exchange API error rates and include them in the exchange Health_Score computation

### Requirement 5: Heartbeat Verification

**User Story:** As an operator, I want Elara to verify that all critical components are sending heartbeats, so that silent failures can be detected quickly.

#### Acceptance Criteria

1. THE Health_Monitor SHALL expect a heartbeat from Trading_Core every 30 seconds
2. THE Health_Monitor SHALL expect a heartbeat from Trader runtime every 30 seconds
3. WHEN a component fails to send a heartbeat within 60 seconds of the expected time, THE Health_Monitor SHALL mark the component as unresponsive
4. WHEN a component is marked as unresponsive, THE Health_Monitor SHALL emit a Health_Event with the component name and last heartbeat timestamp
5. THE Health_Monitor SHALL expose heartbeat status as a Prometheus metric with labels for component name
6. WHEN an unresponsive component sends a heartbeat, THE Health_Monitor SHALL mark the component as responsive and emit a Health_Event indicating recovery

### Requirement 6: Governor Mode Integration

**User Story:** As an operator, I want Elara to automatically adjust governor mode based on health state, so that trading is paused when the system is unreliable.

#### Acceptance Criteria

1. WHEN any critical dependency Health_Score falls below 40, THE Health_Monitor SHALL request the Governor to transition to red mode
2. WHEN any feed is stale for more than 120 seconds, THE Health_Monitor SHALL request the Governor to transition to yellow mode
3. WHEN any exchange latency exceeds 5000ms, THE Health_Monitor SHALL request the Governor to transition to yellow mode
4. WHEN any component is unresponsive for more than 90 seconds, THE Health_Monitor SHALL request the Governor to transition to red mode
5. WHEN all health conditions return to normal thresholds for 60 consecutive seconds, THE Health_Monitor SHALL request the Governor to transition to green mode
6. THE Health_Monitor SHALL emit a Health_Event before requesting a Governor mode transition with the reason and target mode

### Requirement 7: Observability Integration

**User Story:** As an operator, I want health intelligence data integrated into Grafana dashboards, so that I can monitor system health in real-time.

#### Acceptance Criteria

1. THE Health_Monitor SHALL expose all Health_Score values as Prometheus gauge metrics
2. THE Health_Monitor SHALL expose dependency latency measurements as Prometheus histogram metrics
3. THE Health_Monitor SHALL expose feed staleness duration as Prometheus gauge metrics
4. THE Health_Monitor SHALL expose exchange latency as Prometheus histogram metrics
5. THE Health_Monitor SHALL expose heartbeat status as Prometheus gauge metrics (1 for responsive, 0 for unresponsive)
6. THE Health_Monitor SHALL emit structured logs to Loki for all Health_Event occurrences with correlation IDs
7. THE Health_Monitor SHALL include component labels in all metrics to enable filtering and aggregation in Grafana

### Requirement 8: Health Event Bus Integration

**User Story:** As a developer, I want health events published to the event bus, so that other components can react to health state changes.

#### Acceptance Criteria

1. THE Health_Monitor SHALL publish Health_Event messages to the Redis event bus on the "health.events" channel
2. WHEN Redis is unavailable, THE Health_Monitor SHALL write Health_Event messages to the file fallback event log
3. THE Health_Monitor SHALL include event timestamp, component name, severity, and reason in each Health_Event message
4. THE Health_Monitor SHALL assign a unique event ID to each Health_Event for deduplication and tracing
5. THE Health_Monitor SHALL publish Health_Event messages with a JSON schema that includes all required fields
6. THE Health_Monitor SHALL maintain event ordering guarantees within a single component's event stream

### Requirement 9: Configuration and Thresholds

**User Story:** As an operator, I want to configure health monitoring thresholds, so that sensitivity can be adjusted for different trading environments.

#### Acceptance Criteria

1. THE Health_Monitor SHALL load threshold configuration from environment variables or configuration files
2. THE Health_Monitor SHALL support configurable thresholds for: feed staleness timeout, exchange latency warning level, dependency latency warning level, heartbeat timeout, Health_Score warning threshold, Health_Score critical threshold
3. WHEN configuration is invalid or missing, THE Health_Monitor SHALL use documented default values and emit a warning log
4. THE Health_Monitor SHALL validate all threshold values at startup and reject invalid configurations
5. THE Health_Monitor SHALL expose current threshold configuration as Prometheus info metrics
6. THE Health_Monitor SHALL support per-Trading_Mode threshold overrides (paper mode may have relaxed thresholds)

### Requirement 10: Health Status API

**User Story:** As an operator, I want to query current health status via API, so that external monitoring systems can integrate with Elara's health intelligence.

#### Acceptance Criteria

1. THE Health_Monitor SHALL expose a GET /health/status endpoint that returns overall system health
2. THE Health_Monitor SHALL expose a GET /health/components endpoint that returns Health_Score for all monitored components
3. THE Health_Monitor SHALL expose a GET /health/dependencies endpoint that returns status for all dependencies
4. THE Health_Monitor SHALL expose a GET /health/feeds endpoint that returns staleness status for all feeds
5. THE Health_Monitor SHALL return HTTP 200 when overall health is acceptable and HTTP 503 when health is critical
6. THE Health_Monitor SHALL include response timestamps and correlation IDs in all API responses
7. THE Health_Monitor SHALL complete all health status API requests within 100ms

### Requirement 11: Parser and Pretty Printer for Health Events

**User Story:** As a developer, I want to parse and format health event messages, so that health events can be reliably serialized and deserialized across the event bus.

#### Acceptance Criteria

1. THE Health_Event_Parser SHALL parse JSON health event messages into Health_Event objects
2. WHEN an invalid health event message is provided, THE Health_Event_Parser SHALL return a descriptive error with the validation failure reason
3. THE Health_Event_Pretty_Printer SHALL format Health_Event objects into valid JSON messages
4. FOR ALL valid Health_Event objects, parsing then printing then parsing SHALL produce an equivalent object (round-trip property)
5. THE Health_Event_Parser SHALL validate that all required fields are present (event_id, timestamp, component, severity, reason)
6. THE Health_Event_Pretty_Printer SHALL produce JSON output that conforms to the documented Health_Event schema

### Requirement 12: Startup Health Check

**User Story:** As an operator, I want Elara to verify system health at startup, so that trading does not begin with degraded dependencies or stale configuration.

#### Acceptance Criteria

1. WHEN Elara starts, THE Health_Monitor SHALL verify connectivity to Redis before marking the system as ready
2. WHEN Elara starts, THE Health_Monitor SHALL verify connectivity to PostgreSQL before marking the system as ready
3. WHEN Elara starts, THE Health_Monitor SHALL verify that at least one exchange API is reachable before marking the system as ready
4. WHEN any startup health check fails, THE Health_Monitor SHALL prevent the Governor from entering green mode
5. WHEN all startup health checks pass, THE Health_Monitor SHALL emit a Health_Event indicating the system is ready
6. THE Health_Monitor SHALL complete all startup health checks within 30 seconds or mark the system as unhealthy
7. THE Health_Monitor SHALL expose startup health check results as Prometheus metrics with pass/fail status per check
