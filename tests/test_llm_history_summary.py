import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from skills.LLMSkill import LLMSkill


class _FakeStateManager:
    def __init__(self):
        self._data = {}

    def set_state(self, key, value, user_id="default", ttl=None):
        _ = ttl
        self._data[(user_id, key)] = value

    def get_state(self, key, user_id="default"):
        return self._data.get((user_id, key))

    def delete_state(self, key, user_id="default"):
        self._data.pop((user_id, key), None)


class LLMHistorySummaryTests(unittest.TestCase):
    def setUp(self):
        self._backup = {
            "LLM_HISTORY_TURNS": Config.LLM_HISTORY_TURNS,
            "LLM_HISTORY_MAX_CHARS": Config.LLM_HISTORY_MAX_CHARS,
            "LLM_HISTORY_MAX_TOKENS": Config.LLM_HISTORY_MAX_TOKENS,
            "LLM_HISTORY_SUMMARY_ENABLED": getattr(Config, "LLM_HISTORY_SUMMARY_ENABLED", True),
            "LLM_HISTORY_SUMMARY_MAX_CHARS": getattr(Config, "LLM_HISTORY_SUMMARY_MAX_CHARS", 1600),
        }
        Config.LLM_HISTORY_TURNS = 2
        Config.LLM_HISTORY_MAX_CHARS = 180
        Config.LLM_HISTORY_MAX_TOKENS = 60
        Config.LLM_HISTORY_SUMMARY_ENABLED = True
        Config.LLM_HISTORY_SUMMARY_MAX_CHARS = 300

    def tearDown(self):
        for key, value in self._backup.items():
            setattr(Config, key, value)

    def test_old_history_compacted_into_summary(self):
        state = _FakeStateManager()
        skill = LLMSkill(state_manager=state)
        user_id = "u1"

        for idx in range(12):
            skill._append_history(user_id, "user", f"pesan user panjang ke-{idx} " + ("x" * 80))
            skill._append_history(user_id, "assistant", f"jawaban assistant panjang ke-{idx} " + ("y" * 80))

        history = state.get_state("llm_history", user_id=user_id) or []
        summary = state.get_state("llm_history_summary", user_id=user_id) or ""

        self.assertLessEqual(len(history), 8)
        self.assertTrue(str(summary).strip())


if __name__ == "__main__":
    unittest.main()

