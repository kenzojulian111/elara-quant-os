# Elara Q1 Observability

Year 1 Q1 is focused on making the runtime observable, profileable, and easier to debug during degraded trading conditions.

## Scope

- Core and trader Prometheus metrics.
- Request correlation IDs and JSON log trace context.
- Runtime health, readiness, and sanity scoring.
- Trader status freshness and exchange order telemetry.
- Optional OpenTelemetry traces exported to Tempo.
- Loki log ingestion for file logs and runtime event JSONL.
- Profiling workflow for CPU, async hot paths, and memory growth.

## Start The Stack

```powershell
docker compose --profile observability up -d
```

Local endpoints:

- Core: http://localhost:5000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Loki: http://localhost:3100
- Tempo: http://localhost:3200

Grafana credentials are controlled by:

```env
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=replace-with-strong-grafana-password
```

The default dashboard is provisioned as `Elara / Elara Runtime`.

## Metrics

Core exposes:

```txt
http://elara-core:5000/metrics
```

Trader exposes:

```txt
http://elara-trader:8768/metrics
```

Important metric families:

- `elara_http_requests_total`
- `elara_http_request_duration_seconds`
- `elara_runtime_sanity_score`
- `elara_runtime_state`
- `elara_trader_status_age_seconds`
- `elara_trader_status_stale`
- `elara_exchange_order_events_total`
- `elara_exchange_operation_duration_seconds`
- `elara_profile_block_duration_seconds`

For Docker Compose, `PROMETHEUS_METRICS_PUBLIC=1` allows Prometheus to scrape over the private Docker network. If metrics are exposed beyond localhost, prefer:

```env
PROMETHEUS_METRICS_PUBLIC=0
PROMETHEUS_METRICS_TOKEN=replace-with-strong-metrics-token
```

## Tracing

OpenTelemetry is optional by default so the runtime can start without Tempo.

Enable it with:

```env
OTEL_ENABLED=1
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
OTEL_EXPORTER_CONSOLE=0
```

Core uses `OTEL_SERVICE_NAME=elara-core`. Trader uses `OTEL_SERVICE_NAME=elara-trader`.

## Logs

Use structured JSON logs for correlation:

```env
LOG_JSON=1
```

Every HTTP request gets:

- `X-Correlation-ID`
- `X-Trace-ID`

Those values are also injected into JSON logs through the observability context.

Promtail scrapes:

- `logs/*.log`
- `runtime/*.jsonl`

## Profiling

Use the helper script:

```powershell
.\scripts\profile_runtime.ps1 -Tool py-spy -ProcessName python -DurationSec 60
```

For Scalene:

```powershell
.\scripts\profile_runtime.ps1 -Tool scalene -Command "apps/elara_core/main.py"
```

For Memray:

```powershell
.\scripts\profile_runtime.ps1 -Tool memray -Command "apps/elara_trader/main.py"
```

Install profilers only in development or a dedicated diagnostic image:

```powershell
python -m pip install py-spy scalene memray
```

## Alert Rules

Prometheus loads `deploy/observability/prometheus/alerts.yml`.

Current Q1 alerts cover:

- Core HTTP 5xx.
- Runtime sanity drop.
- Trader stale status.
- Exchange order failed or rejected.

## Q1 Completion Checklist

- Core liveness, readiness, and deep health endpoints exist.
- Core `/metrics` endpoint exports Prometheus text.
- Trader `/metrics` endpoint exports trader status and execution telemetry.
- Prometheus scrapes core and trader.
- Grafana provisions data sources and runtime dashboard.
- Loki and Promtail are configured for runtime logs.
- Tempo accepts OTLP traces.
- JSON logs carry correlation and trace IDs.
- Profiling script supports py-spy, Scalene, and Memray.
