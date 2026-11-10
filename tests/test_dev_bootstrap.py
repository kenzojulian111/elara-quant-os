import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dev_bootstrap.bootstrap_runner import DevBootstrapConfig, run_bootstrap
from utils.service_contracts import CoreHealthContract, HealthCheckContract, TraderStatusContract


class DevBootstrapTests(unittest.TestCase):
    def test_run_bootstrap_starts_services_and_returns_summary(self):
        with tempfile.TemporaryDirectory(prefix="elara_dev_bootstrap_") as tmpdir:
            root = Path(tmpdir)
            core_health = CoreHealthContract(
                overall_status="ok",
                checks={
                    "redis": HealthCheckContract(name="redis", status="ok"),
                    "db": HealthCheckContract(name="db", status="ok"),
                    "exchange": HealthCheckContract(name="exchange", status="degraded"),
                },
            )
            trader_status = TraderStatusContract(
                is_running=True,
                simulation_fallback=False,
                control_state="active",
            )

            with (
                patch("dev_bootstrap.bootstrap_runner.project_root", return_value=root),
                patch("dev_bootstrap.bootstrap_runner.service_manager.find_python", return_value="python"),
                patch("dev_bootstrap.bootstrap_runner.service_manager.start_redis") as start_redis,
                patch("dev_bootstrap.bootstrap_runner.service_manager.start_python_service") as start_python_service,
                patch("dev_bootstrap.bootstrap_runner.health_client.fetch_core_health", return_value=core_health),
                patch("dev_bootstrap.bootstrap_runner.health_client.fetch_trader_status", return_value=trader_status),
            ):
                summary = run_bootstrap(DevBootstrapConfig(addr="127.0.0.1", redis_port=6379, core_port=5000, trader_port=8768))

        self.assertEqual("ok", summary.core_health)
        self.assertEqual("ok", summary.redis_health)
        self.assertEqual("ok", summary.db_health)
        self.assertEqual("degraded", summary.exchange_health)
        self.assertTrue(summary.trader_running)
        self.assertEqual("active", summary.trader_control_state)
        self.assertTrue(summary.logs_dir.endswith(os.path.join("runtime", "logs")))
        self.assertEqual(1, start_redis.call_count)
        self.assertEqual(2, start_python_service.call_count)

    def test_run_bootstrap_tolerates_health_failures(self):
        with tempfile.TemporaryDirectory(prefix="elara_dev_bootstrap_fail_") as tmpdir:
            root = Path(tmpdir)
            with (
                patch("dev_bootstrap.bootstrap_runner.project_root", return_value=root),
                patch("dev_bootstrap.bootstrap_runner.service_manager.find_python", return_value="python"),
                patch("dev_bootstrap.bootstrap_runner.service_manager.start_redis"),
                patch("dev_bootstrap.bootstrap_runner.service_manager.start_python_service"),
                patch("dev_bootstrap.bootstrap_runner.health_client.fetch_core_health", side_effect=RuntimeError("core down")),
                patch("dev_bootstrap.bootstrap_runner.health_client.fetch_trader_status", side_effect=RuntimeError("trader down")),
            ):
                summary = run_bootstrap(DevBootstrapConfig())

        self.assertEqual("unknown", summary.core_health)
        self.assertFalse(summary.trader_running)


if __name__ == "__main__":
    unittest.main()

