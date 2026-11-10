from __future__ import annotations

from config.settings import Config
from core.base import NLPUnderstanding, Skill


class KnowledgeSkill(Skill):
    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        _ = user_id
        context = str(getattr(nlu, "response", "") or "")
        question = str(getattr(nlu, "raw_text", "") or "")
        return await self._generate_answer(context, question)

    async def _generate_answer(self, context: str, question: str) -> str:
        prompt = f"Konteks:\n{context}\n\nPertanyaan:\n{question}".strip()
        provider_order = []
        primary = str(getattr(Config, "LLM_PRIMARY_PROVIDER", "") or "").strip().lower()
        for provider in (primary, "gemini", "groq", "openai"):
            if provider and provider not in provider_order:
                provider_order.append(provider)

        nlp = getattr(self, "nlp", None)
        if nlp is None:
            return "Knowledge provider belum tersedia."

        for provider in provider_order:
            if provider == "gemini":
                model = getattr(nlp, "gemini_model", None)
                if model is None:
                    continue
                if hasattr(model, "generate_content_async"):
                    response = await model.generate_content_async(prompt)
                    text = str(getattr(response, "text", "") or "").strip()
                    if text:
                        return text
            if provider == "groq":
                client = getattr(nlp, "groq_client", None)
                if client is None:
                    continue
                response = await client.chat.completions.create(
                    model=str(getattr(Config, "GROQ_MODEL", "llama-3.1-8b-instant") or "llama-3.1-8b-instant"),
                    messages=[{"role": "user", "content": prompt}],
                )
                try:
                    text = str(response.choices[0].message.content or "").strip()
                except Exception:
                    text = ""
                if text:
                    return text
            if provider == "openai":
                client = getattr(nlp, "openai_client", None)
                if client is None:
                    continue
                response = await client.chat.completions.create(
                    model=str(getattr(Config, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini"),
                    messages=[{"role": "user", "content": prompt}],
                )
                try:
                    text = str(response.choices[0].message.content or "").strip()
                except Exception:
                    text = ""
                if text:
                    return text
        return "Knowledge provider fallback tidak menemukan jawaban."

