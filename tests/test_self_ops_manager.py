import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.self_ops import SelfOpsManager


class SelfOpsManagerTests(unittest.TestCase):
    def test_safe_json_dump_supports_relative_path_without_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SelfOpsManager(root_path=tmpdir, runtime_path=tmpdir)
            prev_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                manager._safe_json_dump("state.json", {"ok": True})
                self.assertTrue(os.path.exists("state.json"))
                with open("state.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data, {"ok": True})
            finally:
                os.chdir(prev_cwd)

    def test_repair_state_file_handles_relative_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SelfOpsManager(root_path=tmpdir, runtime_path=tmpdir)
            prev_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with open("state.json", "w", encoding="utf-8") as f:
                    f.write("{invalid")
                actions = []
                manager._repair_state_file("state.json", actions)
                self.assertTrue(any(a.startswith("state_repaired:") for a in actions))
                with open("state.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data, {})
            finally:
                os.chdir(prev_cwd)

    def test_collect_runtime_health_returns_score_and_severity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SelfOpsManager(root_path=tmpdir, runtime_path=tmpdir)
            actions = []
            warnings = []
            with patch.object(
                manager,
                "_run_command",
                return_value={"returncode": 1, "stdout": "", "stderr": "no git upstream", "cmd": "git"},
            ):
                health = manager._collect_runtime_health(actions, warnings)
            self.assertIn("score", health)
            self.assertIn("severity", health)
            self.assertGreaterEqual(float(health["score"]), 0.0)
            self.assertLessEqual(float(health["score"]), 100.0)

    def test_detect_heal_anomaly_on_score_drop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SelfOpsManager(root_path=tmpdir, runtime_path=tmpdir)
            rows = [
                {"health_score": 92.0},
                {"health_score": 91.0},
                {"health_score": 90.0},
                {"health_score": 89.0},
                {"health_score": 88.0},
                {"health_score": 87.0},
                {"health_score": 86.0},
                {"health_score": 65.0},
            ]
            msg = manager._detect_heal_anomaly(rows)
            self.assertIn("health_score_anomaly", msg)

    def test_collect_config_drift_detects_file_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracked = os.path.join(tmpdir, "tracked.txt")
            baseline = os.path.join(tmpdir, "runtime", "drift.json")
            os.makedirs(os.path.dirname(baseline), exist_ok=True)
            with open(tracked, "w", encoding="utf-8") as f:
                f.write("v1")

            manager = SelfOpsManager(root_path=tmpdir, runtime_path=os.path.join(tmpdir, "runtime"))
            actions_1, warnings_1 = [], []
            with patch("utils.self_ops.Config.SELF_HEAL_CONFIG_DRIFT_ENABLED", True, create=True), \
                 patch("utils.self_ops.Config.SELF_HEAL_CONFIG_DRIFT_PATH", baseline, create=True), \
                 patch("utils.self_ops.Config.SELF_HEAL_CONFIG_DRIFT_TARGETS", "tracked.txt", create=True):
                first = manager._collect_config_drift(actions_1, warnings_1)
                self.assertTrue(first.get("baseline_refreshed", False))
                self.assertEqual(first.get("changed_count", 0), 0)

                with open(tracked, "w", encoding="utf-8") as f:
                    f.write("v2")
                actions_2, warnings_2 = [], []
                second = manager._collect_config_drift(actions_2, warnings_2)
                self.assertGreater(second.get("changed_count", 0), 0)
                self.assertIn("tracked.txt", second.get("changed", []))

    def test_evaluate_deploy_slo_respects_thresholds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SelfOpsManager(root_path=tmpdir, runtime_path=tmpdir)
            with patch("utils.self_ops.Config.SELF_DEPLOY_SLO_ENFORCE", True, create=True), \
                 patch("utils.self_ops.Config.SELF_DEPLOY_SLO_MIN_HEALTH_SCORE", 60.0, create=True), \
                 patch("utils.self_ops.Config.SELF_DEPLOY_SLO_MAX_WARNING_COUNT", 3, create=True), \
                 patch("utils.self_ops.Config.SELF_DEPLOY_SLO_MAX_HEAL_DURATION_MS", 5000, create=True):
                bad = manager._evaluate_deploy_slo(
                    {"health_score": 40.0, "warnings": ["a"], "duration_ms": 1000}
                )
                self.assertFalse(bad.get("ok", True))

                good = manager._evaluate_deploy_slo(
                    {"health_score": 80.0, "warnings": ["a"], "duration_ms": 1200}
                )
                self.assertTrue(good.get("ok", False))


if __name__ == "__main__":
    unittest.main()

