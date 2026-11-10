from __future__ import annotations

import re
from typing import List, Optional

from core.agency.agent_loop import AgentLoop
from core.agency.goal_queue import GoalQueue
from core.agency.goals import GOAL_STATUS_BLOCKED, GoalFactory
from core.agency.governance import PolicyEngine
from core.agency.identity_anchor import load_identity_anchor, resolve_anchor_paths
from core.base import ElaraTool, NLPUnderstanding, Skill


class GoalEngineSkill(Skill):
    def __init__(self, state_manager, root_path: Optional[str] = None, skill_manager=None):
        self.state_manager = state_manager
        self.root_path = str(root_path or "").strip() or None
        self.skill_manager = skill_manager

    def get_intents(self) -> List[str]:
        return [
            "goal_add",
            "goal_list",
            "goal_focus",
            "goal_next",
            "goal_archive",
            "goal_done",
            "goal_block",
            "goal_unblock",
        ]

    def get_tools(self) -> List[ElaraTool]:
        return [
            ElaraTool(
                name="goal_add",
                description="Add a new goal to the goal queue.",
                parameters={
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "domain": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "importance": {"type": "number"},
                        "urgency": {"type": "number"},
                        "impact": {"type": "number"},
                        "cost": {"type": "number"},
                        "risk": {"type": "number"},
                        "effort": {"type": "number"},
                        "confidence": {"type": "number"},
                        "deadline": {"type": "string"},
                        "ttl": {"type": "string"},
                        "preemptible": {"type": "boolean"},
                    },
                    "required": ["description"],
                },
            ),
            ElaraTool(
                name="goal_list",
                description="List current goals with priority scores.",
                parameters={"type": "object", "properties": {}},
            ),
            ElaraTool(
                name="goal_focus",
                description="Focus on a specific goal by ID.",
                parameters={
                    "type": "object",
                    "properties": {"goal_id": {"type": "string"}},
                    "required": ["goal_id"],
                },
            ),
            ElaraTool(
                name="goal_next",
                description="Generate next suggested plan for the top goal.",
                parameters={"type": "object", "properties": {}},
            ),
            ElaraTool(
                name="goal_archive",
                description="Archive a goal by ID.",
                parameters={
                    "type": "object",
                    "properties": {"goal_id": {"type": "string"}},
                    "required": ["goal_id"],
                },
            ),
            ElaraTool(
                name="goal_done",
                description="Mark a goal as done by ID.",
                parameters={
                    "type": "object",
                    "properties": {"goal_id": {"type": "string"}},
                    "required": ["goal_id"],
                },
            ),
            ElaraTool(
                name="goal_block",
                description="Block a goal by ID.",
                parameters={
                    "type": "object",
                    "properties": {"goal_id": {"type": "string"}},
                    "required": ["goal_id"],
                },
            ),
            ElaraTool(
                name="goal_unblock",
                description="Unblock a goal by ID.",
                parameters={
                    "type": "object",
                    "properties": {"goal_id": {"type": "string"}},
                    "required": ["goal_id"],
                },
            ),
        ]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        text = str(getattr(nlu, "raw_text", "") or "")
        intent = str(getattr(nlu, "intent", "") or "")
        queue = GoalQueue(self.state_manager, user_id=user_id)
        anchor = self._load_anchor()
        policy = PolicyEngine(anchor)

        if intent == "goal_add":
            payload = _parse_goal_payload(text)
            if not payload:
                return "Format: /goal add <deskripsi goal> (opsional: domain:system tags:a,b urgency:0.7 ttl:2d)"
            if anchor.goal_ttl_default_sec and not payload.get("ttl_sec"):
                payload["ttl_sec"] = int(anchor.goal_ttl_default_sec)
            goal = GoalFactory.create(
                payload["description"],
                domain=payload.get("domain", "system"),
                tags=payload.get("tags", []),
                importance=payload.get("importance", 0.7),
                urgency=payload.get("urgency", 0.5),
                impact=payload.get("impact", 0.7),
                cost=payload.get("cost", 0.5),
                risk=payload.get("risk", 0.3),
                effort=payload.get("effort", 0.4),
                confidence=payload.get("confidence", 0.6),
                source=str(user_id or "user"),
                deadline=payload.get("deadline"),
                ttl_sec=payload.get("ttl_sec"),
                preemptible=payload.get("preemptible", True),
            )
            if not goal.description:
                return "Deskripsi goal kosong."
            decision = policy.check_goal(goal)
            if not decision.allowed:
                try:
                    goal.mark_status(GOAL_STATUS_BLOCKED)
                except Exception:
                    goal.status = GOAL_STATUS_BLOCKED
                queue.add(goal, anchor=anchor)
                return f"Goal diblokir oleh policy ({decision.reason}). id={goal.goal_id}"
            queue.add(goal, anchor=anchor)
            return f"Goal ditambahkan: {goal.description} (id={goal.goal_id}, score={goal.priority_score:.2f})"

        if intent == "goal_list":
            goals = queue.list()
            if not goals:
                return "Belum ada goal aktif."
            lines = ["Goal Queue:"]
            queue.refresh_scores(anchor.scoring_weights or {})
            for goal in sorted(goals, key=lambda item: float(item.priority_score), reverse=True):
                meta = []
                if goal.deadline:
                    meta.append(f"deadline={goal.deadline}")
                if goal.ttl_sec:
                    meta.append(f"ttl={goal.ttl_sec}s")
                if goal.tags:
                    meta.append(f"tags={','.join(goal.tags)}")
                meta_text = f" | {'; '.join(meta)}" if meta else ""
                lines.append(
                    f"- {goal.goal_id} | {goal.status} | {goal.domain} | "
                    f"score={goal.priority_score:.2f} | {goal.description}{meta_text}"
                )
            return "\n".join(lines)

        if intent == "goal_focus":
            goal_id = _extract_goal_id(text)
            if not goal_id:
                return "Format: /goal focus <goal_id>"
            if queue.set_active(goal_id):
                return f"Goal aktif diset ke {goal_id}"
            return "Goal tidak ditemukan."

        if intent == "goal_archive":
            goal_id = _extract_goal_id(text)
            if not goal_id:
                return "Format: /goal archive <goal_id>"
            if queue.archive(goal_id):
                return f"Goal {goal_id} diarsipkan."
            return "Goal tidak ditemukan."

        if intent == "goal_done":
            goal_id = _extract_goal_id(text)
            if not goal_id:
                return "Format: /goal done <goal_id>"
            if queue.set_done(goal_id):
                return f"Goal {goal_id} ditandai selesai."
            return "Goal tidak ditemukan."

        if intent == "goal_block":
            goal_id = _extract_goal_id(text)
            if not goal_id:
                return "Format: /goal block <goal_id>"
            if queue.block(goal_id):
                return f"Goal {goal_id} diblokir."
            return "Goal tidak ditemukan."

        if intent == "goal_unblock":
            goal_id = _extract_goal_id(text)
            if not goal_id:
                return "Format: /goal unblock <goal_id>"
            if queue.unblock(goal_id):
                return f"Goal {goal_id} di-unblock."
            return "Goal tidak ditemukan."

        if intent == "goal_next":
            tool_inventory = self._available_tools()
            suggestion = AgentLoop(queue, anchor, self.state_manager, user_id=user_id).run_once(tool_inventory)
            if suggestion is None:
                return "Tidak ada goal aktif untuk dijalankan."
            steps = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(suggestion.plan.steps))
            actions = _format_actions(suggestion.allowed_actions)
            blocked = _format_blocked(suggestion.blocked_actions)
            context = _format_context(suggestion.context)
            return "\n".join(filter(None, [suggestion.summary, steps, actions, blocked, context]))

        return "Perintah goal tidak dikenali."

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        nlu = NLPUnderstanding(intent=_normalize_intent(name), confidence=1.0, raw_text=str(args or ""))
        if name == "goal_add":
            nlu.raw_text = _build_goal_add_text(args)
        if name in {"goal_focus", "goal_archive", "goal_done", "goal_block", "goal_unblock"}:
            nlu.raw_text = f"/goal {name.split('_')[1]} {args.get('goal_id')}"
        return await self.handle(nlu, user_id=user_id)

    def _available_tools(self) -> set[str]:
        if not self.skill_manager or not hasattr(self.skill_manager, "get_all_tools_schema"):
            return set()
        tools = self.skill_manager.get_all_tools_schema() or []
        return {str(item.get("function", {}).get("name") or "").strip() for item in tools}

    def _load_anchor(self):
        runtime_path, fallback_path = resolve_anchor_paths(self.root_path)
        return load_identity_anchor(runtime_path, fallback_path)


