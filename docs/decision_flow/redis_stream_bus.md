# Redis Streams Durable Event Bus

Elara uses Redis Streams as the first deployed durable inter-service event bus.
Redis Pub/Sub remains a low-latency compatibility path, but it is not the source
of truth when Streams are enabled.

Production Streams mode requires Redis 7.x because stale pending-event recovery
uses `XAUTOCLAIM`.

## Signal Flow

```text
Core life signal
-> XADD elara:streams:trade_signal
-> best-effort Pub/Sub notification
-> Trader consumer group
-> durable signal decode
-> idempotent command enqueue
-> XACK after enqueue succeeds
```

## Runtime Contract

- `REDIS_STREAM_EVENT_BUS_ENABLED=1` enables Redis Streams.
- `REDIS_STREAM_EVENT_BUS_REQUIRED=1` blocks unsafe startup combinations.
- `REDIS_STREAM_EVENT_KEY` defines the stream namespace.
- `REDIS_STREAM_EVENT_MAXLEN` bounds retention.
- `REDIS_STREAM_CLAIM_IDLE_MS` controls stale pending-event recovery.
- Redis readiness checks stream append, replay, and delete.
- Docker persists Redis AOF under the `redis-data` volume.

## Remaining Deployment Work

- Configure Redis replication, persistence backups, and recovery drills.
- Define retention from measured event volume.
- Move to NATS JetStream or Kafka only when scale requires another broker.
