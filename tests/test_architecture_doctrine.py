import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ArchitectureDoctrineTests(unittest.TestCase):
    def test_doctrine_covers_roadmap_architecture_axes(self):
        doctrine = (ROOT / "docs" / "architecture_doctrine.md").read_text(encoding="utf-8")

        required_sections = [
            "## Hardest Parts",
            "## Architectural Direction",
            "## Governance Philosophy",
            "## Separation Of Responsibility",
            "## Runtime Thinking",
            "## Operational Safety",
            "## Observability Awareness",
            "## Decision Abstraction",
            "## Explainable Decision Flow",
            "## Layered Orchestration",
            "## Review Checklist",
        ]
        for section in required_sections:
            self.assertIn(section, doctrine)

    def test_core_identity_and_readme_link_doctrine(self):
        core_identity = (ROOT / "CORE_IDENTITY.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("docs/architecture_doctrine.md", core_identity)
        self.assertIn("docs/architecture_doctrine.md", readme)

    def test_doctrine_mentions_current_code_anchors(self):
        doctrine = (ROOT / "docs" / "architecture_doctrine.md").read_text(encoding="utf-8")

        anchors = [
            "trading_core/data_contracts.py",
            "trading_core/orchestration/decision_core.py",
            "trading_core/orchestration/decision_reasoner.py",
            "trading_core/orchestration/decision_governor.py",
            "core/runtime_state.py",
            "utils/observability.py",
        ]
        for anchor in anchors:
            self.assertIn(anchor, doctrine)


if __name__ == "__main__":
    unittest.main()
