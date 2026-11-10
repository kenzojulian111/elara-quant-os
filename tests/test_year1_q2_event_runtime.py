import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime.capabilities import default_core_capabilities, default_trader_capabilities, negotiate
from trading_core.coordination.event_stream import EventStream, StreamReplayCursor
from trading_core.market.columnar import from_rows, rolling_return_pct
from trading_core.market.streaming import BackpressurePolicy, MarketFanout, MarketStreamNormalizer
from utils.fast_json import backend_name, dumps_text, loads


class Year1Q2EventRuntimeTests(unittest.TestCase):
    def test_fast_json_roundtrip_has_backend(self):
        payload = {"event": "decision", "value": 1}
        encoded = dumps_text(payload)

        self.assertEqual(payload, loads(encoded))
        self.assertIn(backend_name(), {"json", "orjson", "msgspec"})

    def test_event_stream_appends_replays_and_verifies_hash_chain(self):
        with tempfile.TemporaryDirectory(prefix="elara_event_stream_") as tmpdir:
            stream = EventStream(os.path.join(tmpdir, "events.jsonl"), stream="decisions")
            first = stream.append("ORDER_SUBMITTED", {"symbol": "BTC/USDT"})
            second = stream.append("ORDER_ACK", {"order_id": "oid-1"})

            replayed = list(stream.replay(StreamReplayCursor(stream="decisions", after_sequence=first.sequence)))
            ok, reason = stream.verify_hash_chain()

        self.assertEqual([second.event_id], [item.event_id for item in replayed])
        self.assertTrue(ok, reason)
        self.assertEqual(first.event_hash, second.previous_hash)

    def test_event_stream_subscriber_backpressure_drops_oldest(self):
        with tempfile.TemporaryDirectory(prefix="elara_event_stream_bp_") as tmpdir:
            stream = EventStream(os.path.join(tmpdir, "events.jsonl"), stream="market")
            subscriber = stream.subscribe("worker", max_pending=1)
            first = stream.append("tick", {"price": 100})
            second = stream.append("tick", {"price": 101})

            items = subscriber.drain()

        self.assertEqual([second.event_id], [item.event_id for item in items])
        self.assertNotEqual(first.event_id, second.event_id)

    def test_market_fanout_applies_backpressure_and_critical_bypass(self):
        normalizer = MarketStreamNormalizer()
        fanout = MarketFanout(BackpressurePolicy(max_queue_depth=1, drop_policy="reject_new"))
        fanout.subscribe("decision")
        tick = normalizer.normalize({"price": 100}, stream="ticker", symbol="BTC/USDT", event_type="tick")
        book = normalizer.normalize({"price": 101}, stream="ticker", symbol="BTC/USDT", event_type="order_book")

        fanout.publish(tick)
        snapshots = fanout.publish(book)
        drained = fanout.drain("decision")

        self.assertTrue(snapshots["decision"].accepted)
        self.assertEqual("critical_bypass_dropped_oldest", snapshots["decision"].reason)
        self.assertEqual(["order_book"], [item.event_type for item in drained])

    def test_columnar_adapter_computes_returns_without_forcing_polars(self):
        frame = from_rows(
            [
                {"close": "100", "volume": "10"},
                {"close": "110", "volume": "12"},
                {"close": "99", "volume": "11"},
            ],
            prefer="python",
        )

        self.assertEqual("python", frame.backend)
        self.assertEqual(3, frame.height)
        self.assertEqual([0.0, 10.0, -10.0], rolling_return_pct(frame))

    def test_capability_negotiation_reports_missing_required(self):
        core = default_core_capabilities()
        trader = default_trader_capabilities()
        result = negotiate(
            core,
            trader,
            required=("execution_ack_v1", "websocket_v2"),
        )

        self.assertFalse(result.ready)
        self.assertEqual(("websocket_v2",), result.missing_required)
        self.assertIn("metrics_prometheus_v1", result.common)


if __name__ == "__main__":
    unittest.main()
