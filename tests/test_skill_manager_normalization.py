import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from core.base import ElaraTool, NLPUnderstanding, Skill
from core.skill_manager import SkillManager


class _DemoSkill(Skill):
    def get_intents(self):
        return ["Ping_Test"]

    def get_tools(self):
        return [
            ElaraTool(
                name="Do_Ping",
                description="run ping",
                parameters={"type": "object", "properties": {}},
            )
        ]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        _ = user_id
        return f"handled:{nlu.intent}"

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        _ = args, user_id
        return f"tool:{name}"


class _SyncDemoSkill(Skill):
    def get_intents(self):
        return ["Sync_Ping"]

    def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        _ = user_id
        return f"sync:{nlu.intent}"


class SkillManagerNormalizationTests(unittest.TestCase):
    def setUp(self):
        self._backup_disabled_skills = Config.DISABLED_SKILLS
        self._backup_disabled_intents = Config.DISABLED_INTENTS
        Config.DISABLED_SKILLS = ""
        Config.DISABLED_INTENTS = ""

    def tearDown(self):
        Config.DISABLED_SKILLS = self._backup_disabled_skills
        Config.DISABLED_INTENTS = self._backup_disabled_intents

    def test_intent_lookup_is_case_insensitive(self):
        manager = SkillManager()
        manager.register(_DemoSkill())
        nlu = NLPUnderstanding(intent="PING_TEST", confidence=1.0)
        reply = asyncio.run(manager.handle(nlu, user_id="u1"))
        self.assertEqual(reply, "handled:PING_TEST")

    def test_tool_lookup_is_case_insensitive(self):
        manager = SkillManager()
        manager.register(_DemoSkill())
        reply = asyncio.run(manager.execute_tool("do_ping", {}, user_id="u2"))
        self.assertEqual(reply, "tool:do_ping")

    def test_execute_tool_respects_runtime_disabled_skill(self):
        manager = SkillManager()
        manager.register(_DemoSkill())
        manager.set_skill_enabled("DemoSkill", False)
        reply = asyncio.run(manager.execute_tool("do_ping", {}, user_id="u3"))
        self.assertIn("dinonaktifkan", reply.lower())

    def test_sync_handle_is_supported(self):
        manager = SkillManager()
        manager.register(_SyncDemoSkill())
        nlu = NLPUnderstanding(intent="SYNC_PING", confidence=1.0)
        reply = asyncio.run(manager.handle(nlu, user_id="u4"))
        self.assertEqual(reply, "sync:SYNC_PING")


if __name__ == "__main__":
    unittest.main()

