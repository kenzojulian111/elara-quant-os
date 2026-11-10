import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.runtime_state import RuntimeStateMachine
from utils.health import assess_runtime_mode


class RuntimeStateMachineTests(unittest.TestCase):
    def test_runtime_state_escalates_and_recovers_through_explicit_states(self):
        clock = {"now": 100.0}
        machine = RuntimeStateMachine(time_fn=lambda: clock["now"])

        degraded = machine.apply_assessment(
            assess_runtime_mode(dependency_states={"redis": "down"}, metrics={"loop_lag_ms": 0.0})
        )
        self.assertEqual("full", degraded.from_state)
        self.assertEqual("degraded", degraded.to_state)

        safe = machine.apply_assessment(
            assess_runtime_mode(dependency_states={"exchange": "down"}, metrics={"loop_lag_ms": 0.0})
        )
        self.assertEqual("degraded", safe.from_state)
        self.assertEqual("safe", safe.to_state)

        clock["now"] = 130.0
        recovery = machine.apply_assessment(
            assess_runtime_mode(dependency_states={"exchange": "up", "redis": "up"}, metrics={"loop_lag_ms": 0.0})
        )
        self.assertEqual("safe", recovery.from_state)
        self.assertEqual("recovery", recovery.to_state)

        clock["now"] = 160.0
        full = machine.apply_assessment(
            assess_runtime_mode(dependency_states={"exchange": "up", "redis": "up"}, metrics={"loop_lag_ms": 0.0})
        )
        self.assertEqual("recovery", full.from_state)
        self.assertEqual("full", full.to_state)
        self.assertEqual("full", machine.snapshot().state)

    def test_invalid_direct_safe_to_full_transition_is_rejected(self):
        machine = RuntimeStateMachine()
        machine.transition_to("safe", reason="critical_dependency_down")

        with self.assertRaisesRegex(ValueError, "Invalid runtime transition"):
            machine.transition_to("full", reason="operator_forced_full")


if __name__ == "__main__":
    unittest.main()
