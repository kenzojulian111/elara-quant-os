import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.base import Entity, NLPUnderstanding, Skill
from skills.elara_skill import ElaraSkill


class _FakeStateManager:
    def set_state(self, key, value, user_id="default", ttl=None):
        _ = (key, value, user_id, ttl)

    def get_state(self, key, user_id="default"):
        _ = (key, user_id)
        return None

    def delete_state(self, key, user_id="default"):
        _ = (key, user_id)


class _FakeNLP:
    def __init__(self, nlu):
        self._nlu = nlu

    async def understand(self, text, user_id="default", tools_schema=None):
        _ = (text, user_id, tools_schema)
        return [self._nlu]


class _DummySkill(Skill):
    def __init__(self):
        self.calls = 0

    def get_intents(self):
        return ["turn_on_device"]

    async def handle(self, nlu, user_id="default"):
        _ = (nlu, user_id)
        self.calls += 1
        return "ok-turn-on"


class _FakeSkillManager:
    def __init__(self, skill):
        self.intent_map = {"turn_on_device": skill}
        self.fallback_skill = None

    def get_all_tools_schema(self):
        return []


class _FakeDbManager:
    def __init__(self):
        self.calls = []

    def log_command(self, source, intent, text, entities=None, user_id="default"):
        self.calls.append(
            {
                "source": source,
                "intent": intent,
                "text": text,
                "entities": entities or [],
                "user_id": user_id,
            }
        )


class ElaraCommandLoggingTests(unittest.TestCase):
    def test_handle_command_uses_db_manager_log_command_in_app_context(self):
        nlu = NLPUnderstanding(
            intent="turn_on_device",
            confidence=0.95,
            entities=[Entity(type="device", value="lampu", span=None)],
            source="keyword_bypass",
            raw_text="nyalakan lampu",
        )
        skill = _DummySkill()
        skill_manager = _FakeSkillManager(skill)
        db_manager = _FakeDbManager()
        elara = ElaraSkill(
            nlp_engine=_FakeNLP(nlu),
            skill_manager=skill_manager,
            state_manager=_FakeStateManager(),
            db_manager=db_manager,
        )

        with patch("skills.elara_skill.has_app_context", return_value=True):
            reply = asyncio.run(elara.handle_command("nyalakan lampu", user_id="u1", source="telegram"))

        self.assertEqual(reply, "ok-turn-on")
        self.assertEqual(skill.calls, 1)
        self.assertEqual(len(db_manager.calls), 1)
        logged = db_manager.calls[0]
        self.assertEqual(logged["source"], "telegram")
        self.assertEqual(logged["intent"], "turn_on_device")
        self.assertEqual(logged["text"], "nyalakan lampu")
        self.assertEqual(logged["user_id"], "u1")
        self.assertEqual(logged["entities"], [{"type": "device", "value": "lampu", "span": None}])


if __name__ == "__main__":
    unittest.main()

