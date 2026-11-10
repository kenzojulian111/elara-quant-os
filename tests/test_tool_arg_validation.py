import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.base import NLPUnderstanding, ToolCall
from config.settings import Config
from skills.elara_skill import ElaraSkill


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


class _FakeNLP:
    def __init__(self, nlu):
        self._nlu = nlu

    async def understand(self, text, user_id="default", tools_schema=None):
        _ = text, user_id, tools_schema
        return [self._nlu]

    async def summarize_tool_results(self, user_msg, tool_results):
        _ = user_msg
        return "\n".join(str(item.get("result") or "") for item in tool_results)


class _FakeSkillManager:
    def __init__(self):
        self.calls = []
        self.intent_map = {}
        self.fallback_skill = None

    def get_all_tools_schema(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "set_mode",
                    "description": "Set bot mode",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["safe", "fast"]},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["mode"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    async def execute_tool(self, name, args, user_id="default"):
        self.calls.append((name, args, user_id))
        return f"ok:{name}"


class ToolArgValidationTests(unittest.TestCase):
    def setUp(self):
        self._allowlist_backup = getattr(Config, "LLM_AGENT_TOOL_ALLOWLIST", "")
        Config.LLM_AGENT_TOOL_ALLOWLIST = ""

    def tearDown(self):
        Config.LLM_AGENT_TOOL_ALLOWLIST = self._allowlist_backup

    def test_invalid_enum_argument_blocked(self):
        nlu = NLPUnderstanding(
            intent="tool_call",
            confidence=1.0,
            tool_calls=[ToolCall(name="set_mode", args={"mode": "turbo"})],
            raw_text="set mode turbo",
            source="tool-calling",
        )
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        elara = ElaraSkill(_FakeNLP(nlu), manager, state, db_manager=None)

        reply = asyncio.run(elara.handle_command("set mode turbo", user_id="u1"))
        self.assertIn("invalid args", reply)
        self.assertEqual(len(manager.calls), 0)

    def test_valid_enum_argument_executes_tool(self):
        nlu = NLPUnderstanding(
            intent="tool_call",
            confidence=1.0,
            tool_calls=[ToolCall(name="set_mode", args={"mode": "safe", "confirm": True})],
            raw_text="set mode safe",
            source="tool-calling",
        )
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        elara = ElaraSkill(_FakeNLP(nlu), manager, state, db_manager=None)

        reply = asyncio.run(elara.handle_command("set mode safe", user_id="u2"))
        self.assertIn("ok:set_mode", reply)
        self.assertEqual(len(manager.calls), 1)


if __name__ == "__main__":
    unittest.main()

