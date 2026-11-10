import asyncio
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config.settings import Config

if "chromadb" not in sys.modules:
    fake_chromadb = types.ModuleType("chromadb")
    fake_utils = types.ModuleType("chromadb.utils")
    fake_embedding = types.ModuleType("chromadb.utils.embedding_functions")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_or_create_collection(self, *args, **kwargs):
            return SimpleNamespace(query=lambda **kw: {"documents": [[]]})

    fake_chromadb.PersistentClient = _FakeClient
    fake_embedding.SentenceTransformerEmbeddingFunction = lambda *args, **kwargs: None
    fake_utils.embedding_functions = fake_embedding

    sys.modules["chromadb"] = fake_chromadb
    sys.modules["chromadb.utils"] = fake_utils
    sys.modules["chromadb.utils.embedding_functions"] = fake_embedding

if "pypdf" not in sys.modules:
    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = object
    sys.modules["pypdf"] = fake_pypdf

from skills.KnowledgeSkill import KnowledgeSkill


class _FakeResponse:
    def __init__(self, text: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=text))]


class _FakeCompletions:
    async def create(self, **kwargs):
        return _FakeResponse("jawaban dari groq")


class _FakeGroqClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class KnowledgeSkillProviderFallbackTests(unittest.TestCase):
    def test_fallback_to_groq_when_primary_gemini_missing(self):
        with patch.object(Config, "LLM_PRIMARY_PROVIDER", "gemini"):
            skill = KnowledgeSkill.__new__(KnowledgeSkill)
            skill.nlp = SimpleNamespace(
                gemini_model=None,
                groq_client=_FakeGroqClient(),
                openai_client=None,
            )
            answer = asyncio.run(skill._generate_answer("ctx", "apa kabar?"))
            self.assertEqual(answer, "jawaban dari groq")


if __name__ == "__main__":
    unittest.main()

