import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from core.base import NLPUnderstanding, ToolCall
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


class _FakeSkillManager:
    def __init__(self):
        self.calls = []

    def get_all_tools_schema(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "cek_cuaca",
                    "description": "cek cuaca",
                    "parameters": {"type": "object", "properties": {"kota": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "adguard_disable",
                    "description": "disable adguard",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute_tool(self, name, args, user_id="default"):
        self.calls.append((name, args, user_id))
        if name == "cek_cuaca":
            return "Jakarta sedang hujan."
        if name == "adguard_disable":
            return "AdGuard dimatikan."
        if name == "flatten_all_positions":
            return "flatten dikirim"
        return "ok"


class _FakeNLPPlanner:
    def __init__(self):
        self._step = 0

    async def generate_tool_calls(self, user_msg, tools_schema):
        _ = user_msg, tools_schema
        if self._step == 0:
            self._step += 1
            return ([ToolCall(name="cek_cuaca", args={"kota": "Jakarta"})], "test-planner")
        if self._step == 1:
            self._step += 1
            return ([ToolCall(name="adguard_disable", args={})], "test-planner")
        return ([], "test-planner")

    async def summarize_tool_results(self, user_msg, tool_results):
        _ = user_msg
        names = [x["name"] for x in tool_results]
        return "summary:" + ",".join(names)


class LLMSkillOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self._cfg = {
            "LLM_AGENT_MAX_LOOPS": Config.LLM_AGENT_MAX_LOOPS,
            "LLM_AGENT_MAX_TOTAL_TOOL_CALLS": Config.LLM_AGENT_MAX_TOTAL_TOOL_CALLS,
            "LLM_AGENT_TOOL_ALLOWLIST": Config.LLM_AGENT_TOOL_ALLOWLIST,
            "LLM_AGENT_TOOL_DENYLIST": Config.LLM_AGENT_TOOL_DENYLIST,
            "LLM_AGENT_ALLOW_CRITICAL_TOOLS": Config.LLM_AGENT_ALLOW_CRITICAL_TOOLS,
            "CRITICAL_CONFIRM_TOOLS": Config.CRITICAL_CONFIRM_TOOLS,
        }
        Config.LLM_AGENT_MAX_LOOPS = 3
        Config.LLM_AGENT_MAX_TOTAL_TOOL_CALLS = 8
        Config.LLM_AGENT_TOOL_ALLOWLIST = ""
        Config.LLM_AGENT_TOOL_DENYLIST = ""
        Config.LLM_AGENT_ALLOW_CRITICAL_TOOLS = False
        Config.CRITICAL_CONFIRM_TOOLS = "flatten_all_positions"

    def tearDown(self):
        for key, value in self._cfg.items():
            setattr(Config, key, value)

    def test_agent_loop_executes_multiple_steps(self):
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        planner = _FakeNLPPlanner()
        skill = LLMSkill(
            state_manager=state,
            skill_manager=manager,
            nlp_engine=planner,
            primary_provider="groq",
        )
        nlu = NLPUnderstanding(
            intent="general_conversation",
            confidence=1.0,
            entities=[],
            raw_text="cek cuaca jakarta dan matikan adguard",
            source="test",
        )
        reply = asyncio.run(skill.handle(nlu, user_id="u1"))
        self.assertIn("summary:cek_cuaca,adguard_disable", reply)
        self.assertEqual(len(manager.calls), 2)

    def test_direct_tool_calls_execute(self):
        state = _FakeStateManager()
        manager = _FakeSkillManager()
        planner = _FakeNLPPlanner()
        skill = LLMSkill(
            state_manager=state,
            skill_manager=manager,
            nlp_engine=planner,
            primary_provider="groq",
        )
        nlu = NLPUnderstanding(
            intent="tool_call",
            confidence=1.0,
            entities=[],
            tool_calls=[ToolCall(name="adguard_disable", args={})],
            raw_text="tool call",
            source="test",
        )
        reply = asyncio.run(skill.handle(nlu, user_id="u2"))
        self.assertIn("summary:adguard_disable", reply)
        self.assertEqual(len(manager.calls), 1)

    def test_critical_tool_blocked_in_agent_loop(self):
        class _CriticalPlanner:
            async def generate_tool_calls(self, user_msg, tools_schema):
                _ = user_msg, tools_schema
                return ([ToolCall(name="flatten_all_positions", args={})], "test")

            async def summarize_tool_results(self, user_msg, tool_results):
                _ = user_msg
                return str(tool_results)

        state = _FakeStateManager()
        manager = _FakeSkillManager()
        skill = LLMSkill(
            state_manager=state,
            skill_manager=manager,
            nlp_engine=_CriticalPlanner(),
            primary_provider="groq",
        )
        nlu = NLPUnderstanding(
            intent="general_conversation",
            confidence=1.0,
            entities=[],
            raw_text="flatten semua posisi",
            source="test",
        )
        reply = asyncio.run(skill.handle(nlu, user_id="u3"))
        self.assertIn("blocked", reply.lower())
        self.assertEqual(len(manager.calls), 0)

    def test_agent_respects_max_total_tool_calls(self):
        Config.LLM_AGENT_MAX_TOTAL_TOOL_CALLS = 1

        class _LoopPlanner:
            async def generate_tool_calls(self, user_msg, tools_schema):
                _ = user_msg, tools_schema
                return (
                    [
                        ToolCall(name="cek_cuaca", args={"kota": "Jakarta"}),
                        ToolCall(name="adguard_disable", args={}),
                    ],
                    "test",
                )

            async def summarize_tool_results(self, user_msg, tool_results):
                _ = user_msg
                return "calls=" + str(len(tool_results))

        state = _FakeStateManager()
        manager = _FakeSkillManager()
        skill = LLMSkill(
            state_manager=state,
            skill_manager=manager,
            nlp_engine=_LoopPlanner(),
            primary_provider="groq",
        )
        nlu = NLPUnderstanding(
            intent="general_conversation",
            confidence=1.0,
            entities=[],
            raw_text="jalankan banyak tools",
            source="test",
        )
        reply = asyncio.run(skill.handle(nlu, user_id="u4"))
        self.assertIn("calls=1", reply)
        self.assertEqual(len(manager.calls), 1)


if __name__ == "__main__":
    unittest.main()

