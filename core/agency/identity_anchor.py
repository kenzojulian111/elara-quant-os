from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


DEFAULT_ANCHOR = {
    "version": "1.1",
    "owner": "default",
    "core_goals": [
        "stabilize daily productivity",
        "maintain system safety and auditability",
    ],
    "non_negotiables": [
        "no irreversible actions without explicit confirmation",
        "respect user privacy by default",
    ],
    "blocked_goal_keywords": [
        "delete all",
        "wipe",
        "shutdown entire system",
    ],
    "blocked_goal_regex": [],
    "risk_appetite": "low",
    "approval_required_actions": [
        "trade_live",
        "device_shutdown",
        "delete_data",
    ],
    "approval_required_risk_levels": ["high", "critical"],
    "allowed_domains": [
        "productivity",
        "system",
        "trading",
    ],
    "action_allowlist": [],
    "action_blocklist": [],
    "min_goal_confidence": 0.2,
    "max_active_goals": 1,
    "max_sub_goals": 3,
    "max_goal_queue": 24,
    "max_actions_per_cycle": 5,
    "goal_ttl_default_sec": 0,
    "goal_dedupe_enabled": True,
    "context_budget": {
        "system": 600,
        "task": 1200,
        "evidence": 1200,
    },
    "scoring_weights": {
        "importance": 1.0,
        "urgency": 1.0,
        "impact": 1.0,
        "risk": 0.6,
        "effort": 0.5,
    },
    "preemption": {
        "min_score_delta": 0.15,
        "deadline_hours": 6,
        "risk_override": True,
    },
    "daily_action_budget": {
        "low": 12,
        "medium": 8,
        "high": 4,
    },
}


@dataclass(frozen=True)
class IdentityAnchor:
    version: str = "1.1"
    owner: str = "default"
    core_goals: List[str] = field(default_factory=list)
    non_negotiables: List[str] = field(default_factory=list)
    blocked_goal_keywords: List[str] = field(default_factory=list)
    blocked_goal_regex: List[str] = field(default_factory=list)
    risk_appetite: str = "low"
    approval_required_actions: List[str] = field(default_factory=list)
    approval_required_risk_levels: List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    action_allowlist: List[str] = field(default_factory=list)
    action_blocklist: List[str] = field(default_factory=list)
    min_goal_confidence: float = 0.2
    max_active_goals: int = 1
    max_sub_goals: int = 3
    max_goal_queue: int = 24
    max_actions_per_cycle: int = 5
    goal_ttl_default_sec: int = 0
    goal_dedupe_enabled: bool = True
    context_budget: Dict[str, int] = field(default_factory=dict)
    scoring_weights: Dict[str, float] = field(default_factory=dict)
    preemption: Dict[str, float | bool] = field(default_factory=dict)
    daily_action_budget: Dict[str, int] = field(default_factory=dict)
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IdentityAnchor":
        data = dict(DEFAULT_ANCHOR)
        data.update(payload or {})
        risk_appetite = str(data.get("risk_appetite") or "low").strip().lower()
        if risk_appetite not in {"low", "medium", "high"}:
            risk_appetite = "low"
        return cls(
            version=str(data.get("version") or "1.1"),
            owner=str(data.get("owner") or "default"),
            core_goals=[str(item) for item in data.get("core_goals") or []],
            non_negotiables=[str(item) for item in data.get("non_negotiables") or []],
            blocked_goal_keywords=[str(item) for item in data.get("blocked_goal_keywords") or []],
            blocked_goal_regex=[str(item) for item in data.get("blocked_goal_regex") or []],
            risk_appetite=risk_appetite,
            approval_required_actions=[str(item) for item in data.get("approval_required_actions") or []],
            approval_required_risk_levels=[
                str(item) for item in data.get("approval_required_risk_levels") or []
            ],
            allowed_domains=[str(item) for item in data.get("allowed_domains") or []],
            action_allowlist=[str(item) for item in data.get("action_allowlist") or []],
            action_blocklist=[str(item) for item in data.get("action_blocklist") or []],
            min_goal_confidence=float(data.get("min_goal_confidence") or 0.2),
            max_active_goals=max(1, int(data.get("max_active_goals") or 1)),
            max_sub_goals=max(1, int(data.get("max_sub_goals") or 3)),
            max_goal_queue=max(0, int(data.get("max_goal_queue") or 0)),
            max_actions_per_cycle=max(1, int(data.get("max_actions_per_cycle") or 1)),
            goal_ttl_default_sec=max(0, int(data.get("goal_ttl_default_sec") or 0)),
            goal_dedupe_enabled=_as_bool(data.get("goal_dedupe_enabled", True), True),
            context_budget={k: int(v) for k, v in (data.get("context_budget") or {}).items()},
            scoring_weights={k: float(v) for k, v in (data.get("scoring_weights") or {}).items()},
            preemption=dict(data.get("preemption") or {}),
            daily_action_budget={k: int(v) for k, v in (data.get("daily_action_budget") or {}).items()},
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "owner": self.owner,
            "core_goals": list(self.core_goals),
            "non_negotiables": list(self.non_negotiables),
            "blocked_goal_keywords": list(self.blocked_goal_keywords),
            "blocked_goal_regex": list(self.blocked_goal_regex),
            "risk_appetite": self.risk_appetite,
            "approval_required_actions": list(self.approval_required_actions),
            "approval_required_risk_levels": list(self.approval_required_risk_levels),
            "allowed_domains": list(self.allowed_domains),
            "action_allowlist": list(self.action_allowlist),
            "action_blocklist": list(self.action_blocklist),
            "min_goal_confidence": self.min_goal_confidence,
            "max_active_goals": self.max_active_goals,
            "max_sub_goals": self.max_sub_goals,
            "max_goal_queue": self.max_goal_queue,
            "max_actions_per_cycle": self.max_actions_per_cycle,
            "goal_ttl_default_sec": self.goal_ttl_default_sec,
            "goal_dedupe_enabled": self.goal_dedupe_enabled,
            "context_budget": dict(self.context_budget),
            "scoring_weights": dict(self.scoring_weights),
            "preemption": dict(self.preemption),
            "daily_action_budget": dict(self.daily_action_budget),
            "updated_at": self.updated_at,
        }


def resolve_anchor_paths(root_path: str) -> tuple[str, str]:
    root = str(root_path or "").strip() or os.getcwd()
    runtime_path = os.path.join(root, "runtime", "identity_anchor.json")
    fallback_path = os.path.join(root, "config", "identity_anchor.json")
    return runtime_path, fallback_path


def load_identity_anchor(runtime_path: str, fallback_path: str) -> IdentityAnchor:
    for path in (runtime_path, fallback_path):
        if not path or not os.path.exists(path):
            continue
        data = _safe_json_load(path)
        if data:
            return IdentityAnchor.from_dict(data)
    return IdentityAnchor.from_dict(DEFAULT_ANCHOR)


def _safe_json_load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

