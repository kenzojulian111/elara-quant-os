from __future__ import annotations

import asyncio
import inspect
import re
import secrets
import time
from typing import Any

from flask import has_app_context

from config.settings import Config


def _split_csv(value: Any) -> set[str]:
    return {str(part or "").strip().lower() for part in str(value or "").split(",") if str(part).strip()}


class ElaraSkill:
    def __init__(self, nlp_engine, skill_manager, state_manager, db_manager=None):
        self.nlp_engine = nlp_engine
        self.skill_manager = skill_manager
        self.state_manager = state_manager
        self.db_manager = db_manager
        self._pending_confirms: dict[str, dict[str, Any]] = {}

    def _critical_tools(self) -> set[str]:
        return _split_csv(getattr(Config, "CRITICAL_CONFIRM_TOOLS", "flatten_all_positions"))

    def _critical_intents(self) -> set[str]:
        return _split_csv(getattr(Config, "CRITICAL_CONFIRM_INTENTS", ""))

    def _looks_prompt_injection(self, text: str) -> bool:
        lowered = str(text or "").lower()
        markers = ("ignore previous instructions", "system prompt", "override safety", "force skill")
        return any(marker in lowered for marker in markers)

    def _make_confirm_token(self) -> str:
        token_bytes = max(2, int(getattr(Config, "CRITICAL_CONFIRM_TOKEN_BYTES", 3) or 3))
        return secrets.token_hex(token_bytes)

    def _store_pending_confirmation(self, user_id: str, payload: dict[str, Any]) -> str:
        token = self._make_confirm_token()
        ttl = max(30, int(getattr(Config, "CRITICAL_CONFIRM_TTL_SEC", 120) or 120))
        self._pending_confirms[user_id] = {
            "token": token,
            "payload": payload,
            "expires_at": time.time() + ttl,
        }
        return token

    def _load_pending_confirmation(self, user_id: str):
        row = self._pending_confirms.get(user_id)
        if not row:
            return None
        if float(row.get("expires_at", 0.0) or 0.0) <= time.time():
            self._pending_confirms.pop(user_id, None)
            return None
        return row

    def _log_command(self, source: str, nlu, user_id: str) -> None:
        if not self.db_manager or not hasattr(self.db_manager, "log_command"):
            return
        if not has_app_context():
            return
        entities = []
        for entity in list(getattr(nlu, "entities", []) or []):
            entities.append(
                {
                    "type": getattr(entity, "type", None),
                    "value": getattr(entity, "value", None),
                    "span": getattr(entity, "span", None),
                }
            )
        self.db_manager.log_command(
            source=source,
            intent=str(getattr(nlu, "intent", "") or ""),
            text=str(getattr(nlu, "raw_text", "") or ""),
            entities=entities,
            user_id=user_id,
        )

    def _tool_schema_by_name(self, name: str) -> dict[str, Any]:
        for row in self.skill_manager.get_all_tools_schema():
            function = row.get("function", {})
            if str(function.get("name") or "").strip().lower() == str(name or "").strip().lower():
                return function.get("parameters", {}) or {}
        return {}

    def _validate_tool_args(self, tool_name: str, args: dict[str, Any]) -> str:
        schema = self._tool_schema_by_name(tool_name)
        if not schema:
            return ""
        props = dict(schema.get("properties") or {})
        required = [str(item) for item in schema.get("required") or []]
        additional = schema.get("additionalProperties", True)
        for field in required:
            if field not in args:
                return f"invalid args: missing '{field}'"
        if additional is False:
            for field in args:
                if field not in props:
                    return f"invalid args: unexpected '{field}'"
        for field, spec in props.items():
            if field not in args:
                continue
            value = args.get(field)
            if spec.get("enum") and value not in spec.get("enum"):
                return f"invalid args: '{field}' di luar enum"
            expected_type = str(spec.get("type") or "").lower()
            if expected_type == "boolean" and not isinstance(value, bool):
                return f"invalid args: '{field}' harus boolean"
            if expected_type == "string" and not isinstance(value, str):
                return f"invalid args: '{field}' harus string"
            if expected_type == "number" and not isinstance(value, (int, float)):
                return f"invalid args: '{field}' harus number"
        return ""

    async def _run_skill_handle(self, skill, nlu, user_id: str):
        result = skill.handle(nlu, user_id=user_id)
        if inspect.isawaitable(result):
            return await result
        return await asyncio.to_thread(lambda: result)

    async def _execute_tool_calls(self, tool_calls, user_id: str, user_msg: str):
        tool_results = []
        for tool_call in tool_calls:
            name = str(getattr(tool_call, "name", "") or "")
            args = dict(getattr(tool_call, "args", {}) or {})
            validation_error = self._validate_tool_args(name, args)
            if validation_error:
                return validation_error
            result = await self.skill_manager.execute_tool(name, args, user_id=user_id)
            tool_results.append({"name": name, "args": args, "result": result})

        if hasattr(self.nlp_engine, "summarize_tool_results"):
            return await self.nlp_engine.summarize_tool_results(user_msg, tool_results)
        return "\n".join(str(item.get("result") or "") for item in tool_results)

    async def _execute_pending_confirmation(self, user_id: str, token: str):
        row = self._load_pending_confirmation(user_id)
        if not row:
            return "Token konfirmasi salah atau sudah kadaluarsa."
        if not secrets.compare_digest(str(row.get("token") or ""), str(token or "").strip().lower()):
            return "Token konfirmasi salah atau sudah kadaluarsa."

        payload = dict(row.get("payload") or {})
        self._pending_confirms.pop(user_id, None)
        if payload.get("type") == "tool_calls":
            return await self._execute_tool_calls(payload.get("tool_calls") or [], user_id, payload.get("user_msg") or "")
        if payload.get("type") == "intent":
            skill = payload.get("skill")
            nlu = payload.get("nlu")
            if skill is None or nlu is None:
                return "Aksi konfirmasi tidak valid."
            return await self._run_skill_handle(skill, nlu, user_id)
        return "Aksi konfirmasi tidak valid."

    async def handle_command(self, text: str, user_id: str = "default", source: str = "core"):
        text = str(text or "")
        match = re.match(r"^\s*/confirm\s+([a-f0-9]{4,32})\s*$", text.strip().lower())
        if match:
            return await self._execute_pending_confirmation(user_id, match.group(1))

        tools_schema = self.skill_manager.get_all_tools_schema() if hasattr(self.skill_manager, "get_all_tools_schema") else []
        try:
            nlu_list = await self.nlp_engine.understand(text, user_id=user_id, tools_schema=tools_schema)
        except TypeError as exc:
            if "tools_schema" not in str(exc):
                raise
            nlu_list = await self.nlp_engine.understand(text, user_id=user_id)
        nlu = (nlu_list or [None])[0]
        if nlu is None:
            return "Aku belum paham perintah itu."

        self._log_command(source, nlu, user_id)

        if bool(getattr(Config, "PROMPT_INJECTION_GUARD_ENABLED", True)) and self._looks_prompt_injection(text):
            source_name = str(getattr(nlu, "source", "") or "").lower()
            if source_name and not source_name.startswith("keyword") and (
                str(getattr(nlu, "intent", "") or "").lower() in self._critical_intents()
                or any(str(getattr(call, "name", "") or "").lower() in self._critical_tools() for call in getattr(nlu, "tool_calls", []) or [])
            ):
                return "Blocked by prompt-injection guardrail."

        tool_calls = list(getattr(nlu, "tool_calls", []) or [])
        if tool_calls:
            critical_tool = next(
                (call for call in tool_calls if str(getattr(call, "name", "") or "").lower() in self._critical_tools()),
                None,
            )
            if critical_tool and bool(getattr(Config, "CRITICAL_CONFIRM_ENABLED", True)):
                token = self._store_pending_confirmation(
                    user_id,
                    {"type": "tool_calls", "tool_calls": tool_calls, "user_msg": text},
                )
                return f"Perintah kritikal butuh konfirmasi. Jalankan /confirm {token}"
            return await self._execute_tool_calls(tool_calls, user_id, text)

        intent = str(getattr(nlu, "intent", "") or "")
        skill = getattr(self.skill_manager, "intent_map", {}).get(intent) or getattr(self.skill_manager, "intent_map", {}).get(intent.lower())
        if skill is None and hasattr(self.skill_manager, "handle"):
            return await self.skill_manager.handle(nlu, user_id=user_id)
        if skill is None:
            skill = getattr(self.skill_manager, "fallback_skill", None)
        if skill is None:
            return "Skill tidak ditemukan."

        if bool(getattr(Config, "CRITICAL_CONFIRM_ENABLED", True)) and intent.lower() in self._critical_intents():
            token = self._store_pending_confirmation(user_id, {"type": "intent", "skill": skill, "nlu": nlu})
            return f"Perintah kritikal butuh konfirmasi. Jalankan /confirm {token}"

        return await self._run_skill_handle(skill, nlu, user_id)

