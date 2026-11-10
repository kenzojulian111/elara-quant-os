import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime.interfaces.orchestrator_service import OrchestratorService


class OrchestratorServiceTests(unittest.TestCase):
    def test_status_circuit_holds_false_recovery_during_cooldown(self):
        clock = {"now": 100.0}
        service = OrchestratorService(
            trading_status_path="",
            trading_command_path="",
            time_fn=lambda: clock["now"],
        )
        service.status_circuit.fail_threshold = 2
        service.status_circuit.cooldown_sec = 30.0

        first = service._observe_trading_status(None)
        self.assertFalse(first["is_running"])
        self.assertIn("dependency_failure_streak=trader_status:1", first["dependency_circuit_reasons"])

        second = service._observe_trading_status(None)
        self.assertFalse(second["is_running"])
        self.assertIn("dependency_circuit_open=trader_status:down", second["dependency_circuit_reasons"])

        clock["now"] = 110.0
        false_recovery = service._observe_trading_status(
            {
                "is_running": True,
                "control_state": "active",
            }
        )
        self.assertFalse(false_recovery["is_running"])
        self.assertIn("dependency_circuit_open=trader_status:up", false_recovery["dependency_circuit_reasons"])

        clock["now"] = 140.0
        recovered = service._observe_trading_status(
            {
                "is_running": True,
                "control_state": "active",
            }
        )
        self.assertTrue(recovered["is_running"])
        self.assertEqual("up", recovered["dependency_states"]["trader_status"])

    def test_setup_error_is_promoted_to_blocking_dependency_state(self):
        service = OrchestratorService(
            trading_status_path="",
            trading_command_path="",
        )

        observed = service._observe_trading_status(
            {
                "is_running": False,
                "setup_error": "exchange auth failed",
                "control_state": "blocked",
            }
        )

        self.assertFalse(observed["is_running"])
        self.assertEqual("down", observed["dependency_states"]["trader_setup"])
        self.assertEqual("exchange auth failed", observed["setup_error"])


if __name__ == "__main__":
    unittest.main()

