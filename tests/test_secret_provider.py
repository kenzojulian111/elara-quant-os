import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import secret_provider


class SecretProviderTests(unittest.TestCase):
    def tearDown(self):
        secret_provider.clear_secret_provider_cache()

    def test_env_has_priority_over_file_provider(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps({"API_TOKEN": "from-file"}))
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        with patch.dict(
            os.environ,
            {
                "API_TOKEN": "from-env",
                "SECRET_MANAGER_PROVIDER": "file",
                "SECRET_MANAGER_FILE": path,
            },
            clear=False,
        ):
            secret_provider.clear_secret_provider_cache()
            self.assertEqual(secret_provider.get_secret("API_TOKEN"), "from-env")

    def test_file_provider_cache_refreshes_after_file_change(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps({"ROTATING_TOKEN": "v1"}))
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        with patch.dict(
            os.environ,
            {
                "SECRET_MANAGER_PROVIDER": "file",
                "SECRET_MANAGER_FILE": path,
            },
            clear=False,
        ):
            secret_provider.clear_secret_provider_cache()
            self.assertEqual(secret_provider.get_secret("ROTATING_TOKEN"), "v1")

            time.sleep(0.01)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ROTATING_TOKEN": "v2"}, f)

            self.assertEqual(secret_provider.get_secret("ROTATING_TOKEN"), "v2")

    def test_evict_secrets_from_env_counts_removed_keys(self):
        with patch.dict(os.environ, {"A": "1", "B": "2"}, clear=False):
            removed = secret_provider.evict_secrets_from_env(["A", "", "C", "B"])
            self.assertEqual(removed, 2)
            self.assertNotIn("A", os.environ)
            self.assertNotIn("B", os.environ)


if __name__ == "__main__":
    unittest.main()

