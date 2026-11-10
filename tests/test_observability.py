import json
import logging
import os
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.json_logging import JsonLogFormatter
from utils.observability import (
    MetricsRegistry,
    current_observability_context,
    profile_block,
    record_exchange_latency,
    record_exchange_order_event,
    record_feed_freshness,
    record_trader_status,
    reset_observability_context,
    set_observability_context,
    REGISTRY,
)


class ObservabilityTests(unittest.TestCase):
    def test_metrics_registry_renders_prometheus_text(self):
        registry = MetricsRegistry()
        registry.counter("elara_test_events_total", service="core")
        registry.gauge("elara_test_health_score", 98.5)
        registry.histogram("elara_test_latency_seconds", 0.12, buckets=(0.1, 0.5), route="/health")

        rendered = registry.render_prometheus()

        self.assertIn("# TYPE elara_test_events_total counter", rendered)
        self.assertIn('elara_test_events_total{service="core"} 1', rendered)
        self.assertIn("elara_test_health_score 98.5", rendered)
        self.assertIn('elara_test_latency_seconds_bucket{le="0.5",route="/health"} 1', rendered)
        self.assertIn('elara_test_latency_seconds_count{route="/health"} 1', rendered)

    def test_observability_context_is_added_to_json_logs(self):
        token = set_observability_context(correlation_id="corr-1", trace_id="trace-1")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="hello",
                args=(),
                exc_info=None,
            )
            payload = json.loads(JsonLogFormatter().format(record))
        finally:
            reset_observability_context(token)

        self.assertEqual("corr-1", payload["correlation_id"])
        self.assertEqual("trace-1", payload["trace_id"])
        self.assertEqual("hello", payload["message"])
        self.assertEqual({}, current_observability_context())

    def test_profile_block_records_success_and_error_samples(self):
        with profile_block("unit_test_block"):
            pass

        with self.assertRaises(RuntimeError):
            with profile_block("unit_test_block"):
                raise RuntimeError("boom")

    def test_trader_status_records_freshness_and_control_state(self):
        record_trader_status(
            {
                "ts": time.time(),
                "is_running": True,
                "control_state": "active",
                "open_positions": 2,
                "equity": 1000.5,
            },
            source="unit_test",
            stale_after_sec=60,
        )

        rendered = REGISTRY.render_prometheus()

        self.assertIn('elara_trader_running{source="unit_test"} 1', rendered)
        self.assertIn('elara_trader_open_positions{source="unit_test"} 2', rendered)
        self.assertIn('elara_trader_control_state{source="unit_test",state="active"} 1', rendered)
        self.assertIn('elara_trader_status_stale{source="unit_test"} 0', rendered)

    def test_feed_freshness_and_exchange_metrics_render(self):
        record_feed_freshness("orderbook", time.time() - 120, symbol="BTC/USDT", stale_after_sec=30)
        record_exchange_latency(
            exchange="binance",
            operation="create_limit_order",
            symbol="BTC/USDT",
            duration_sec=0.25,
            status="ok",
        )
        record_exchange_order_event(exchange="binance", symbol="BTC/USDT", side="buy", status="submitted")

        rendered = REGISTRY.render_prometheus()

        self.assertIn('elara_feed_stale{feed="orderbook",symbol="BTC/USDT"} 1', rendered)
        self.assertIn("elara_exchange_operation_duration_seconds_count", rendered)
        self.assertIn(
            'elara_exchange_order_events_total{exchange="binance",side="buy",status="submitted",symbol="BTC/USDT"} 1',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
