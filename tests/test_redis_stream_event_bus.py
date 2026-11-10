from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from config.governance import build_config_registry
from trading_core.coordination import RedisStreamsExternalBusAdapter
from trading_core.evaluation import ReplayDatasetStore


class _FakeRedisStreams:
    def __init__(self) -> None:
        self.streams = {}
        self.groups = set()
        self.pending = {}
        self.sequence = 0

    def xadd(self, key, fields, maxlen=None, approximate=True):
        _ = approximate
        self.sequence += 1
        stream_id = f"{self.sequence}-0"
        rows = self.streams.setdefault(key, [])
        rows.append((stream_id, fields))
        if maxlen:
            del rows[:-int(maxlen)]
        return stream_id

    def xrange(self, key, min="-", max="+", count=None):
        _ = max
        rows = list(self.streams.get(key, ()))
        if str(min).startswith("("):
            offset = str(min)[1:]
            rows = [row for row in rows if row[0] != offset]
        return rows[:count] if count else rows

    def xgroup_create(self, key, group, id="0-0", mkstream=True):
        _ = id
        if mkstream:
            self.streams.setdefault(key, [])
        marker = (key, group)
        if marker in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(marker)

    def xreadgroup(self, group, consumer, streams, count=100, block=0):
        _ = block
        key, offset = next(iter(streams.items()))
        marker = (key, group, consumer)
        if offset == "0":
            rows = list(self.pending.get(marker, ()))
        else:
            already_pending = {
                stream_id
                for pending_marker, pending_rows in self.pending.items()
                if pending_marker[:2] == (key, group)
                for stream_id, _ in pending_rows
            }
            rows = [row for row in self.streams.get(key, ()) if row[0] not in already_pending]
            self.pending.setdefault(marker, []).extend(rows[:count])
        return [(key, rows[:count])] if rows else []

    def xack(self, key, group, *stream_ids):
        removed = 0
        for marker, rows in list(self.pending.items()):
            if marker[:2] != (key, group):
                continue
            kept = [row for row in rows if row[0] not in set(stream_ids)]
            removed += len(rows) - len(kept)
            self.pending[marker] = kept
        return removed

    def xautoclaim(self, key, group, consumer, min_idle_time, start_id="0-0", count=100):
        _ = min_idle_time, start_id
        claimed = []
        for marker, rows in list(self.pending.items()):
            if marker[:2] != (key, group) or marker[2] == consumer:
                continue
            claimed.extend(rows[:count])
            self.pending[marker] = rows[count:]
        self.pending.setdefault((key, group, consumer), []).extend(claimed)
        return ("0-0", claimed, ())

    def xdel(self, key, *stream_ids):
        rows = self.streams.get(key, [])
        kept = [row for row in rows if row[0] not in set(stream_ids)]
        self.streams[key] = kept
        return len(rows) - len(kept)


class RedisStreamEventBusTests(unittest.TestCase):
    def test_publish_replay_consume_redelivery_and_ack(self) -> None:
        client = _FakeRedisStreams()
        bus = RedisStreamsExternalBusAdapter(client, stream_key="elara:test:events", maxlen=100)
        published = bus.publish("trade.signal.v1", {"cmd": "stop"}, source="test")

        replay = bus.replay()
        first_delivery = bus.consume(group="trader", consumer="worker", block_ms=0)
        redelivery = bus.consume(group="trader", consumer="worker", block_ms=0, include_pending=True)
        acknowledged = bus.acknowledge(group="trader", stream_ids=(published.stream_id,))

        self.assertEqual(replay[0].payload["cmd"], "stop")
        self.assertEqual(first_delivery[0].event_id, published.event_id)
        self.assertEqual(redelivery[0].stream_id, published.stream_id)
        self.assertEqual(acknowledged, 1)
        self.assertEqual(bus.consume(group="trader", consumer="worker", block_ms=0, include_pending=True), ())

    def test_registry_rejects_required_stream_bus_when_disabled(self) -> None:
        registry = build_config_registry(
            {
                "REDIS_EVENT_BUS_REQUIRED": True,
                "REDIS_STREAM_EVENT_BUS_REQUIRED": True,
                "REDIS_STREAM_EVENT_BUS_ENABLED": False,
            }
        )

        self.assertIn("runtime.stream_bus_required_but_disabled", registry.validate().errors)

    def test_stale_pending_event_is_claimed_by_new_consumer(self) -> None:
        client = _FakeRedisStreams()
        bus = RedisStreamsExternalBusAdapter(client, stream_key="elara:test:events", maxlen=100)
        published = bus.publish("trade.signal.v1", {"cmd": "stop"})
        bus.consume(group="trader", consumer="old-worker", block_ms=0)

        claimed = bus.claim_stale(group="trader", consumer="new-worker", min_idle_ms=0)

        self.assertEqual(claimed[0].stream_id, published.stream_id)
        self.assertEqual(bus.acknowledge(group="trader", stream_ids=(published.stream_id,)), 1)

    def test_replay_dataset_hash_rejects_tampering(self) -> None:
        client = _FakeRedisStreams()
        bus = RedisStreamsExternalBusAdapter(client, stream_key="elara:test:events", maxlen=100)
        bus.publish("trade.signal.v1", {"cmd": "stop"})
        with tempfile.TemporaryDirectory(prefix="elara_replay_dataset_") as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            store = ReplayDatasetStore()
            manifest = store.write(path, bus.replay(), dataset_id="dataset-test")
            loaded = store.load_verified(path)
            self.assertEqual(manifest.dataset_id, "dataset-test")
            self.assertEqual(loaded.events[0].payload["cmd"], "stop")

            path.write_text(path.read_text(encoding="utf-8").replace('"stop"', '"resume"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replay_dataset_hash_mismatch"):
                store.load_verified(path)


if __name__ == "__main__":
    unittest.main()
