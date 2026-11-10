import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.nlp_engine import ElaraNLP


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


class _FakeFunctionCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunctionCall(name, arguments)


class _FakeCompletions:
    def __init__(self):
        self.last_kwargs = {}

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        msg = SimpleNamespace(
            tool_calls=[
                _FakeToolCall("call_1", "turn_off_device", {"device": "pc", "room": "kamar"}),
                _FakeToolCall("call_2", "get_trading_status", {}),
            ],
            content="",
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeGroqClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class ToolCallingNLUTests(unittest.TestCase):
    def setUp(self):
        self.state = _FakeStateManager()
        self.groq = _FakeGroqClient()
        self.tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "turn_off_device",
                    "description": "Turn off a device",
                    "parameters": {"type": "object", "properties": {"device": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_trading_status",
                    "description": "Get status",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def test_understand_returns_native_tool_calls(self):
        nlp = ElaraNLP(
            state_manager=self.state,
            groq_client=self.groq,
            providers=["groq"],
        )
        nlu = asyncio.run(
            nlp.understand(
                "tolong matikan pc lalu ringkas status trading",
                user_id="u1",
                tools_schema=self.tools_schema,
            )
        )[0]
        self.assertEqual(nlu.intent, "tool_call")
        self.assertEqual(len(nlu.tool_calls), 2)
        self.assertEqual(nlu.tool_calls[0].name, "turn_off_device")
        self.assertEqual(nlu.tool_calls[1].name, "get_trading_status")

    def test_tool_calls_cached_and_reused(self):
        nlp = ElaraNLP(
            state_manager=self.state,
            groq_client=self.groq,
            providers=["groq"],
        )
        first = asyncio.run(
            nlp.understand(
                "tolong matikan pc lalu ringkas status trading",
                user_id="u2",
                tools_schema=self.tools_schema,
            )
        )[0]
        self.assertEqual(first.intent, "tool_call")

        # Simulasikan provider offline: hasil harus tetap datang dari cache.
        nlp.groq_client = None
        second = asyncio.run(
            nlp.understand(
                "tolong matikan pc lalu ringkas status trading",
                user_id="u2",
                tools_schema=self.tools_schema,
            )
        )[0]
        self.assertEqual(second.intent, "tool_call")
        self.assertEqual(len(second.tool_calls), 2)

    def test_openai_options_include_nvidia_payload_knobs(self):
        openai = _FakeOpenAIClient()
        nlp = ElaraNLP(
            state_manager=self.state,
            openai_client=openai,
            providers=["openai"],
        )
        env = {
            "OPENAI_MAX_TOKENS": "16384",
            "OPENAI_TEMPERATURE": "0.60",
            "OPENAI_TOP_P": "0.95",
            "OPENAI_TOP_K": "20",
            "OPENAI_PRESENCE_PENALTY": "0",
            "OPENAI_REPETITION_PENALTY": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            got, src = asyncio.run(nlp.generate_tool_calls("cek status", self.tools_schema))

        self.assertEqual(src, "openai")
        self.assertEqual(len(got), 2)
        kwargs = openai.chat.completions.last_kwargs
        self.assertEqual(kwargs["max_tokens"], 16384)
        self.assertEqual(kwargs["temperature"], 0.60)
        self.assertEqual(kwargs["top_p"], 0.95)
        self.assertEqual(kwargs["presence_penalty"], 0.0)
        self.assertEqual(kwargs["extra_body"], {"top_k": 20, "repetition_penalty": 1.0})


if __name__ == "__main__":
    unittest.main()

