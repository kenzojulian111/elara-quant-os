import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.health import DependencyCircuitBreaker, assess_runtime_mode
from utils.health import record_db_lock, record_db_write_lock_contention, snapshot


class HealthMetricsTests(unittest.TestCase):
    def test_health_snapshot_exposes_db_lock_and_write_contention_metrics(self):
        before = snapshot()
        before_db_lock = int(before.get("db_lock_count") or 0)
        before_contention = int(before.get("db_write_lock_contention_count") or 0)

        record_db_lock()
        record_db_write_lock_contention()

        after = snapshot()
        after_db_lock = int(after.get("db_lock_count") or 0)
        after_contention = int(after.get("db_write_lock_contention_count") or 0)

        self.assertGreaterEqual(after_db_lock, before_db_lock + 1)
        self.assertGreaterEqual(after_contention, before_contention + 1)
        self.assertIn("db_lock_rate_per_min", after)
        self.assertIn("db_write_lock_contention_rate_per_min", after)
        self.assertGreaterEqual(float(after.get("db_lock_rate_per_min") or 0.0), 0.0)
        self.assertGreaterEqual(float(after.get("db_write_lock_contention_rate_per_min") or 0.0), 0.0)

    def test_runtime_mode_goes_safe_when_critical_dependency_is_down(self):
        assessment = assess_runtime_mode(
            dependency_states={"exchange": "down"},
            metrics={"loop_lag_ms": 10.0},
            system_health_score=90.0,
        )

        self.assertEqual("safe", assessment.mode)
        self.assertFalse(assessment.allow_new_entries)
        self.assertIn("core_dependency_down=exchange:down:market_access", assessment.reasons)

    def test_runtime_mode_support_dependency_redis_down_is_degraded(self):
        assessment = assess_runtime_mode(
            dependency_states={"redis": "down"},
            metrics={"loop_lag_ms": 10.0},
            system_health_score=90.0,
        )

        self.assertEqual("degraded", assessment.mode)
        self.assertFalse(assessment.allow_new_entries)
        self.assertIn("support_dependency_down=redis:down:event_bus", assessment.reasons)

    def test_runtime_mode_experimental_dependency_isolated_from_core(self):
        assessment = assess_runtime_mode(
            dependency_states={"voice": "down", "exchange": "up"},
            metrics={"loop_lag_ms": 10.0},
            system_health_score=95.0,
        )

        self.assertEqual("full", assessment.mode)
        self.assertIn("experimental_dependency_isolated=voice:down:operator_voice", assessment.reasons)

    def test_runtime_mode_goes_degraded_on_soft_failure(self):
        assessment = assess_runtime_mode(
            dependency_states={"exchange": "slow"},
            metrics={"loop_lag_ms": 300.0},
            system_health_score=80.0,
        )

        self.assertEqual("degraded", assessment.mode)
        self.assertFalse(assessment.allow_new_entries)
        self.assertTrue(assessment.allow_background_tasks)

    def test_dependency_circuit_breaker_holds_failed_dependency_open_during_cooldown(self):
        clock = {"now": 100.0}
        breaker = DependencyCircuitBreaker(
            fail_threshold=2,
            cooldown_sec=30.0,
            time_fn=lambda: clock["now"],
        )

        first = breaker.observe({"exchange": "down"})
        self.assertEqual("down", first.states["exchange"])
        self.assertEqual("closed", first.circuit_states["exchange"])
        self.assertIn("dependency_failure_streak=exchange:1", first.reasons)

        second = breaker.observe({"exchange": "down"})
        self.assertEqual("failed", second.states["exchange"])
        self.assertEqual("open", second.circuit_states["exchange"])
        self.assertIn("dependency_circuit_open=exchange:down", second.reasons)

        clock["now"] = 110.0
        third = breaker.observe({"exchange": "up"})
        self.assertEqual("failed", third.states["exchange"])
        self.assertEqual("open", third.circuit_states["exchange"])
        self.assertIn("dependency_circuit_open=exchange:up", third.reasons)

        clock["now"] = 140.0
        recovered = breaker.observe({"exchange": "up"})
        self.assertEqual("up", recovered.states["exchange"])
        self.assertEqual("half_open", recovered.circuit_states["exchange"])

        settled = breaker.observe({"exchange": "up"})
        self.assertEqual("up", settled.states["exchange"])
        self.assertEqual("closed", settled.circuit_states["exchange"])

    def test_runtime_mode_goes_safe_on_db_contention_spike(self):
        assessment = assess_runtime_mode(
            dependency_states={"exchange": "up"},
            metrics={"db_write_lock_contention_rate_per_min": 15.0, "loop_lag_ms": 20.0},
            system_health_score=95.0,
        )

        self.assertEqual("safe", assessment.mode)
        self.assertFalse(assessment.allow_new_entries)
        self.assertIn("db_contention_safe=15.00", assessment.reasons)


if __name__ == "__main__":
    unittest.main()

