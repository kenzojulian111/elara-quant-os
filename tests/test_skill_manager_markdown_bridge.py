import os
import shutil
import tempfile
import time
import unittest

from config.settings import Config
from core.skill_manager import SkillManager


class SkillManagerMarkdownBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_md_bridge_")
        cls._skills_dir = os.path.join(cls._tmpdir, "my-skills")
        os.makedirs(cls._skills_dir, exist_ok=True)
        cls._skill_name = "custom-ops-skill"
        skill_dir = os.path.join(cls._skills_dir, cls._skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: custom-ops-skill\n"
                "description: Custom operations skill for core bridge tests.\n"
                "trust_level: standard\n"
                "tags: ops,bridge\n"
                "---\n\n"
                "# Custom Ops Skill\n\n"
                "Bridge test body.\n"
            )

        cls._cfg_backup = {
            "EXPERIMENTAL_RUNTIME_ALLOWLIST": getattr(Config, "EXPERIMENTAL_RUNTIME_ALLOWLIST", ""),
            "LLM_MARKDOWN_SKILLS_ENABLED": getattr(Config, "LLM_MARKDOWN_SKILLS_ENABLED", False),
            "LLM_MARKDOWN_SKILLS_DIRS": getattr(Config, "LLM_MARKDOWN_SKILLS_DIRS", ""),
            "LLM_MARKDOWN_SKILLS_INCLUDE": getattr(Config, "LLM_MARKDOWN_SKILLS_INCLUDE", ""),
            "LLM_MARKDOWN_SKILLS_EXCLUDE": getattr(Config, "LLM_MARKDOWN_SKILLS_EXCLUDE", ""),
            "LLM_MARKDOWN_SKILLS_MAX_COUNT": getattr(Config, "LLM_MARKDOWN_SKILLS_MAX_COUNT", 1200),
            "LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS": getattr(Config, "LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS", 6000),
            "LLM_MARKDOWN_SKILLS_REFRESH_SEC": getattr(Config, "LLM_MARKDOWN_SKILLS_REFRESH_SEC", 30),
            "LLM_MARKDOWN_SKILLS_WATCH_ENABLED": getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_ENABLED", True),
            "LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC": getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC", 2.0),
        }

        Config.EXPERIMENTAL_RUNTIME_ALLOWLIST = "markdown_registry"
        Config.LLM_MARKDOWN_SKILLS_ENABLED = True
        Config.LLM_MARKDOWN_SKILLS_DIRS = cls._skills_dir
        Config.LLM_MARKDOWN_SKILLS_INCLUDE = ""
        Config.LLM_MARKDOWN_SKILLS_EXCLUDE = ""
        Config.LLM_MARKDOWN_SKILLS_MAX_COUNT = 100
        Config.LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS = 4000
        Config.LLM_MARKDOWN_SKILLS_REFRESH_SEC = 3600
        Config.LLM_MARKDOWN_SKILLS_WATCH_ENABLED = True
        Config.LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC = 0.05

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def test_core_skill_manager_loads_markdown_registry(self):
        manager = SkillManager()
        items = manager.list_markdown_skills(limit=20)
        names = {str((row or {}).get("name") or "") for row in items}
        self.assertIn(self._skill_name, names)

    def test_core_skill_manager_search_markdown_registry(self):
        manager = SkillManager()
        rows = manager.search_markdown_skills("custom ops", limit=10)
        self.assertTrue(rows)
        first = rows[0]
        self.assertEqual(first.get("name"), self._skill_name)
        self.assertIn("score", first)

    def test_core_skill_manager_exposes_markdown_sources(self):
        manager = SkillManager()
        rows = manager.get_markdown_skill_sources()
        self.assertTrue(rows)
        by_path = {str((row or {}).get("path") or ""): row for row in rows}
        self.assertIn(self._skills_dir, by_path)
        source = by_path[self._skills_dir]
        self.assertTrue(bool(source.get("exists")))
        self.assertGreaterEqual(int(source.get("loaded") or 0), 1)

    def test_core_skill_manager_exposes_markdown_registry_stats(self):
        manager = SkillManager()
        stats = manager.get_markdown_registry_stats()
        self.assertTrue(bool(stats.get("available")))
        self.assertGreaterEqual(int(stats.get("count") or 0), 1)
        self.assertTrue(bool(stats.get("enabled")))
        self.assertIn("watch_enabled", stats)
        self.assertIn("refresh_count", stats)

    def test_core_skill_manager_watch_detects_new_skill(self):
        manager = SkillManager()
        dynamic_skill = "bridge-dynamic-skill"
        dynamic_dir = os.path.join(self._skills_dir, dynamic_skill)
        os.makedirs(dynamic_dir, exist_ok=True)
        with open(os.path.join(dynamic_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: bridge-dynamic-skill\n"
                "description: Dynamic skill to verify watch refresh.\n"
                "---\n\n"
                "# Dynamic\n"
            )
        time.sleep(0.25)
        names = {str((row or {}).get("name") or "") for row in manager.list_markdown_skills(limit=200)}
        self.assertIn(dynamic_skill, names)

