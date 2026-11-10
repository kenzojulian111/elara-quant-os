from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

from core.base import Entity, NLPUnderstanding, ToolCall


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        key = str(item or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


class ElaraNLP:
    def __init__(
        self,
        state_manager=None,
        providers: list[str] | None = None,
        groq_client=None,
        openai_client=None,
        gemini_model=None,
        tool_cache_ttl_sec: int | None = None,
    ):
        self.state_manager = state_manager
        self.providers = list(providers or [])
        self.groq_client = groq_client
        self.openai_client = openai_client
        self.gemini_model = gemini_model
        env_ttl = os.getenv("NLP_TOOL_CACHE_TTL_SEC", "").strip()
        if tool_cache_ttl_sec is None and env_ttl:
            try:
                tool_cache_ttl_sec = int(env_ttl)
            except Exception:
                tool_cache_ttl_sec = None
        self.tool_cache_ttl_sec = int(tool_cache_ttl_sec) if tool_cache_ttl_sec is not None else 300

    def _provider_available(self, provider: str) -> bool:
        provider = str(provider or "").strip().lower()
        if provider == "groq":
            return self.groq_client is not None
        if provider == "openai":
            return self.openai_client is not None
        if provider == "gemini":
            return self.gemini_model is not None
        return False

    def _provider_sequence(self) -> list[str]:
        return [provider for provider in _dedupe(self.providers) if self._provider_available(provider)]

    def _tool_cache_key(self, user_id: str, text: str, tools_schema: list[dict]) -> str:
        raw = json.dumps(
            {
                "u": user_id,
                "t": text,
                "tools": [item.get("function", {}).get("name") for item in tools_schema],
            },
            sort_keys=True,
        )
        return "nlp_tool_cache:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _resolve_model(self, provider: str, purpose: str = "nlu") -> str:
        provider = str(provider or "").strip().lower()
        purpose = str(purpose or "nlu").strip().lower()

        def _env_first(*keys: str, default: str) -> str:
            for key in keys:
                raw = os.getenv(key, "").strip()
                if raw:
                    return raw
            return default

        if provider == "groq":
            base = _env_first("GROQ_MODEL", default="llama-3.1-8b-instant")
        elif provider == "openai":
            base = _env_first("OPENAI_MODEL", default="gpt-4o-mini")
        elif provider == "gemini":
            base = _env_first("GEMINI_MODEL", default="gemini-1.5-flash-latest")
        else:
            base = "gpt-4o-mini"

        if purpose == "tool":
            return _env_first(
                "LLM_TOOL_MODEL",
                f"{provider.upper()}_TOOL_MODEL",
                default=base,
            )
        return _env_first(
            "LLM_NLU_MODEL",
            f"{provider.upper()}_NLU_MODEL",
            default=base,
        )

    def _completion_options(self, provider: str, *, deterministic: bool = False) -> dict:
        provider = str(provider or "").strip().lower()

        def _float_env(key: str, default: float | None = None) -> float | None:
            raw = os.getenv(key, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _int_env(key: str, default: int | None = None) -> int | None:
            raw = os.getenv(key, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError:
                return default
            return value if value > 0 else default

        options: dict = {}
        temperature = 0.0 if deterministic else _float_env(f"{provider.upper()}_TEMPERATURE", 0.6)
        if temperature is not None:
            options["temperature"] = temperature
        max_tokens = _int_env(f"{provider.upper()}_MAX_TOKENS")
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        top_p = _float_env(f"{provider.upper()}_TOP_P")
        if top_p is not None:
            options["top_p"] = top_p
        presence_penalty = _float_env(f"{provider.upper()}_PRESENCE_PENALTY")
        if presence_penalty is not None:
            options["presence_penalty"] = presence_penalty

        if provider == "openai":
            extra_body: dict = {}
            top_k = _int_env("OPENAI_TOP_K")
            repetition_penalty = _float_env("OPENAI_REPETITION_PENALTY")
            if top_k is not None:
                extra_body["top_k"] = top_k
            if repetition_penalty is not None:
                extra_body["repetition_penalty"] = repetition_penalty
            if extra_body:
                options["extra_body"] = extra_body
        return options

    def _nlu_prompt(self, text: str) -> tuple[str, str]:
        system = (
            "You are an intent router for a personal assistant. "
            "Return ONLY JSON with keys: intent, confidence, entities, response. "
            "intent must be one of: get_weather, trading_status, screen_stocks, "
            "goal_add, goal_list, goal_focus, goal_next, goal_archive, goal_done, "
            "goal_block, goal_unblock, general_conversation. "
            "confidence is 0-1. entities is a list of {type, value}. "
            "response is a short Indonesian summary."
        )
        user = f"User: {text}"
        return system, user

    def _parse_llm_json(self, content: str, raw_text: str) -> NLPUnderstanding | None:
        if not content:
            return None
        payload = None
        try:
            payload = json.loads(content)
        except Exception:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if match:
                try:
                    payload = json.loads(match.group(0))
                except Exception:
                    payload = None
        if not isinstance(payload, dict):
            return None
        intent = str(payload.get("intent") or "general_conversation").strip() or "general_conversation"
        try:
            confidence = float(payload.get("confidence") or 0.4)
        except Exception:
            confidence = 0.4
        entities_raw = payload.get("entities") or []
        entities: list[Entity] = []
        if isinstance(entities_raw, list):
            for item in entities_raw:
                if not isinstance(item, dict):
                    continue
                etype = str(item.get("type") or "").strip()
                value = str(item.get("value") or "").strip()
                if etype or value:
                    entities.append(Entity(type=etype or "unknown", value=value))
        response = str(payload.get("response") or "").strip()
        return NLPUnderstanding(
            intent=intent,
            confidence=max(0.0, min(1.0, confidence)),
            entities=entities,
            response=response,
            source="llm",
            raw_text=raw_text,
        )

    def _keyword_understanding(self, text: str):
        lowered = str(text or "").lower()
        if "cuaca" in lowered:
            return NLPUnderstanding(intent="get_weather", confidence=0.9, source="keyword_flexible", raw_text=text)
        if "status trading" in lowered or "status hedge" in lowered:
            return NLPUnderstanding(intent="trading_status", confidence=0.9, source="keyword_flexible", raw_text=text)
        if "saham" in lowered and "momentum" in lowered:
            return NLPUnderstanding(intent="screen_stocks", confidence=0.9, source="keyword_flexible", raw_text=text)
        if lowered.startswith("/goal") or lowered.startswith("goal ") or "tambah goal" in lowered:
            if "add" in lowered or "tambah" in lowered:
                return NLPUnderstanding(intent="goal_add", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "list" in lowered or "daftar" in lowered:
                return NLPUnderstanding(intent="goal_list", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "focus" in lowered:
                return NLPUnderstanding(intent="goal_focus", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "archive" in lowered or "arsip" in lowered:
                return NLPUnderstanding(intent="goal_archive", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "done" in lowered or "selesai" in lowered or "complete" in lowered:
                return NLPUnderstanding(intent="goal_done", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "unblock" in lowered or "buka blokir" in lowered:
                return NLPUnderstanding(intent="goal_unblock", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "block" in lowered or "blok" in lowered:
                return NLPUnderstanding(intent="goal_block", confidence=0.9, source="keyword_flexible", raw_text=text)
            if "next" in lowered or "run" in lowered:
                return NLPUnderstanding(intent="goal_next", confidence=0.9, source="keyword_flexible", raw_text=text)
            return NLPUnderstanding(intent="goal_list", confidence=0.9, source="keyword_flexible", raw_text=text)
        return None

    async def _get_llm_understanding(self, text: str, provider: str):
        provider = str(provider or "").strip().lower()
        if not text:
            return None
        system, user = self._nlu_prompt(text)
        try:
            if provider == "openai" and self.openai_client is not None:
                model = self._resolve_model(provider, "nlu")
                response = await self.openai_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **self._completion_options(provider, deterministic=True),
                )
                content = str(response.choices[0].message.content or "")
                return self._parse_llm_json(content, text)
            if provider == "groq" and self.groq_client is not None:
                model = self._resolve_model(provider, "nlu")
                response = await self.groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **self._completion_options(provider, deterministic=True),
                )
                content = str(response.choices[0].message.content or "")
                return self._parse_llm_json(content, text)
            if provider == "gemini" and self.gemini_model is not None:
                prompt = f"{system}\n{user}"
                result = await asyncio.to_thread(self.gemini_model.generate_content, prompt)
                content = str(getattr(result, "text", "") or "")
                return self._parse_llm_json(content, text)
        except Exception:
            return None
        return None

    async def _generate_tool_calls_with_provider(self, user_msg: str, tools_schema: list[dict], provider: str):
        provider = str(provider or "").lower()
        try:
            if provider == "groq" and self.groq_client is not None:
                model = self._resolve_model(provider, "tool")
                response = await self.groq_client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": user_msg}],
                    tools=tools_schema,
                    tool_choice="auto",
                    **self._completion_options(provider),
                )
                message = response.choices[0].message
            elif provider == "openai" and self.openai_client is not None:
                model = self._resolve_model(provider, "tool")
                response = await self.openai_client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": user_msg}],
                    tools=tools_schema,
                    tool_choice="auto",
                    **self._completion_options(provider),
                )
                message = response.choices[0].message
            else:
                return []
        except Exception:
            return []
        out: list[ToolCall] = []
        for item in getattr(message, "tool_calls", []) or []:
            args = getattr(getattr(item, "function", None), "arguments", "{}")
            try:
                parsed_args = json.loads(args) if isinstance(args, str) else dict(args or {})
            except Exception:
                parsed_args = {}
            out.append(
                ToolCall(
                    name=str(getattr(getattr(item, "function", None), "name", "") or ""),
                    args=parsed_args,
                    call_id=str(getattr(item, "id", "") or ""),
                )
            )
        return out

    async def generate_tool_calls(self, user_msg: str, tools_schema: list[dict]):
        for provider in self._provider_sequence():
            tool_calls = await self._generate_tool_calls_with_provider(user_msg, tools_schema, provider)
            if tool_calls:
                return tool_calls, provider
        return [], ""

    async def understand(self, text: str, user_id: str = "default", tools_schema: list[dict] | None = None):
        tools_schema = list(tools_schema or [])
        if tools_schema:
            cache_key = self._tool_cache_key(user_id, text, tools_schema)
            cached = self.state_manager.get_state(cache_key, user_id=user_id) if self.state_manager else None
            if cached:
                tool_calls = [
                    ToolCall(name=item.get("name", ""), args=dict(item.get("args") or {}), call_id=item.get("call_id", ""))
                    for item in cached
                ]
                return [NLPUnderstanding(intent="tool_call", confidence=1.0, source="cache", raw_text=text, tool_calls=tool_calls)]
            tool_calls, source = await self.generate_tool_calls(text, tools_schema)
            if tool_calls:
                if self.state_manager:
                    self.state_manager.set_state(
                        cache_key,
                        [{"name": item.name, "args": item.args, "call_id": item.call_id} for item in tool_calls],
                        user_id=user_id,
                        ttl=self.tool_cache_ttl_sec,
                    )
                return [NLPUnderstanding(intent="tool_call", confidence=1.0, source=source, raw_text=text, tool_calls=tool_calls)]

        keyword = self._keyword_understanding(text)
        if keyword is not None:
            return [keyword]

        for provider in self._provider_sequence():
            result = await self._get_llm_understanding(text, provider)
            if result:
                if isinstance(result, list):
                    return result
                return [result]
        return [NLPUnderstanding(intent="general_conversation", confidence=0.4, source="fallback", raw_text=text)]

    async def summarize_tool_results(self, user_msg: str, tool_results: list[dict]):
        _ = user_msg
        if not tool_results:
            return ""
        return "\n".join(str(item.get("result") or "") for item in tool_results)

