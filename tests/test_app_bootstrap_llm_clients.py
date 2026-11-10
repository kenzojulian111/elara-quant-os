import inspect
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import app_bootstrap


class AppBootstrapLLMClientTests(unittest.TestCase):
    def test_build_llm_clients_returns_plain_tuple(self):
        with (
            patch.object(app_bootstrap.Config, "OPENAI_API_KEY", ""),
            patch.object(app_bootstrap.Config, "GROQ_API_KEY", ""),
            patch.dict(os.environ, {"LLM_BASE_URL": "", "LLM_LOCAL_API_KEY": ""}, clear=False),
        ):
            result = app_bootstrap._build_llm_clients()

        self.assertFalse(inspect.isawaitable(result))
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()

