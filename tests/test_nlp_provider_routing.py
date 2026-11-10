import asyncio
import os
import sys
import unittest

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


class NLPProviderRoutingTests(unittest.TestCase):
    def test_understand_deduplicates_provider_sequence(self):
        nlp = ElaraNLP(state_manager=_FakeStateManager(), providers=["groq", "groq", "openai"])
        nlp.groq_client = object()
        nlp.openai_client = object()

        calls = []

        async def _fake_understanding(_text, provider):
            calls.append(provider)
            return None

        nlp._get_llm_understanding = _fake_understanding  # type: ignore[method-assign]
        out = asyncio.run(nlp.understand("command", user_id="u1"))
        self.assertEqual(len(out), 1)
        self.assertEqual(calls, ["groq", "openai"])

    def test_generate_tool_calls_deduplicates_provider_sequence(self):
        nlp = ElaraNLP(state_manager=_FakeStateManager(), providers=["groq", "groq", "openai"])
        nlp.groq_client = object()
        nlp.openai_client = object()

        calls = []

        async def _fake_tool_calls(user_msg, tools_schema, provider):
            _ = user_msg, tools_schema
            calls.append(provider)
            return []

        nlp._generate_tool_calls_with_provider = _fake_tool_calls  # type: ignore[method-assign]
        got, src = asyncio.run(
            nlp.generate_tool_calls(
                user_msg="please do stuff",
                tools_schema=[
                    {
                        "type": "function",
                        "function": {
                            "name": "noop",
                            "description": "noop",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )
        )
        self.assertEqual(got, [])
        self.assertEqual(src, "")
        self.assertEqual(calls, ["groq", "openai"])


if __name__ == "__main__":
    unittest.main()

