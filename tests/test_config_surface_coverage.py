import os
import re
import sys
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config


class ConfigSurfaceCoverageTests(unittest.TestCase):
    def test_all_config_attributes_referenced_in_repo_exist(self):
        root = Path(ROOT)
        pattern = re.compile(r"Config\.([A-Z][A-Z_]*)")
        keys = set()

        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            keys.update(pattern.findall(text))

        missing = []
        for key in sorted(keys):
            try:
                getattr(Config, key)
            except AttributeError:
                missing.append(key)

        self.assertEqual([], missing, f"Missing config attrs: {missing}")


if __name__ == "__main__":
    unittest.main()

