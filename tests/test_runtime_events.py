import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.runtime_events import RuntimeEventStore


class RuntimeEventStoreTests(unittest.TestCase):
    def test_append_only_events_are_hash_chained(self):
        with tempfile.TemporaryDirectory(prefix="elara_runtime_events_") as tmpdir:
            path = os.path.join(tmpdir, "events.jsonl")
            clock = {"now": 100.0}
            store = RuntimeEventStore(path, time_fn=lambda: clock["now"])

            first = store.append("runtime_degraded", source="test", payload={"reason": "redis_down"})
            clock["now"] = 101.0
            second = store.append(
                "order_submitted",
                source="test",
                payload={"symbol": "BTC/USDT"},
                execution_id="exec-1",
            )

            self.assertEqual("", first.previous_hash)
            self.assertEqual(first.event_hash, second.previous_hash)
            self.assertEqual({"ok": True, "count": 2, "errors": ()}, store.verify_chain())
            self.assertEqual(["RUNTIME_DEGRADED", "ORDER_SUBMITTED"], [event.event_type for event in store.read_tail(2)])

    def test_verify_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="elara_runtime_events_tamper_") as tmpdir:
            path = os.path.join(tmpdir, "events.jsonl")
            store = RuntimeEventStore(path)
            store.append("runtime_degraded", source="test", payload={"reason": "redis_down"})

            with open(path, "r", encoding="utf-8") as f:
                row = json.loads(f.readline())
            row["payload"]["reason"] = "changed"
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")

            verification = store.verify_chain()
            self.assertFalse(verification["ok"])
            self.assertTrue(any("event_hash_mismatch" in err for err in verification["errors"]))


if __name__ == "__main__":
    unittest.main()
