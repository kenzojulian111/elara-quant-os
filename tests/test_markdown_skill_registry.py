import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.markdown_skill_registry import MarkdownSkillRegistry


def _write_skill(base_dir: str, folder_name: str, skill_name: str, description: str = "desc") -> str:
    skill_dir = os.path.join(base_dir, folder_name)
    os.makedirs(skill_dir, exist_ok=True)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(
            "---\n"
            f"name: {skill_name}\n"
            f"description: {description}\n"
            "trust: standard\n"
            "---\n\n"
            "# Skill Body\n"
        )
    return skill_md


class MarkdownSkillRegistryTests(unittest.TestCase):
    def _make_registry(self, directory: str) -> MarkdownSkillRegistry:
        reg = MarkdownSkillRegistry(
            directories=[directory],
            root_path=ROOT,
            enabled=True,
            include_patterns=[],
            exclude_patterns=[],
            refresh_sec=5,
        )
        reg.refresh_if_needed(force=True)
        return reg

    def test_frontmatter_name_with_underscore_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(tmp, "android_ui_verification", "android_ui_verification")
            reg = self._make_registry(tmp)

            self.assertEqual(1, reg.count())
            self.assertIsNotNone(reg.get("android_ui_verification"))
            self.assertIsNotNone(reg.get("android-ui-verification"))

    def test_explicit_route_supports_underscore_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(tmp, "android_ui_verification", "android_ui_verification")
            reg = self._make_registry(tmp)

            routed = reg.route_from_text("pakai $android_ui_verification")
            self.assertIsNotNone(routed)
            self.assertEqual("explicit", routed.matched_by)
            self.assertEqual("android-ui-verification", routed.doc.name)

    def test_duplicate_skill_name_keeps_first_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_path = _write_skill(tmp, "docx", "docx-official", description="first")
            _write_skill(tmp, "docx-copy", "docx-official", description="second")
            reg = self._make_registry(tmp)

            self.assertEqual(1, reg.count())
            doc = reg.get("docx-official")
            self.assertIsNotNone(doc)
            self.assertEqual("first", doc.description)
            self.assertEqual(first_path, doc.path)

    def test_source_breakdown_reports_loaded_count_and_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(tmp, "skill-a", "systematic-debugging")
            _write_skill(tmp, "skill-b", "elara-trading-ops")
            missing_dir = os.path.join(tmp, "not-exists")
            reg = MarkdownSkillRegistry(
                directories=[tmp, missing_dir],
                root_path=ROOT,
                enabled=True,
                include_patterns=[],
                exclude_patterns=[],
                refresh_sec=5,
            )
            reg.refresh_if_needed(force=True)

            rows = reg.source_breakdown()
            by_path = {str(row.get("path") or ""): row for row in rows}

            self.assertIn(tmp, by_path)
            self.assertTrue(bool(by_path[tmp].get("exists")))
            self.assertEqual(2, int(by_path[tmp].get("loaded") or 0))

            self.assertIn(missing_dir, by_path)
            self.assertFalse(bool(by_path[missing_dir].get("exists")))
            self.assertEqual(0, int(by_path[missing_dir].get("loaded") or 0))

    def test_watch_refresh_detects_new_skill_without_waiting_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(tmp, "skill-a", "systematic-debugging")
            reg = MarkdownSkillRegistry(
                directories=[tmp],
                root_path=ROOT,
                enabled=True,
                include_patterns=[],
                exclude_patterns=[],
                refresh_sec=3600,
                watch_enabled=True,
                watch_interval_sec=0.05,
            )
            reg.refresh_if_needed(force=True)
            self.assertIn("systematic-debugging", reg.list_names())

            _write_skill(tmp, "skill-b", "elara-trading-ops")
            time.sleep(0.25)

            names = reg.list_names()
            self.assertIn("elara-trading-ops", names)
            stats = reg.stats()
            self.assertGreaterEqual(int(stats.get("watch_change_count") or 0), 1)
            self.assertGreaterEqual(int(stats.get("refresh_count") or 0), 2)


if __name__ == "__main__":
    unittest.main()