def _normalize_intent(name: str) -> str:
    mapping = {
        "goal_add": "goal_add",
        "goal_list": "goal_list",
        "goal_focus": "goal_focus",
        "goal_next": "goal_next",
        "goal_archive": "goal_archive",
        "goal_done": "goal_done",
        "goal_block": "goal_block",
        "goal_unblock": "goal_unblock",
    }
    return mapping.get(str(name or "").strip().lower(), "goal_list")


def _parse_goal_payload(text: str) -> Optional[dict]:
    match = re.search(r"(?:goal\s+add|tambah\s+goal)\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"^/goal\s+add\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return None
    payload_raw = str(match.group(1) or "").strip()
    description = _strip_kv_fields(payload_raw)
    if not description:
        return None
    payload = {"description": description}
    payload.update(_extract_kv(payload_raw))
    return payload


def _extract_goal_id(text: str) -> str:
    match = re.search(r"([a-f0-9]{8,64})", str(text or ""), flags=re.IGNORECASE)
    return str(match.group(1) or "").strip() if match else ""


def _extract_kv(text: str) -> dict:
    payload: dict[str, object] = {}
    for key, cast in (
        ("importance", float),
        ("urgency", float),
        ("impact", float),
        ("cost", float),
        ("risk", float),
        ("effort", float),
        ("confidence", float),
    ):
        value = _find_number(text, key)
        if value is not None:
            payload[key] = cast(value)

    domain = _find_word(text, "domain")
    if domain:
        payload["domain"] = domain

    tags = _find_list(text, "tags")
    if tags:
        payload["tags"] = tags

    deadline = _find_deadline(text, "deadline")
    if deadline:
        payload["deadline"] = deadline

    ttl_sec = _find_duration_sec(text, "ttl")
    if ttl_sec is not None:
        payload["ttl_sec"] = int(ttl_sec)

    preemptible = _find_word(text, "preemptible")
    if preemptible:
        payload["preemptible"] = preemptible.lower() not in {"false", "0", "no"}

    return payload


def _strip_kv_fields(text: str) -> str:
    cleaned = re.sub(r"\b\w+:\s*[^\s]+", "", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _find_number(text: str, key: str) -> Optional[float]:
    match = re.search(rf"{re.escape(key)}\s*:\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _find_word(text: str, key: str) -> Optional[str]:
    match = re.search(rf"{re.escape(key)}\s*:\s*([a-zA-Z0-9_\-]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return str(match.group(1) or "").strip()


def _find_deadline(text: str, key: str) -> Optional[str]:
    match = re.search(rf"{re.escape(key)}\s*:\s*([0-9Tt:\-+Zz\.]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return str(match.group(1) or "").strip()


def _find_list(text: str, key: str) -> List[str]:
    match = re.search(rf"{re.escape(key)}\s*:\s*([a-zA-Z0-9_,\-]+)", text, flags=re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _find_duration_sec(text: str, key: str) -> Optional[int]:
    match = re.search(
        rf"{re.escape(key)}\s*:\s*([0-9]+(?:\.[0-9]+)?)([smhdw]?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = str(match.group(2) or "").lower()
    multiplier = {
        "": 1,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }.get(unit, 1)
    return int(value * multiplier)


def _build_goal_add_text(args: dict) -> str:
    if not isinstance(args, dict):
        return "/goal add "
    desc = str(args.get("description") or "").strip()
    parts = []
    for key in ("domain", "importance", "urgency", "impact", "cost", "risk", "effort", "confidence", "deadline", "ttl"):
        if key in args and args.get(key) is not None:
            parts.append(f"{key}:{args.get(key)}")
    tags = args.get("tags")
    if isinstance(tags, (list, tuple)):
        tags_text = ",".join(str(tag).strip() for tag in tags if str(tag).strip())
        if tags_text:
            parts.append(f"tags:{tags_text}")
    preemptible = args.get("preemptible")
    if preemptible is not None:
        parts.append(f"preemptible:{str(preemptible).lower()}")
    suffix = (" " + " ".join(parts)) if parts else ""
    return f"/goal add {desc}{suffix}"


def _format_actions(actions) -> str:
    if not actions:
        return ""
    lines = ["Aksi disarankan:"]
    for action in actions:
        tool = f"tool={action.tool_name}" if action.tool_name else "tool=manual"
        lines.append(f"- {action.action_type} ({tool}, risk={action.risk_level})")
    return "\n".join(lines)


def _format_blocked(blocked) -> str:
    if not blocked:
        return ""
    lines = ["Aksi diblokir:"]
    for action, decision in blocked:
        lines.append(f"- {action.action_type} (reason={decision.reason})")
    return "\n".join(lines)


def _format_context(context) -> str:
    if not context.system and not context.task and not context.evidence:
        return ""
    return "\n".join(
        [
            "Context Pack:",
            context.system,
            context.task,
            context.evidence,
        ]
    )

