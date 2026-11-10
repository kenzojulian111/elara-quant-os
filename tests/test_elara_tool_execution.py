import asyncio
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from core.base import NLPUnderstanding, ToolCall
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
    def __init__(self, nlu_result):
        self._nlu_result = nlu_result

    async def understand(self, text, user_id="default", tools_schema=None):
        _ = text, user_id, tools_schema
        return [self._nlu_result]

    async def summarize_tool_results(self, user_msg, tool_results):
        _ = user_msg
        names = [item.get("name") for item in tool_results]
        return f"tools_ok:{','.join(names)}"


class _FakeSkillManager:
    def __init__(self):
        self.intent_map = {}
        self.fallback_skill = None
        self.calls = []

    def get_all_tools_schema(self):
        return [
            {"type": "function", "function": {"name": "turn_off_device", "description": "", "parameters": {}}},
            {"type": "function", "function": {"name": "get_trading_status", "description": "", "parameters": {}}},
            {"type": "function", "function": {"name": "flatten_all_positions", "description": "", "parameters": {}}},
        ]

    async def execute_tool(self, name, args, user_id="default"):
        self.calls.append((name, args, user_id))
        return f"ok:{name}"


class ElaraToolExecutionTests(unittest.TestCase):
    def setUp(self):
        self._cfg_backup = {
            "CRITICAL_CONFIRM_ENABLED": Config.CRITICAL_CONFIRM_ENABLED,
            "CRITICAL_CONFIRM_TOOLS": getattr(Config, "CRITICAL_CONFIRM_TOOLS", "flatten_all_positions"),
        }
        Config.CRITICAL_CONFIRM_ENABLED = True
        Config.CRITICAL_CONFIRM_TOOLS = "flatten_all_positions"

    def tearDown(self):
        for key, value in self._cfg_backup.items():
            setattr(Config, key, value)

    def test_multi_tool_calls_executed_and_summarized(self):
        nlu = NLPUnderstanding(
            intent="tool_call",
            confidence=1.0,
            tool_calls=[
                ToolCall(name="turn_off_device", args={"device": "pc"}),
                ToolCall(name="get_trading_status", args={}),
            ],
            raw_text="matikan pc lalu cek trading",
            source="groq-tool-calling",
        )
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        elara = ElaraSkill(_FakeNLP(nlu), manager, state, db_manager=None)

        reply = asyncio.run(elara.handle_command("matikan pc lalu cek trading", user_id="u1"))
        self.assertIn("tools_ok:turn_off_device,get_trading_status", reply)
        self.assertEqual(len(manager.calls), 2)

    def test_critical_tool_call_requires_confirmation(self):
        nlu = NLPUnderstanding(
            intent="tool_call",
            confidence=1.0,
            tool_calls=[ToolCall(name="flatten_all_positions", args={})],
            raw_text="flatten sekarang",
            source="groq-tool-calling",
        )
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        elara = ElaraSkill(_FakeNLP(nlu), manager, state, db_manager=None)

        first = asyncio.run(elara.handle_command("flatten semua posisi", user_id="u2"))
        self.assertIn("Perintah kritikal", first)
        self.assertEqual(len(manager.calls), 0)

        match = re.search(r"/confirm\s+([a-f0-9]{4,32})", first.lower())
        self.assertIsNotNone(match)
        token = match.group(1)
        second = asyncio.run(elara.handle_command(f"/confirm {token}", user_id="u2"))
        self.assertIn("tools_ok:flatten_all_positions", second)
        self.assertEqual(len(manager.calls), 1)


if __name__ == "__main__":
    unittest.main()

