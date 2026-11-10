import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import Year1ReadinessChecker


class Year1ReadinessTests(unittest.TestCase):
    def test_year1_readiness_report_is_ready(self):
        report = Year1ReadinessChecker(ROOT).build_report()

        self.assertTrue(report.ready, report.to_dict())
        self.assertEqual("Year 1 roadmap baseline ready.", report.summary)
        self.assertEqual({"Q1", "Q2", "Q3", "Q4"}, {quarter.quarter for quarter in report.quarters})

    def test_quarter_capabilities_match_year1_goals(self):
        report = Year1ReadinessChecker(ROOT).build_report()

        self.assertIn("runtime_health", report.quarter("Q1").capabilities)
        self.assertIn("append_only_events", report.quarter("Q2").capabilities)
        self.assertIn("probabilistic_decision", report.quarter("Q3").capabilities)
        self.assertIn("uncertainty_profile", report.quarter("Q3").capabilities)
        self.assertIn("causal_graph_inference", report.quarter("Q3").capabilities)
        self.assertIn("operational_memory", report.quarter("Q4").capabilities)

    def test_year1_completion_doc_links_readiness_gate(self):
        doc = (ROOT / "docs" / "year1_completion.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Year1ReadinessChecker", doc)
        self.assertIn("docs/year1_completion.md", readme)


if __name__ == "__main__":
    unittest.main()
