# Replay-Native Evaluation

Replay-native evaluation checks whether a decision timeline is explainable and deterministic enough to audit.

`ReplayDecisionEvaluator` currently verifies:

- replay is not empty
- event sequence is monotonic
- required events are present
- terminal events exist
- reasoning is followed only by terminal outcomes

This is not yet full strategy evaluation or black-swan replay. It is the event-level contract required before deeper evaluation can be trusted.

`ReplayDatasetStore` exports Redis Streams events into immutable JSONL datasets
with a manifest hash. Loading fails when event content is modified.

## Use

```python
records = replay_decision_events(stream, trace_id=trace_id)
report = ReplayDecisionEvaluator().evaluate_records(records)
```

```powershell
python scripts/export_redis_stream_replay.py --output runtime/replay/trade-signals.jsonl
```

## Next Maturity Steps

- compare replayed decisions with expected decisions
- attach market snapshot hashes
- attach config snapshot hashes
- evaluate calibration error by regime
- run black-swan and latency scenarios against the same event contract
