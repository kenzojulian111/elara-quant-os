from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from pathlib import Path
from typing import Any

from config.settings import Config
from utils.system_policy import subsystem_runtime_enabled
try:
    from utils.markdown_skill_registry import MarkdownSkillRegistry
except Exception:
    MarkdownSkillRegistry = None


def _normalize_name(value: str) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _split_csv(value: Any) -> set[str]:
    return {_normalize_name(part) for part in str(value or "").split(",") if str(part).strip()}


def _split_patterns(value: Any) -> list[str]:
    return [part.strip().lower() for part in re.split(r"[;,]+", str(value or "")) if part.strip()]


def _split_paths(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]+", str(value or "")) if part.strip()]


class SkillManager:
    def __init__(self):
        self.skills: dict[str, Any] = {}
        self.intent_map: dict[str, Any] = {}
        self.tool_map: dict[str, tuple[Any, dict[str, Any]]] = {}
        self.fallback_skill = None
        self._skill_enabled: dict[str, bool] = {}
        self._markdown_rows: list[dict[str, Any]] = []
        self._markdown_sources: list[dict[str, Any]] = []
        self._markdown_last_refresh = 0.0
        self._markdown_refresh_count = 0
        self.markdown_registry = None
        self._markdown_registry_error = ""
        self._init_markdown_registry()

    def _init_markdown_registry(self) -> None:
        if MarkdownSkillRegistry is None:
            self._markdown_registry_error = "markdown_registry_unavailable"
            return
        directories = _split_paths(getattr(Config, "LLM_MARKDOWN_SKILLS_DIRS", ""))
        include_patterns = _split_patterns(getattr(Config, "LLM_MARKDOWN_SKILLS_INCLUDE", ""))
        exclude_patterns = _split_patterns(getattr(Config, "LLM_MARKDOWN_SKILLS_EXCLUDE", ""))
        markdown_enabled = subsystem_runtime_enabled("markdown_registry")
        try:
            self.markdown_registry = MarkdownSkillRegistry(
                directories=directories,
                root_path=str(getattr(Config, "root_path", os.getcwd())),
                enabled=markdown_enabled,
                max_skills=int(getattr(Config, "LLM_MARKDOWN_SKILLS_MAX_COUNT", 1200) or 1200),
                max_body_chars=int(getattr(Config, "LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS", 6000) or 6000),
                refresh_sec=int(getattr(Config, "LLM_MARKDOWN_SKILLS_REFRESH_SEC", 30) or 30),
                watch_enabled=markdown_enabled and bool(getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_ENABLED", False)),
                watch_interval_sec=float(
                    getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC", 2.0) or 2.0
                ),
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
            )
        except Exception as e:
            self.markdown_registry = None
            self._markdown_registry_error = str(e or "markdown_registry_init_error")

    def register(self, skill) -> None:
        skill_name = skill.__class__.__name__
        self.skills[skill_name] = skill
        self._skill_enabled.setdefault(_normalize_name(skill_name), True)

        for intent in getattr(skill, "get_intents", lambda: [])() or []:
            self.intent_map[_normalize_name(intent)] = skill

        for tool in getattr(skill, "get_tools", lambda: [])() or []:
            self.tool_map[_normalize_name(tool.name)] = (skill, tool.as_schema())

    def set_skill_enabled(self, skill_name: str, enabled: bool) -> None:
        self._skill_enabled[_normalize_name(skill_name)] = bool(enabled)

    def _is_skill_enabled(self, skill) -> bool:
        skill_name = _normalize_name(skill.__class__.__name__)
        disabled_skills = _split_csv(getattr(Config, "DISABLED_SKILLS", ""))
        return self._skill_enabled.get(skill_name, True) and skill_name not in disabled_skills

    def _is_intent_disabled(self, intent: str) -> bool:
        return _normalize_name(intent) in _split_csv(getattr(Config, "DISABLED_INTENTS", ""))

    async def handle(self, nlu, user_id: str = "default"):
        intent = str(getattr(nlu, "intent", "") or "")
        skill = self.intent_map.get(_normalize_name(intent)) or self.fallback_skill
        if skill is None:
            return "Skill tidak ditemukan."
        if self._is_intent_disabled(intent) or not self._is_skill_enabled(skill):
            return f"Skill {skill.__class__.__name__} sedang dinonaktifkan."
        result = skill.handle(nlu, user_id=user_id)
        if inspect.isawaitable(result):
            return await result
        return await asyncio.to_thread(lambda: result)

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        entry = self.tool_map.get(_normalize_name(name))
        if not entry:
            return f"Tool '{name}' tidak ditemukan."
        skill, _schema = entry
        if not self._is_skill_enabled(skill):
            return f"Skill {skill.__class__.__name__} sedang dinonaktifkan."
        result = skill.execute_tool(name, args or {}, user_id=user_id)
        if inspect.isawaitable(result):
            return await result
        return await asyncio.to_thread(lambda: result)

    def get_all_tools_schema(self) -> list[dict[str, Any]]:
        return [schema for _skill, schema in self.tool_map.values()]

    def _markdown_dirs(self) -> list[str]:
        raw = str(getattr(Config, "LLM_MARKDOWN_SKILLS_DIRS", "") or "")
        dirs: list[str] = []
        for part in re.split(r"[;,]+", raw):
            text = part.strip()
            if not text:
                continue
            path = Path(text)
            if not path.is_absolute():
                path = (Path(getattr(Config, "root_path", os.getcwd())) / path).resolve()
            dirs.append(str(path))
        return dirs

    def _should_refresh_markdown(self) -> bool:
        if not subsystem_runtime_enabled("markdown_registry"):
            return False
        interval = float(getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_INTERVAL_SEC", 2.0) or 2.0)
        return (time.time() - self._markdown_last_refresh) >= max(0.0, interval)

    def _refresh_markdown_registry(self) -> None:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "refresh_if_needed"):
            try:
                self.markdown_registry.refresh_if_needed()
                return
            except Exception:
                pass
        if not self._should_refresh_markdown():
            return
        include = _split_csv(getattr(Config, "LLM_MARKDOWN_SKILLS_INCLUDE", ""))
        exclude = _split_csv(getattr(Config, "LLM_MARKDOWN_SKILLS_EXCLUDE", ""))
        max_body = int(getattr(Config, "LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS", 6000) or 6000)
        rows: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []

        for base_dir in self._markdown_dirs():
            loaded = 0
            exists = os.path.isdir(base_dir)
            if exists:
                for skill_md in Path(base_dir).glob("*/SKILL.md"):
                    name = skill_md.parent.name
                    normalized = _normalize_name(name)
                    if include and normalized not in include:
                        continue
                    if exclude and normalized in exclude:
                        continue
                    try:
                        body = skill_md.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    description = ""
                    frontmatter = re.search(r"^---\s*\n(.*?)\n---\s*\n?", body, flags=re.S)
                    if frontmatter:
                        for line in frontmatter.group(1).splitlines():
                            if ":" not in line:
                                continue
                            key, value = line.split(":", 1)
                            if key.strip().lower() == "description":
                                description = value.strip().strip("'\"")
                    rows.append(
                        {
                            "name": name,
                            "description": description,
                            "path": str(skill_md.parent),
                            "body": body[:max_body],
                        }
                    )
                    loaded += 1
            sources.append({"path": base_dir, "exists": exists, "loaded": loaded})

        self._markdown_rows = rows
        self._markdown_sources = sources
        self._markdown_last_refresh = time.time()
        self._markdown_refresh_count += 1

    def list_markdown_skills(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "list_names"):
            max_items = max(1, int(limit or 20))
            rows: list[dict[str, Any]] = []
            for name in list(self.markdown_registry.list_names() or [])[:max_items]:
                doc = self.markdown_registry.get(name)
                if not doc:
                    continue
                rows.append(
                    {
                        "name": str(getattr(doc, "name", "") or ""),
                        "description": str(getattr(doc, "description", "") or ""),
                        "path": str(getattr(doc, "path", "") or ""),
                        "body": str(getattr(doc, "body", "") or ""),
                    }
                )
            return rows
        self._refresh_markdown_registry()
        return self._markdown_rows[: max(1, int(limit or 20))]

    def search_markdown_skills(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "search_scored"):
            max_items = max(1, int(limit or 10))
            rows: list[dict[str, Any]] = []
            for doc, score in list(self.markdown_registry.search_scored(query, limit=max_items) or []):
                rows.append(
                    {
                        "name": str(getattr(doc, "name", "") or ""),
                        "description": str(getattr(doc, "description", "") or ""),
                        "path": str(getattr(doc, "path", "") or ""),
                        "body": str(getattr(doc, "body", "") or ""),
                        "score": int(score or 0),
                    }
                )
            return rows
        self._refresh_markdown_registry()
        terms = [_normalize_name(part) for part in str(query or "").split() if part.strip()]
        scored: list[dict[str, Any]] = []
        for row in self._markdown_rows:
            haystack = " ".join(
                [str(row.get("name") or ""), str(row.get("description") or ""), str(row.get("body") or "")]
            ).lower()
            score = sum(1 for term in terms if term and term.replace("_", " ") in haystack)
            if score > 0:
                item = dict(row)
                item["score"] = score
                scored.append(item)
        scored.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("name") or "")))
        return scored[: max(1, int(limit or 10))]

    def get_markdown_skill_sources(self) -> list[dict[str, Any]]:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "source_breakdown"):
            return list(self.markdown_registry.source_breakdown() or [])
        self._refresh_markdown_registry()
        return list(self._markdown_sources)

    def get_markdown_registry_stats(self) -> dict[str, Any]:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "stats"):
            stats = dict(self.markdown_registry.stats() or {})
            stats["available"] = bool(stats.get("skill_count", 0))
            stats["count"] = int(stats.get("skill_count", 0) or 0)
            return stats
        self._refresh_markdown_registry()
        markdown_enabled = subsystem_runtime_enabled("markdown_registry")
        return {
            "available": bool(self._markdown_rows),
            "count": len(self._markdown_rows),
            "enabled": markdown_enabled,
            "watch_enabled": markdown_enabled and bool(getattr(Config, "LLM_MARKDOWN_SKILLS_WATCH_ENABLED", False)),
            "refresh_count": self._markdown_refresh_count,
            "last_refresh_ts": self._markdown_last_refresh,
        }

    def refresh_markdown_registry(self, force: bool = True, reason: str = "manual") -> dict[str, Any]:
        if self.markdown_registry is not None and hasattr(self.markdown_registry, "refresh_if_needed"):
            try:
                self.markdown_registry.refresh_if_needed(force=bool(force), reason=str(reason or "manual"))
                stats = dict(self.markdown_registry.stats() or {})
                stats["available"] = bool(stats.get("skill_count", 0))
                stats["count"] = int(stats.get("skill_count", 0) or 0)
                return {"enabled": bool(self.markdown_registry.enabled), "refreshed": True, **stats}
            except Exception as e:
                return {
                    "enabled": subsystem_runtime_enabled("markdown_registry"),
                    "available": False,
                    "refreshed": False,
                    "message": f"refresh_error:{e}",
                }
        self._refresh_markdown_registry()
        return {
            "enabled": subsystem_runtime_enabled("markdown_registry"),
            "available": bool(self._markdown_rows),
            "refreshed": True,
            "count": len(self._markdown_rows),
        }

