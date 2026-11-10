# Event Ledger Contract

`EventLedger` is the lightweight append-only ledger used by shadow trading and execution adapters.

New records include:

- `schema_version`
- `sequence`
- `event_id`
- `event_type`
- `timestamp`
- `source`
- `trace_id`
- `correlation_id`
- `previous_hash`
- `event_hash`
- `payload`

## Hash Chain

Each new ledger record includes the previous event hash and a stable hash of the current record with `event_hash` cleared.

This does not replace `RuntimeEventStore` or `EventStream`. It hardens the legacy ledger path so decision and execution records can be audited without losing compatibility with existing readers.

## Verification

Use:

```python
ok, reason = ledger.verify_hash_chain()
```

`ok=False` indicates schema mismatch, previous hash mismatch, or payload tampering.
