import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import ComplexityGovernanceChecker


class ComplexityGovernanceTests(unittest.TestCase):
    def test_current_repo_has_no_critical_complexity_contract_gap(self):
        report = ComplexityGovernanceChecker(ROOT).build_report()

        self.assertEqual(0, report.critical_count, report.to_dict())
        self.assertEqual("warning", report.status)
        self.assertIn("Complexity governance is intact", report.summary)

    def test_config_entropy_is_explicitly_tracked(self):
        report = ComplexityGovernanceChecker(ROOT).build_report()
        config_domain = report.domain("config_surface")
        finding_codes = {finding.code for finding in config_domain.findings}

        self.assertNotIn("config_env_surface_large", finding_codes)
        self.assertLessEqual(
            config_domain.metrics["env_keys"],
            ComplexityGovernanceChecker.ENV_KEY_WARNING_BUDGET,
        )
        self.assertGreater(config_domain.metrics["env_keys"], 0)

    def test_frontier_baselines_are_closed_without_pretending_production_is_deployed(self):
        report = ComplexityGovernanceChecker(ROOT).build_report()
        frontier = report.domain("frontier_gap_tracker")
        finding_codes = {finding.code for finding in frontier.findings}

        self.assertNotIn("rust_runtime_tracked_gap", finding_codes)
        self.assertNotIn("distributed_cognition_tracked_gap", finding_codes)
        self.assertEqual(100, frontier.score)
        self.assertTrue(all(finding.severity == "warning" for finding in frontier.findings))

    def test_missing_protocol_anchor_is_critical(self):
        with tempfile.TemporaryDirectory(prefix="elara_complexity_gate_") as tmpdir:
            tmp = Path(tmpdir)
            for path in (
                ".env.example",
                "CORE_IDENTITY.md",
                "docs/architecture_doctrine.md",
                "docs/complexity_governance.md",
                "docs/config_governance.md",
                "docs/observability_q1.md",
                "docs/year1_completion.md",
                "docs/year1_q1_q3_execution.md",
                "docs/year1_q4_year2_q4_execution.md",
                "config/core.env.example",
                "config/runtime.env.example",
                "config/trading.env.example",
                "config/governance.env.example",
                "config/observability.env.example",
                "config/llm.env.example",
                "config/blockchain.env.example",
                "config/experimental.env.example",
                "config/governance.py",
                "config/pydantic_settings_schema.py",
                "trading_core/data_contracts.py",
            ):
                target = tmp / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("X=1\n" if path == ".env.example" else "# anchor\n", encoding="utf-8")

            report = ComplexityGovernanceChecker(tmp).build_report()

        self.assertGreater(report.critical_count, 0)
        self.assertIn("protocol_contracts_missing", {finding.code for finding in report.findings})


if __name__ == "__main__":
    unittest.main()
