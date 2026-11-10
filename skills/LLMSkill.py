from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config.settings import Config
from core.agency.governance import PolicyEngine
from core.agency.identity_anchor import load_identity_anchor, resolve_anchor_paths
from core.agency.planner import ActionProposal
from core.agency.goals import Goal
from core.base import NLPUnderstanding, Skill
from utils import health as health_metrics


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _split_csv(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]+", str(value or "")) if part.strip()]


def _split_name_set(value: Any) -> set[str]:
    return {_normalize_name(part) for part in re.split(r"[|,;]+", str(value or "")) if part.strip()}


def _parse_assignment_map(value: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _split_csv(value):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        out[str(key or "").strip().lower()] = str(raw_value or "").strip()
    return out


def _parse_profile_skill_rules(value: Any) -> dict[str, set[str]]:
    rules: dict[str, set[str]] = {}
    for part in _split_csv(value):
        if "=" not in part:
            continue
        profile, names = part.split("=", 1)
        rules[str(profile or "").strip().lower()] = _split_name_set(names)
    return rules


def _trust_rank(value: str) -> int:
    order = {"restricted": 0, "standard": 1, "trusted": 2}
    return order.get(str(value or "").strip().lower(), 1)


def _normalize_risk_level(value: str) -> str:
    level = str(value or "").strip().lower()
    if level in {"low", "medium", "high", "critical"}:
        return level
    return "low"


log = logging.getLogger("elara.llm_skill")


@dataclass
class _MarkdownDoc:
    name: str
    description: str
    path: str
    body: str
    trust_level: str = "standard"
    tags: list[str] | None = None


class LLMSkill(Skill):
    def __init__(
        self,
        state_manager=None,
        skill_manager=None,
        nlp_engine=None,
        primary_provider: str | None = None,
        db_manager=None,
        markdown_registry=None,
    ):
        self.state_manager = state_manager
        self.skill_manager = skill_manager
        self.nlp_engine = nlp_engine
        self.primary_provider = str(
            primary_provider or getattr(Config, "LLM_PRIMARY_PROVIDER", "groq") or "groq"
        )
        self.db_manager = db_manager
        self._markdown_feedback: list[dict[str, Any]] = []
        self._markdown_registry = None
        if markdown_registry is not None:
            self.set_markdown_registry(markdown_registry)
        recent_limit = max(10, int(getattr(Config, "LLM_MARKDOWN_METRICS_RECENT_LIMIT", 80) or 80))
        self._markdown_events = deque(maxlen=recent_limit)
        self._markdown_counters = {
            "route_attempts": 0,
            "route_hits": 0,
            "policy_filtered": 0,
            "policy_blocked": 0,
            "policy_escalated": 0,
        }
        self._tool_counters = {
            "tool_calls": 0,
            "executed": 0,
            "blocked": 0,
            "invalid_args": 0,
            "requires_approval": 0,
        }
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._anchor_cache = None
        self._anchor_cache_ts = 0.0

    def get_intents(self) -> list[str]:
        if not bool(getattr(Config, "LLM_AGENT_ORCHESTRATOR_ENABLED", False)):
            return []
        return ["general_conversation", "unknown"]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        user_msg = str(getattr(nlu, "raw_text", "") or "")
        confirm_match = re.match(r"^\s*/confirm\s+([a-f0-9]{4,64})\s*$", user_msg.strip().lower())
        if confirm_match:
            return await self._execute_pending_approval(user_id, confirm_match.group(1))
        if user_msg.strip().lower().startswith("/skills"):
            return self._handle_markdown_skill_meta_command(user_msg, user_id=user_id)

        self._append_history(user_id, "user", user_msg)
        tool_results: list[dict[str, Any]] = []
        pending_approvals: list[Any] = []

        markdown_context = ""
        markdown_info: dict[str, Any] = {"allowed": False, "reason": "no_match"}
        markdown_route = None
        if bool(getattr(Config, "LLM_MARKDOWN_ROUTER_ENABLED", False)) and bool(
            getattr(Config, "LLM_MARKDOWN_SKILLS_ENABLED", False)
        ):
            try:
                markdown_route = self._route_markdown_skill(user_msg, user_id=user_id, include_candidates=True)
            except TypeError:
                markdown_route = self._route_markdown_skill(user_msg, include_candidates=True)
            if markdown_route:
                if bool(getattr(Config, "LLM_MARKDOWN_POLICY_ENABLED", False)):
                    markdown_context, markdown_info = self._build_markdown_skill_context(
                        user_msg,
                        user_id=user_id,
                        route=markdown_route,
                    )
                else:
                    doc = markdown_route.get("doc")
                    score_value = int(markdown_route.get("adjusted_score") or markdown_route.get("score") or 0)
                    min_score = int(getattr(Config, "LLM_MARKDOWN_ROUTER_MIN_SCORE", 0) or 0)
                    if min_score and score_value < min_score:
                        markdown_info = {
                            "allowed": False,
                            "reason": "score_below_min",
                            "doc_name": getattr(doc, "name", ""),
                            "score": score_value,
                            "matched_by": markdown_route.get("matched_by"),
                        }
                    else:
                        markdown_context = (
                            f"My-skill aktif: {getattr(doc, 'name', '')}\n"
                            f"Deskripsi: {getattr(doc, 'description', '')}\n"
                            f"Isi:\n{str(getattr(doc, 'body', '') or '').strip()}"
                        ).strip()
                        markdown_info = {
                            "allowed": True,
                            "reason": "policy_disabled",
                            "doc_name": getattr(doc, "name", ""),
                            "score": score_value,
                            "matched_by": markdown_route.get("matched_by"),
                        }
            self._record_markdown_event(
                user_id=user_id,
                user_msg=user_msg,
                route=markdown_route,
                info=markdown_info,
            )
            if markdown_route and markdown_info and not markdown_info.get("allowed"):
                reason = str(markdown_info.get("reason") or "")
                if reason and reason not in {"no_match", "score_below_min"}:
                    response = f"My-skill diblokir oleh policy ({reason})."
                    self._append_history(user_id, "assistant", response)
                    return response

        planning_text = user_msg
        if markdown_context:
            planning_text = f"{markdown_context}\n\nUser: {user_msg}".strip()

        total_calls = 0
        max_loops = max(1, int(getattr(Config, "LLM_AGENT_MAX_LOOPS", 3) or 3))
        max_calls = max(1, int(getattr(Config, "LLM_AGENT_MAX_TOTAL_TOOL_CALLS", 8) or 8))

        pending_tool_calls = (
            list(getattr(nlu, "tool_calls", []) or [])
            if str(getattr(nlu, "intent", "") or "") == "tool_call"
            else []
        )
        direct_tool_mode = bool(pending_tool_calls)

        for _ in range(max_loops):
            if not pending_tool_calls:
                planner = getattr(self.nlp_engine, "generate_tool_calls", None)
                if planner is None:
                    break
                generated, _provider = await planner(
                    planning_text,
                    self._get_tools_schema(),
                )
                pending_tool_calls = list(generated or [])
            if not pending_tool_calls:
                break

            current_calls = pending_tool_calls
            pending_tool_calls = []
            for call in current_calls:
                if total_calls >= max_calls:
                    break
                total_calls += 1
                name = str(getattr(call, "name", "") or "")
                args = dict(getattr(call, "args", {}) or {})

                self._tool_counters["tool_calls"] += 1
                health_metrics.record_llm_tool_call()

                validation_error = self._validate_tool_args(name, args)
                if validation_error:
                    self._tool_counters["invalid_args"] += 1
                    health_metrics.record_llm_tool_invalid_args()
                    tool_results.append({"name": name, "args": args, "result": validation_error})
                    continue

                allowed, reason = self._is_tool_allowed(name)
                if not allowed:
                    self._tool_counters["blocked"] += 1
                    health_metrics.record_llm_tool_blocked()
                    tool_results.append({"name": name, "args": args, "result": reason})
                    continue

                gov_allowed, requires_approval, gov_reason, risk_level = self._check_governance(
                    name, args, user_msg, user_id
                )
                if not gov_allowed:
                    self._tool_counters["blocked"] += 1
                    health_metrics.record_llm_tool_blocked()
                    tool_results.append(
                        {"name": name, "args": args, "result": f"blocked: {gov_reason}"}
                    )
                    continue

                if requires_approval:
                    if bool(getattr(Config, "LLM_AGENT_APPROVAL_ENABLED", True)):
                        self._tool_counters["requires_approval"] += 1
                        health_metrics.record_llm_tool_requires_approval()
                        pending_approvals.append(call)
                        tool_results.append(
                            {"name": name, "args": args, "result": "requires approval"}
                        )
                        continue
                    self._tool_counters["blocked"] += 1
                    health_metrics.record_llm_tool_blocked()
                    tool_results.append(
                        {"name": name, "args": args, "result": "blocked: approval_required"}
                    )
                    continue

                result = await self.skill_manager.execute_tool(name, args, user_id=user_id)
                self._tool_counters["executed"] += 1
                health_metrics.record_llm_tool_executed()
                self._increment_daily_budget(self._load_anchor(), user_id, risk_level)
                tool_results.append({"name": name, "args": args, "result": result})
            if direct_tool_mode:
                break
            if total_calls >= max_calls:
                break

        if not tool_results and pending_approvals:
            tool_results.append({"name": "approval", "args": {}, "result": "requires approval"})

        if not tool_results and bool(getattr(Config, "LLM_AGENT_CHAT_ENABLED", False)):
            response = await self._generate_chat_response(user_msg, markdown_context)
            self._append_history(user_id, "assistant", response)
            return response

        summary = await self._summarize_results(planning_text, tool_results)
        if pending_approvals and bool(getattr(Config, "LLM_AGENT_APPROVAL_ENABLED", True)):
            token = self._store_pending_approval(
                user_id,
                {"tool_calls": pending_approvals, "user_msg": user_msg},
            )
            summary = (summary + f"\n\nButuh approval. Jalankan /confirm {token}").strip()

        self._append_history(user_id, "assistant", summary)
        return summary

    def _get_tools_schema(self) -> list[dict[str, Any]]:
        if self.skill_manager and hasattr(self.skill_manager, "get_all_tools_schema"):
            return list(self.skill_manager.get_all_tools_schema() or [])
        return []

    def _critical_tool_names(self) -> set[str]:
        return _split_name_set(getattr(Config, "CRITICAL_CONFIRM_TOOLS", ""))

    def _tool_allowlist(self) -> set[str]:
        return _split_name_set(getattr(Config, "LLM_AGENT_TOOL_ALLOWLIST", ""))

    def _tool_denylist(self) -> set[str]:
        return _split_name_set(getattr(Config, "LLM_AGENT_TOOL_DENYLIST", ""))

    def _is_tool_allowed(self, name: str) -> tuple[bool, str]:
        normalized = _normalize_name(name)
        allowlist = self._tool_allowlist()
        denylist = self._tool_denylist()
        if allowlist and normalized not in allowlist:
            return False, f"blocked: {name} tidak ada di allowlist"
        if normalized in denylist:
            return False, f"blocked: {name} ada di denylist"
        allow_critical = bool(getattr(Config, "LLM_AGENT_ALLOW_CRITICAL_TOOLS", False))
        if not allow_critical and normalized in self._critical_tool_names():
            return False, f"blocked: {name} termasuk critical tool"
        return True, ""

    def _tool_schema_by_name(self, name: str) -> dict[str, Any]:
        if not self.skill_manager or not hasattr(self.skill_manager, "get_all_tools_schema"):
            return {}
        for row in self.skill_manager.get_all_tools_schema() or []:
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

    def _make_confirm_token(self) -> str:
        token_bytes = max(2, int(getattr(Config, "LLM_AGENT_APPROVAL_TOKEN_BYTES", 3) or 3))
        return secrets.token_hex(token_bytes)

    def _store_pending_approval(self, user_id: str, payload: dict[str, Any]) -> str:
        token = self._make_confirm_token()
        ttl = max(30, int(getattr(Config, "LLM_AGENT_APPROVAL_TTL_SEC", 180) or 180))
        self._pending_approvals[user_id] = {
            "token": token,
            "payload": payload,
            "expires_at": time.time() + ttl,
        }
        return token

    def _load_pending_approval(self, user_id: str):
        row = self._pending_approvals.get(user_id)
        if not row:
            return None
        if float(row.get("expires_at", 0.0) or 0.0) <= time.time():
            self._pending_approvals.pop(user_id, None)
            return None
        return row

    async def _execute_pending_approval(self, user_id: str, token: str) -> str:
        row = self._load_pending_approval(user_id)
        if not row:
            return "Token approval salah atau sudah kadaluarsa."
        if not secrets.compare_digest(str(row.get("token") or ""), str(token or "").strip().lower()):
            return "Token approval salah atau sudah kadaluarsa."

        payload = dict(row.get("payload") or {})
        self._pending_approvals.pop(user_id, None)
        tool_calls = payload.get("tool_calls") or []
        if not tool_calls:
            return "Aksi approval tidak valid."
        tool_results: list[dict[str, Any]] = []
        user_msg = str(payload.get("user_msg") or "")
        for call in tool_calls:
            name = str(getattr(call, "name", "") or "")
            args = dict(getattr(call, "args", {}) or {})
            self._tool_counters["tool_calls"] += 1
            health_metrics.record_llm_tool_call()
            validation_error = self._validate_tool_args(name, args)
            if validation_error:
                self._tool_counters["invalid_args"] += 1
                health_metrics.record_llm_tool_invalid_args()
                tool_results.append({"name": name, "args": args, "result": validation_error})
                continue
            allowed, reason = self._is_tool_allowed(name)
            if not allowed:
                self._tool_counters["blocked"] += 1
                health_metrics.record_llm_tool_blocked()
                tool_results.append({"name": name, "args": args, "result": reason})
                continue
            gov_allowed, _requires_approval, gov_reason, risk_level = self._check_governance(
                name, args, user_msg, user_id
            )
            if not gov_allowed:
                self._tool_counters["blocked"] += 1
                health_metrics.record_llm_tool_blocked()
                tool_results.append({"name": name, "args": args, "result": f"blocked: {gov_reason}"})
                continue
            result = await self.skill_manager.execute_tool(name, args, user_id=user_id)
            self._tool_counters["executed"] += 1
            health_metrics.record_llm_tool_executed()
            self._increment_daily_budget(self._load_anchor(), user_id, risk_level)
            tool_results.append({"name": name, "args": args, "result": result})
        summary = await self._summarize_results(user_msg, tool_results)
        self._append_history(user_id, "assistant", summary)
        return summary

    def _load_anchor(self):
        refresh_sec = max(5, int(getattr(Config, "LLM_AGENT_ANCHOR_REFRESH_SEC", 30) or 30))
        now = time.time()
        if self._anchor_cache is not None and (now - self._anchor_cache_ts) < refresh_sec:
            return self._anchor_cache
        runtime_path, fallback_path = resolve_anchor_paths(getattr(Config, "root_path", os.getcwd()))
        anchor = load_identity_anchor(runtime_path, fallback_path)
        self._anchor_cache = anchor
        self._anchor_cache_ts = now
        return anchor

    def _action_type_overrides(self) -> dict[str, str]:
        return _parse_assignment_map(getattr(Config, "LLM_AGENT_ACTION_TYPE_OVERRIDES", ""))

    def _risk_overrides(self) -> dict[str, str]:
        return _parse_assignment_map(getattr(Config, "LLM_AGENT_TOOL_RISK_OVERRIDES", ""))

    def _domain_overrides(self) -> dict[str, str]:
        return _parse_assignment_map(getattr(Config, "LLM_AGENT_TOOL_DOMAIN_OVERRIDES", ""))

    def _resolve_action_type(self, tool_name: str) -> str:
        normalized = _normalize_name(tool_name)
        override = self._action_type_overrides().get(normalized)
        return str(override or normalized or "tool").strip().lower()

    def _resolve_risk_level(self, tool_name: str) -> str:
        normalized = _normalize_name(tool_name)
        override = self._risk_overrides().get(normalized)
        if override:
            return _normalize_risk_level(override)
        if normalized in self._critical_tool_names():
            return "high"
        return _normalize_risk_level(getattr(Config, "LLM_AGENT_DEFAULT_RISK_LEVEL", "low") or "low")

    def _resolve_domain(self, tool_name: str, user_msg: str) -> str:
        normalized = _normalize_name(tool_name)
        override = self._domain_overrides().get(normalized)
        if override:
            return str(override).strip().lower()
        text = f"{normalized} {user_msg}".lower()
        if any(token in text for token in ("trade", "trading", "portfolio", "order", "position", "btc", "crypto")):
            return "trading"
        if any(token in text for token in ("weather", "cuaca", "calendar", "schedule", "jadwal")):
            return "productivity"
        return "system"

    def _check_daily_budget(self, anchor, user_id: str, risk_level: str) -> tuple[bool, str]:
        budgets = dict(getattr(anchor, "daily_action_budget", {}) or {})
        if not budgets or not self.state_manager:
            return True, ""
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"llm_action_budget:{day_key}"
        data = dict(self.state_manager.get_state(key, user_id=user_id) or {})
        used = int(data.get(risk_level, 0) or 0)
        limit = int(budgets.get(risk_level, 0) or 0)
        if limit > 0 and used >= limit:
            return False, "daily_action_budget_exceeded"
        return True, ""

    def _increment_daily_budget(self, anchor, user_id: str, risk_level: str) -> None:
        budgets = dict(getattr(anchor, "daily_action_budget", {}) or {})
        if not budgets or not self.state_manager:
            return
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"llm_action_budget:{day_key}"
        data = dict(self.state_manager.get_state(key, user_id=user_id) or {})
        data[risk_level] = int(data.get(risk_level, 0) or 0) + 1
        self.state_manager.set_state(key, data, user_id=user_id)

    def _check_governance(self, tool_name: str, args: dict[str, Any], user_msg: str, user_id: str) -> tuple[bool, bool, str, str]:
        if not bool(getattr(Config, "LLM_AGENT_GOVERNANCE_ENABLED", True)):
            return True, False, "ok", "low"
        anchor = self._load_anchor()
        action_type = self._resolve_action_type(tool_name)
        risk_level = self._resolve_risk_level(tool_name)
        domain = self._resolve_domain(tool_name, user_msg)
        goal = Goal(goal_id=f"llm-{uuid4().hex}", description=user_msg, domain=domain, confidence=1.0)
        action = ActionProposal(
            action_type=action_type,
            tool_name=tool_name,
            args=args,
            reason="llm_tool_call",
            risk_level=risk_level,
            requires_approval=bool(getattr(Config, "LLM_AGENT_REQUIRE_APPROVAL", False)),
        )
        decision = PolicyEngine(anchor).check_action(action, goal)
        requires_approval = bool(decision.requires_approval or action.requires_approval)

        allowed = bool(decision.allowed)
        reason = str(decision.reason or "ok")
        if not allowed and reason == "risk_too_high" and bool(getattr(Config, "LLM_AGENT_ALLOW_RISK_OVERRIDE", True)):
            allowed = True
            requires_approval = True

        if allowed:
            budget_ok, budget_reason = self._check_daily_budget(anchor, user_id, risk_level)
            if not budget_ok:
                return False, False, budget_reason, risk_level
            return True, requires_approval, reason, risk_level
        return False, False, reason, risk_level

    async def _summarize_results(self, user_msg: str, tool_results: list[dict[str, Any]]) -> str:
        summarizer = getattr(self.nlp_engine, "summarize_tool_results", None)
        if summarizer is not None:
            summary = await summarizer(user_msg, tool_results)
            if summary:
                return summary
        if not tool_results:
            return "Tidak ada tool yang dijalankan."
        names = ",".join(str(item.get("name") or "") for item in tool_results)
        return f"summary:{names}"

    async def _generate_chat_response(self, user_msg: str, context: str = "") -> str:
        nlp = self.nlp_engine
        if nlp is None:
            return "LLM chat provider belum tersedia."
        prompt = str(user_msg or "")
        if context:
            prompt = f"{context}\n\nUser: {prompt}".strip()

        primary = str(getattr(Config, "LLM_PRIMARY_PROVIDER", "") or "").strip().lower()
        provider_order: list[str] = []
        for provider in (primary, "gemini", "groq", "openai"):
            if provider and provider not in provider_order:
                provider_order.append(provider)

        chat_override = str(getattr(Config, "LLM_CHAT_MODEL", "") or "").strip()

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
                result = await asyncio.to_thread(model.generate_content, prompt)
                text = str(getattr(result, "text", "") or "").strip()
                if text:
                    return text
            if provider == "groq":
                client = getattr(nlp, "groq_client", None)
                if client is None:
                    continue
                model = chat_override or str(
                    getattr(Config, "GROQ_MODEL", "llama-3.1-8b-instant") or "llama-3.1-8b-instant"
                )
                response = await client.chat.completions.create(
                    model=model,
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
                model = chat_override or str(getattr(Config, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini")
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                try:
                    text = str(response.choices[0].message.content or "").strip()
                except Exception:
                    text = ""
                if text:
                    return text
        return "Tidak ada respon chat dari provider."

    def _append_history(self, user_id: str, role: str, content: str) -> None:
        if not self.state_manager:
            return
        history = list(self.state_manager.get_state("llm_history", user_id=user_id) or [])
        item_max_chars = max(80, int(getattr(Config, "LLM_HISTORY_ITEM_MAX_CHARS", 900) or 900))
        text = str(content or "")
        if len(text) > item_max_chars:
            text = text[:item_max_chars].rstrip() + "..."
        history.append({"role": str(role or ""), "content": text, "ts": time.time()})

        max_turns = max(1, int(getattr(Config, "LLM_HISTORY_TURNS", 14) or 14))
        max_chars = max(200, int(getattr(Config, "LLM_HISTORY_MAX_CHARS", 7000) or 7000))
        max_tokens = max(60, int(getattr(Config, "LLM_HISTORY_MAX_TOKENS", 4000) or 4000))
        summary_enabled = bool(getattr(Config, "LLM_HISTORY_SUMMARY_ENABLED", True))
        summary_max_chars = max(
            200, int(getattr(Config, "LLM_HISTORY_SUMMARY_MAX_CHARS", 1600) or 1600)
        )

        def _history_chars(rows: list[dict[str, Any]]) -> int:
            return sum(len(str((row or {}).get("content") or "")) for row in rows)

        def _history_tokens(rows: list[dict[str, Any]]) -> int:
            return sum(len(str((row or {}).get("content") or "").split()) for row in rows)

        ttl_sec = int(getattr(Config, "LLM_HISTORY_TTL_SEC", 0) or 0)
        if ttl_sec > 0:
            cutoff = time.time() - ttl_sec
            history = [
                row
                for row in history
                if float((row or {}).get("ts") or time.time()) >= cutoff
            ]

        if summary_enabled:
            while (
                len(history) > (max_turns * 2)
                or _history_chars(history) > max_chars
                or _history_tokens(history) > max_tokens
            ):
                compact_count = min(4, max(1, len(history) - (max_turns * 2)))
                compacted = history[:compact_count]
                history = history[compact_count:]
                prev_summary = str(
                    self.state_manager.get_state("llm_history_summary", user_id=user_id) or ""
                ).strip()
                chunk = " | ".join(
                    f"{str((row or {}).get('role') or '').strip()}:"
                    f"{str((row or {}).get('content') or '').strip()}"
                    for row in compacted
                ).strip()
                merged = (prev_summary + " | " + chunk).strip(" |")
                if len(merged) > summary_max_chars:
                    merged = merged[-summary_max_chars:]
                self.state_manager.set_state("llm_history_summary", merged, user_id=user_id)

        self.state_manager.set_state("llm_history", history, user_id=user_id)

    def _markdown_dirs(self) -> list[str]:
        dirs: list[str] = []
        root = Path(str(getattr(Config, "root_path", os.getcwd()) or os.getcwd()))
        for part in re.split(r"[;,]+", str(getattr(Config, "LLM_MARKDOWN_SKILLS_DIRS", "") or "")):
            text = part.strip()
            if not text:
                continue
            path = Path(text)
            if not path.is_absolute():
                path = (root / path).resolve()
            dirs.append(str(path))
        return dirs

    def _load_markdown_docs(self) -> tuple[list[_MarkdownDoc], list[dict[str, Any]]]:
        if self._markdown_registry is not None and hasattr(self._markdown_registry, "search_scored"):
            sources = []
            try:
                if hasattr(self._markdown_registry, "source_breakdown"):
                    sources = list(self._markdown_registry.source_breakdown() or [])
            except Exception:
                sources = []
            return [], sources
        docs: list[_MarkdownDoc] = []
        sources: list[dict[str, Any]] = []
        default_trust = str(getattr(Config, "LLM_MARKDOWN_DEFAULT_TRUST", "standard") or "standard")
        for base_dir in self._markdown_dirs():
            loaded = 0
            path_obj = Path(base_dir)
            if path_obj.is_dir():
                for skill_md in path_obj.glob("*/SKILL.md"):
                    body = skill_md.read_text(encoding="utf-8")
                    meta: dict[str, str] = {}
                    match = re.search(r"^---\s*\n(.*?)\n---\s*\n?", body, flags=re.S)
                    if match:
                        for line in match.group(1).splitlines():
                            if ":" not in line:
                                continue
                            key, raw_value = line.split(":", 1)
                            meta[key.strip().lower()] = raw_value.strip().strip("'\"")
                    docs.append(
                        _MarkdownDoc(
                            name=str(meta.get("name") or skill_md.parent.name).strip(),
                            description=str(meta.get("description") or ""),
                            path=str(skill_md),
                            body=body,
                            trust_level=str(meta.get("trust_level") or default_trust or "standard")
                            .strip()
                            .lower(),
                            tags=[],
                        )
                    )
                    loaded += 1
            sources.append({"path": base_dir, "exists": path_obj.is_dir(), "loaded": loaded})
        return docs, sources

    def set_markdown_registry(self, registry) -> None:
        self._markdown_registry = registry

    def _feedback_adjustment(self, skill_name: str) -> int:
        feedback_enabled = bool(
            getattr(Config, "LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED", False)
            or getattr(Config, "LLM_MARKDOWN_ROUTER_ENABLED", False)
        )
        if not feedback_enabled:
            return 0
        normalized = _normalize_name(skill_name)
        rows = [row for row in self._markdown_feedback if _normalize_name(row.get("skill_name", "")) == normalized]
        min_samples = max(0, int(getattr(Config, "LLM_MARKDOWN_FEEDBACK_ROUTER_MIN_SAMPLES", 3) or 3))
        if len(rows) < max(1, min_samples):
            return 0
        match_weight = float(getattr(Config, "LLM_MARKDOWN_FEEDBACK_MATCH_WEIGHT", 0.35) or 0.35)
        weighted_score = 0.0
        for row in rows:
            feedback = str(row.get("feedback") or "").strip().lower()
            if feedback not in {"positive", "negative"}:
                continue
            match_kind = str(row.get("match_kind") or "").strip().lower()
            weight = 1.0 if match_kind == "explicit" else match_weight
            weighted_score += weight if feedback == "positive" else -weight
        if weighted_score == 0:
            return 0
        total = max(1.0, float(len(rows)))
        ratio = max(-1.0, min(1.0, weighted_score / total))
        max_boost = max(1, int(getattr(Config, "LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_BOOST", 45) or 45))
        max_penalty = max(1, int(getattr(Config, "LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_PENALTY", 60) or 60))
        if ratio >= 0:
            return int(round(ratio * max_boost))
        return int(round(ratio * max_penalty))

    def _route_markdown_skill(self, user_msg: str, **kwargs) -> dict[str, Any]:
        include_candidates = bool(kwargs.get("include_candidates"))
        top_k = int(kwargs.get("top_k") or getattr(Config, "LLM_MARKDOWN_ROUTER_TOP_K", 8) or 8)
        top_k = max(1, min(top_k, 20))
        if self._markdown_registry is not None and hasattr(self._markdown_registry, "search_scored"):
            best: dict[str, Any] | None = None
            candidates: list[dict[str, Any]] = []
            for doc, raw_score in list(self._markdown_registry.search_scored(user_msg, limit=top_k) or []):
                adjustment = self._feedback_adjustment(getattr(doc, "name", ""))
                item = {
                    "doc": doc,
                    "score": int(raw_score),
                    "matched_by": "search",
                    "feedback_adjustment": adjustment,
                    "adjusted_score": int(raw_score) + adjustment,
                }
                candidates.append(item)
            candidates.sort(key=lambda row: int(row.get("adjusted_score") or 0), reverse=True)
            if candidates:
                best = candidates[0]
            if best is None:
                return {}
            if include_candidates:
                best = dict(best)
                best["candidates"] = candidates
            return best

        docs, _sources = self._load_markdown_docs()
        normalized_msg = _normalize_name(user_msg).replace("_", " ")
        best: dict[str, Any] | None = None
        for doc in docs:
            doc_name = _normalize_name(doc.name)
            score = 0
            matched_by = "search"
            if doc_name and doc_name.replace("_", " ") in normalized_msg:
                score = 100
                matched_by = "explicit"
            else:
                haystack = f"{doc.name} {doc.description} {doc.body}".lower()
                for term in normalized_msg.split():
                    if term and term in haystack:
                        score += 1
            if score <= 0:
                continue
            candidate = {
                "doc": doc,
                "score": score,
                "matched_by": matched_by,
                "feedback_adjustment": self._feedback_adjustment(getattr(doc, "name", "")),
            }
            candidate["adjusted_score"] = int(candidate["score"]) + int(candidate["feedback_adjustment"])
            if best is None or int(candidate["adjusted_score"]) > int(best["adjusted_score"]):
                best = candidate
        return best or {}

    def _resolve_user_policy_context(self, user_id: str) -> dict[str, Any]:
        identity = str(user_id or "").strip().lower()
        role = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_USER_ROLE_OVERRIDES", "")).get(
            identity, ""
        )
        if not role and self.db_manager and hasattr(self.db_manager, "get_user_role"):
            role = str(self.db_manager.get_user_role(identity) or "").strip().lower()
        tenant = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_USER_TENANT_OVERRIDES", "")).get(
            identity, ""
        )

        profile = str(getattr(Config, "LLM_MARKDOWN_SKILLS_PROFILE", "default") or "default").strip().lower()
        profile = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_USER_PROFILE_OVERRIDES", "")).get(
            identity, profile
        )
        if role:
            profile = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES", "")).get(
                role, profile
            )
        if tenant:
            profile = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_TENANT_PROFILE_OVERRIDES", "")).get(
                tenant, profile
            )

        min_trust = str(getattr(Config, "LLM_MARKDOWN_MIN_TRUST", "standard") or "standard").strip().lower()
        min_trust = _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES", "")).get(
            identity, min_trust
        )
        if role:
            min_trust = _parse_assignment_map(
                getattr(Config, "LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES", "")
            ).get(role, min_trust)
        if tenant:
            min_trust = _parse_assignment_map(
                getattr(Config, "LLM_MARKDOWN_TENANT_MIN_TRUST_OVERRIDES", "")
            ).get(tenant, min_trust)

        blocked = _split_name_set(getattr(Config, "LLM_MARKDOWN_BLOCKED_SKILLS", ""))
        blocked |= _split_name_set(
            _parse_assignment_map(getattr(Config, "LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES", "")).get(
                identity, ""
            )
        )
        if role:
            blocked |= _split_name_set(
                _parse_assignment_map(
                    getattr(Config, "LLM_MARKDOWN_ROLE_BLOCKED_SKILLS_OVERRIDES", "")
                ).get(role, "")
            )
        if tenant:
            blocked |= _split_name_set(
                _parse_assignment_map(
                    getattr(Config, "LLM_MARKDOWN_TENANT_BLOCKED_SKILLS_OVERRIDES", "")
                ).get(tenant, "")
            )

        return {
            "identity": identity,
            "role": role,
            "tenant": tenant,
            "profile": profile,
            "min_trust": min_trust,
            "blocked_skills": blocked,
        }

    def _is_escalation_requested(self, user_msg: str, policy: dict[str, Any]) -> bool:
        if not bool(getattr(Config, "LLM_MARKDOWN_ESCALATION_ENABLED", False)):
            return False
        role = str(policy.get("role") or "").strip().lower()
        allowed_roles = {part.strip().lower() for part in _split_csv(getattr(Config, "LLM_MARKDOWN_ESCALATION_ROLES", ""))}
        if allowed_roles and role not in allowed_roles:
            return False
        text = str(user_msg or "").strip().lower()
        phrases = [part.strip().lower() for part in _split_csv(getattr(Config, "LLM_MARKDOWN_ESCALATION_PHRASES", ""))]
        return any(phrase and phrase in text for phrase in phrases)

    def _has_suspicious_body(self, body: str) -> bool:
        if not bool(getattr(Config, "LLM_MARKDOWN_PROMPT_GUARD_ENABLED", False)):
            return False
        if not bool(getattr(Config, "LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY", False)):
            return False
        lowered = str(body or "").lower()
        return any(
            marker in lowered
            for marker in (
                "ignore previous instructions",
                "system prompt",
                "override policy",
                "do not reveal",
            )
        )

    def _build_markdown_skill_context(
        self,
        user_msg: str,
        user_id: str = "default",
        route: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if route is None:
            try:
                route = self._route_markdown_skill(user_msg, user_id=user_id)
            except TypeError:
                route = self._route_markdown_skill(user_msg)
        if not route:
            return "", {"allowed": False, "reason": "no_match"}
        doc = route.get("doc")
        if doc is None:
            return "", {"allowed": False, "reason": "no_doc"}

        min_score = int(getattr(Config, "LLM_MARKDOWN_ROUTER_MIN_SCORE", 0) or 0)
        score_value = int(route.get("adjusted_score") or route.get("score") or 0)
        if min_score and score_value < min_score:
            return "", {"allowed": False, "reason": "score_below_min", "score": score_value}

        policy = self._resolve_user_policy_context(user_id)
        info = {
            "allowed": False,
            "policy_profile": policy.get("profile"),
            "policy_role": policy.get("role"),
            "policy_tenant": policy.get("tenant"),
            "policy_min_trust": policy.get("min_trust"),
            "doc_name": getattr(doc, "name", ""),
            "score": score_value,
            "matched_by": route.get("matched_by"),
        }
        doc_name = _normalize_name(getattr(doc, "name", ""))
        if doc_name in set(policy.get("blocked_skills") or set()):
            info["reason"] = "skill diblokir oleh policy"
            return "", info

        profile_rules = _parse_profile_skill_rules(getattr(Config, "LLM_MARKDOWN_PROFILE_SKILL_RULES", ""))
        allowed_by_profile = set(profile_rules.get(str(policy.get("profile") or "").lower(), set()))
        escalated = self._is_escalation_requested(user_msg, policy)
        if allowed_by_profile and doc_name not in allowed_by_profile and not escalated:
            info["reason"] = "skill tidak diizinkan oleh profile"
            return "", info

        doc_trust = str(getattr(doc, "trust_level", "standard") or "standard").strip().lower()
        if _trust_rank(doc_trust) < _trust_rank(str(policy.get("min_trust") or "standard")) and not escalated:
            info["reason"] = "trust di bawah minimum"
            return "", info

        if self._has_suspicious_body(str(getattr(doc, "body", "") or "")) and not escalated:
            info["reason"] = "body terindikasi suspicious"
            return "", info

        info["allowed"] = True
        info["escalated"] = bool(escalated)
        info["reason"] = "escalated policy bypass" if escalated else "allowed"
        context = (
            f"My-skill aktif: {getattr(doc, 'name', '')}\n"
            f"Deskripsi: {getattr(doc, 'description', '')}\n"
            f"Isi:\n{str(getattr(doc, 'body', '') or '').strip()}"
        ).strip()
        return context, info

    def _markdown_policy_info_for_doc(
        self,
        doc: Any,
        *,
        user_id: str,
        user_msg: str = "",
        score: int = 0,
        matched_by: str = "",
    ) -> dict[str, Any]:
        policy = self._resolve_user_policy_context(user_id)
        info = {
            "allowed": False,
            "policy_profile": policy.get("profile"),
            "policy_role": policy.get("role"),
            "policy_tenant": policy.get("tenant"),
            "policy_min_trust": policy.get("min_trust"),
            "doc_name": getattr(doc, "name", ""),
            "score": int(score or 0),
            "matched_by": matched_by,
        }
        doc_name = _normalize_name(getattr(doc, "name", ""))
        if doc_name in set(policy.get("blocked_skills") or set()):
            info["reason"] = "skill diblokir oleh policy"
            return info

        profile_rules = _parse_profile_skill_rules(getattr(Config, "LLM_MARKDOWN_PROFILE_SKILL_RULES", ""))
        allowed_by_profile = set(profile_rules.get(str(policy.get("profile") or "").lower(), set()))
        escalated = self._is_escalation_requested(user_msg, policy)
        if allowed_by_profile and doc_name not in allowed_by_profile and not escalated:
            info["reason"] = "skill tidak diizinkan oleh profile"
            return info

        doc_trust = str(getattr(doc, "trust_level", "standard") or "standard").strip().lower()
        if _trust_rank(doc_trust) < _trust_rank(str(policy.get("min_trust") or "standard")) and not escalated:
            info["reason"] = "trust di bawah minimum"
            return info

        if self._has_suspicious_body(str(getattr(doc, "body", "") or "")) and not escalated:
            info["reason"] = "body terindikasi suspicious"
            return info

        info["allowed"] = True
        info["escalated"] = bool(escalated)
        info["reason"] = "escalated policy bypass" if escalated else "allowed"
        return info

    def _handle_markdown_skill_meta_command(self, command: str, user_id: str = "default") -> str:
        normalized = str(command or "").strip().lower()
        policy = self._resolve_user_policy_context(user_id)
        if normalized.startswith("/skills policy"):
            blocked_count = len(set(policy.get("blocked_skills") or set()))
            return (
                f"Identity: {policy.get('identity')}\n"
                f"Role: {policy.get('role') or '-'}\n"
                f"Profile: {policy.get('profile')}\n"
                f"Min_trust: {policy.get('min_trust')}\n"
                f"Blocked_skills: {blocked_count}"
            )
        if normalized.startswith("/skills sources"):
            _docs, sources = self._load_markdown_docs()
            lines = ["Sumber my-skill:"]
            for source in sources:
                lines.append(
                    f"- path={source.get('path')} exists={source.get('exists')} loaded={source.get('loaded')}"
                )
            return "\n".join(lines)
        return "Perintah meta my-skill tidak dikenali."

    def _record_markdown_event(
        self,
        *,
        user_id: str,
        user_msg: str,
        route: dict[str, Any] | None,
        info: dict[str, Any],
    ) -> None:
        if route is None:
            route = {}
            info = dict(info or {})
            info.setdefault("allowed", False)
            info.setdefault("reason", "no_match")
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user_id or ""),
            "query": str(user_msg or "")[:240],
            "skill": str(getattr(route.get("doc"), "name", "") or ""),
            "score": int(route.get("adjusted_score") or route.get("score") or 0),
            "matched_by": str(route.get("matched_by") or ""),
            "allowed": bool(info.get("allowed")),
            "reason": str(info.get("reason") or ""),
            "escalated": bool(info.get("escalated")),
        }
        self._markdown_events.append(event)
        self._markdown_counters["route_attempts"] += 1
        health_metrics.record_llm_markdown_route_attempt()
        if info.get("allowed"):
            self._markdown_counters["route_hits"] += 1
            health_metrics.record_llm_markdown_route_hit()
            if info.get("escalated"):
                self._markdown_counters["policy_escalated"] += 1
                health_metrics.record_llm_markdown_policy_escalated()
        else:
            reason = str(info.get("reason") or "")
            if reason and reason not in {"no_match", "score_below_min"}:
                self._markdown_counters["policy_blocked"] += 1
                health_metrics.record_llm_markdown_policy_blocked()
            else:
                self._markdown_counters["policy_filtered"] += 1
                health_metrics.record_llm_markdown_policy_filtered()

    def submit_markdown_route_feedback(
        self,
        *,
        user_id: str,
        user_msg: str,
        feedback: str,
        note: str = "",
        skill_name: str = "",
        match_kind: str = "",
    ) -> dict[str, Any]:
        route = self._route_markdown_skill(user_msg, user_id=user_id)
        entry = {
            "user_id": str(user_id or ""),
            "user_msg": str(user_msg or ""),
            "feedback": str(feedback or "").strip().lower(),
            "note": str(note or ""),
            "skill_name": str(
                skill_name or getattr(route.get("doc"), "name", "") or ""
            ),
            "match_kind": str(match_kind or route.get("matched_by") or ""),
        }
        self._markdown_feedback.append(entry)
        return {"recorded": True, "entry": entry}

    def get_markdown_skill_evaluation(self, top_n: int = 5, include_recent: bool = False) -> dict[str, Any]:
        rows = list(self._markdown_feedback)
        positive = sum(1 for row in rows if str(row.get("feedback") or "") == "positive")
        negative = sum(1 for row in rows if str(row.get("feedback") or "") == "negative")
        report = {
            "feedback": {
                "total": len(rows),
                "positive": positive,
                "negative": negative,
            }
        }
        if include_recent:
            report["recent_feedback"] = rows[-max(1, int(top_n or 5)) :]
        return report

    def reset_markdown_route_feedback(self) -> dict[str, Any]:
        previous_total = len(self._markdown_feedback)
        self._markdown_feedback = []
        return {"reset": True, "previous_total": previous_total}

    def get_markdown_skill_analytics(self) -> dict[str, Any]:
        enabled = bool(getattr(Config, "LLM_MARKDOWN_ROUTER_ENABLED", False)) and bool(
            getattr(Config, "LLM_MARKDOWN_SKILLS_ENABLED", False)
        )
        payload = {
            "enabled": enabled,
            "route_attempts": int(self._markdown_counters.get("route_attempts", 0)),
            "route_hits": int(self._markdown_counters.get("route_hits", 0)),
            "policy_filtered": int(self._markdown_counters.get("policy_filtered", 0)),
            "policy_blocked": int(self._markdown_counters.get("policy_blocked", 0)),
            "policy_escalated": int(self._markdown_counters.get("policy_escalated", 0)),
            "tool_calls": int(self._tool_counters.get("tool_calls", 0)),
            "tool_executed": int(self._tool_counters.get("executed", 0)),
            "tool_blocked": int(self._tool_counters.get("blocked", 0)),
            "tool_invalid_args": int(self._tool_counters.get("invalid_args", 0)),
            "tool_requires_approval": int(self._tool_counters.get("requires_approval", 0)),
            "recent_events": list(self._markdown_events),
        }
        return payload

    def get_markdown_skill_catalog(
        self,
        *,
        user_id: str = "default",
        query: str = "",
        limit: int = 30,
    ) -> dict[str, Any]:
        enabled = bool(getattr(Config, "LLM_MARKDOWN_SKILLS_ENABLED", False))
        if not enabled:
            return {"enabled": False, "available": False, "count": 0, "items": []}
        max_items = max(1, int(limit or 30))
        items: list[dict[str, Any]] = []

        if self._markdown_registry is not None and hasattr(self._markdown_registry, "search_scored"):
            if query:
                scored = list(self._markdown_registry.search_scored(query, limit=max_items) or [])
            else:
                names = list(self._markdown_registry.list_names() or [])[:max_items]
                scored = [(self._markdown_registry.get(name), 0) for name in names]
            for doc, raw_score in scored:
                if doc is None:
                    continue
                info = (
                    self._markdown_policy_info_for_doc(
                        doc,
                        user_id=user_id,
                        user_msg=query or str(getattr(doc, "name", "") or ""),
                        score=int(raw_score or 0),
                        matched_by="search" if query else "list",
                    )
                    if bool(getattr(Config, "LLM_MARKDOWN_POLICY_ENABLED", False))
                    else {"allowed": True, "reason": "policy_disabled"}
                )
                items.append(
                    {
                        "name": str(getattr(doc, "name", "") or ""),
                        "description": str(getattr(doc, "description", "") or ""),
                        "path": str(getattr(doc, "path", "") or ""),
                        "trust_level": str(getattr(doc, "trust_level", "") or ""),
                        "tags": list(getattr(doc, "tags", []) or []),
                        "score": int(raw_score or 0),
                        "policy": info,
                    }
                )
        else:
            docs, _sources = self._load_markdown_docs()
            for doc in docs:
                if query and query.lower() not in str(doc.name or "").lower():
                    continue
                info = (
                    self._markdown_policy_info_for_doc(
                        doc,
                        user_id=user_id,
                        user_msg=query or str(getattr(doc, "name", "") or ""),
                        score=0,
                        matched_by="list",
                    )
                    if bool(getattr(Config, "LLM_MARKDOWN_POLICY_ENABLED", False))
                    else {"allowed": True, "reason": "policy_disabled"}
                )
                items.append(
                    {
                        "name": str(doc.name or ""),
                        "description": str(doc.description or ""),
                        "path": str(doc.path or ""),
                        "trust_level": str(doc.trust_level or ""),
                        "tags": list(doc.tags or []),
                        "score": 0,
                        "policy": info,
                    }
                )
                if len(items) >= max_items:
                    break

        return {
            "enabled": True,
            "available": True,
            "count": len(items),
            "items": items,
        }

    def preview_markdown_route(self, query: str, user_id: str = "default", top_k: int = 6) -> dict[str, Any]:
        enabled = bool(getattr(Config, "LLM_MARKDOWN_ROUTER_ENABLED", False))
        if not enabled:
            return {"enabled": False, "available": False, "matched": False}

        max_items = max(1, min(int(top_k or 6), 20))
        min_score = int(getattr(Config, "LLM_MARKDOWN_ROUTER_MIN_SCORE", 0) or 0)

        route = None
        candidates: list[dict[str, Any]] = []
        if self._markdown_registry is not None and hasattr(self._markdown_registry, "search_scored"):
            for doc, raw_score in list(self._markdown_registry.search_scored(query, limit=max_items) or []):
                adjustment = self._feedback_adjustment(getattr(doc, "name", ""))
                item = {
                    "doc": doc,
                    "score": int(raw_score),
                    "matched_by": "search",
                    "feedback_adjustment": adjustment,
                    "adjusted_score": int(raw_score) + adjustment,
                }
                candidates.append(item)
            if candidates:
                route = max(candidates, key=lambda row: int(row.get("adjusted_score") or 0))
        else:
            route = self._route_markdown_skill(query, include_candidates=True, top_k=max_items)
            candidates = list(route.get("candidates") or []) if route else []

        serialized_candidates = []
        for item in candidates:
            doc = item.get("doc") if isinstance(item, dict) else None
            serialized_candidates.append(
                {
                    "name": str(getattr(doc, "name", "") or ""),
                    "description": str(getattr(doc, "description", "") or ""),
                    "path": str(getattr(doc, "path", "") or ""),
                    "trust_level": str(getattr(doc, "trust_level", "") or ""),
                    "tags": list(getattr(doc, "tags", []) or []),
                    "score": int(item.get("score", 0) or 0) if isinstance(item, dict) else 0,
                    "adjusted_score": int(item.get("adjusted_score", 0) or 0)
                    if isinstance(item, dict)
                    else 0,
                    "matched_by": str(item.get("matched_by") or "") if isinstance(item, dict) else "",
                    "feedback_adjustment": int(item.get("feedback_adjustment", 0) or 0)
                    if isinstance(item, dict)
                    else 0,
                }
            )

        if not route or not route.get("doc"):
            return {"enabled": True, "available": True, "matched": False, "candidates": serialized_candidates}

        score_value = int(route.get("adjusted_score") or route.get("score") or 0)
        if min_score and score_value < min_score:
            return {
                "enabled": True,
                "available": True,
                "matched": False,
                "reason": "score_below_min",
                "candidates": candidates,
            }

        doc = route.get("doc")
        policy = (
            self._markdown_policy_info_for_doc(
                doc,
                user_id=user_id,
                user_msg=query,
                score=score_value,
                matched_by=str(route.get("matched_by") or ""),
            )
            if bool(getattr(Config, "LLM_MARKDOWN_POLICY_ENABLED", False))
            else {"allowed": True, "reason": "policy_disabled"}
        )
        return {
            "enabled": True,
            "available": True,
            "matched": bool(policy.get("allowed")),
            "score": score_value,
            "matched_by": str(route.get("matched_by") or ""),
            "skill": {
                "name": str(getattr(doc, "name", "") or ""),
                "description": str(getattr(doc, "description", "") or ""),
                "path": str(getattr(doc, "path", "") or ""),
                "trust_level": str(getattr(doc, "trust_level", "") or ""),
                "tags": list(getattr(doc, "tags", []) or []),
            },
            "policy": policy,
            "candidates": serialized_candidates,
        }

