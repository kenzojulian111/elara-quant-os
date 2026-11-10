import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.service_contracts import CoreHealthContract, TraderStatusContract


class ServiceContractsTests(unittest.TestCase):
    def test_core_health_contract_reads_nested_check_statuses(self):
        contract = CoreHealthContract.from_payload(
            {
                "status": "ok",
                "checks": {
                    "redis": {"status": "ok"},
                    "db": {"status": "degraded"},
                },
            }
        )

        self.assertEqual("ok", contract.overall_status)
        self.assertEqual("ok", contract.check_status("redis"))
        self.assertEqual("degraded", contract.check_status("db"))
        self.assertEqual("unknown", contract.check_status("exchange"))

    def test_trader_status_contract_normalizes_missing_fields(self):
        contract = TraderStatusContract.from_payload({"is_running": 1})

        self.assertTrue(contract.is_running)
        self.assertFalse(contract.simulation_fallback)
        self.assertEqual("unknown", contract.control_state)
        self.assertEqual("", contract.setup_error)
        self.assertFalse(contract.stale)
        self.assertTrue(contract.raw["is_running"])
        self.assertEqual("unknown", contract.raw["control_state"])

    def test_trader_status_contract_normalizes_string_flags(self):
        contract = TraderStatusContract.from_payload(
            {
                "is_running": "true",
                "simulation_fallback": "false",
                "control_state": "active",
            }
        )

        self.assertTrue(contract.is_running)
        self.assertFalse(contract.simulation_fallback)
        self.assertEqual("active", contract.control_state)

    def test_trader_status_contract_preserves_setup_error_and_stale_flag(self):
        contract = TraderStatusContract.from_payload(
            {
                "is_running": False,
                "setup_error": "exchange auth failed",
                "stale": 1,
            }
        )

        self.assertFalse(contract.is_running)
        self.assertEqual("exchange auth failed", contract.setup_error)
        self.assertTrue(contract.stale)


if __name__ == "__main__":
    unittest.main()

