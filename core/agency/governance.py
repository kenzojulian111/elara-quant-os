from __future__ import annotations

from dataclasses import dataclass
import re

from core.agency.goals import Goal
from core.agency.identity_anchor import IdentityAnchor
from core.agency.planner import ActionProposal


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False


class PolicyEngine:
    def __init__(self, anchor: IdentityAnchor):
        self.anchor = anchor

    def check_goal(self, goal: Goal) -> PolicyDecision:
        lowered = str(goal.description or "").lower()
        if not lowered.strip():
            return PolicyDecision(False, "goal_description_empty")
        for keyword in self.anchor.blocked_goal_keywords:
            if keyword and keyword.lower() in lowered:
                return PolicyDecision(False, f"blocked_keyword:{keyword}")
        for pattern in self.anchor.blocked_goal_regex:
            if not pattern:
                continue
            try:
                if re.search(pattern, lowered, flags=re.IGNORECASE):
                    return PolicyDecision(False, f"blocked_regex:{pattern}")
            except re.error:
                continue
        if self.anchor.allowed_domains and goal.domain not in self.anchor.allowed_domains:
            return PolicyDecision(False, f"domain_not_allowed:{goal.domain}")
        if float(goal.confidence) < float(self.anchor.min_goal_confidence):
            return PolicyDecision(False, "goal_confidence_too_low")
        return PolicyDecision(True, "ok")

    def check_action(self, action: ActionProposal, goal: Goal) -> GovernanceDecision:
        action_type = str(action.action_type or "").strip().lower()
        allowlist = {str(item).strip().lower() for item in self.anchor.action_allowlist or [] if str(item).strip()}
        blocklist = {str(item).strip().lower() for item in self.anchor.action_blocklist or [] if str(item).strip()}
        if allowlist and action_type not in allowlist:
            return GovernanceDecision(False, "action_not_allowed", requires_approval=False)
        if action_type in blocklist:
            return GovernanceDecision(False, "action_blocked", requires_approval=False)

        requires_approval = action_type in {str(item).lower() for item in self.anchor.approval_required_actions}
        approval_risk_levels = {
            str(item).strip().lower() for item in self.anchor.approval_required_risk_levels or []
        }
        if action.risk_level in approval_risk_levels:
            requires_approval = True
        if action.requires_approval:
            requires_approval = True

        appetite = str(self.anchor.risk_appetite or "low").lower()
        appetite_limit = _risk_appetite_threshold(appetite)
        if _risk_score(action.risk_level) > appetite_limit:
            return GovernanceDecision(False, "risk_too_high", requires_approval=requires_approval)
        if goal.domain == "trading" and action_type == "trade_live":
            return GovernanceDecision(False, "live_trading_blocked", requires_approval=True)
        return GovernanceDecision(True, "ok", requires_approval=requires_approval)


def _risk_score(level: str) -> float:
    mapping = {
        "low": 0.2,
        "medium": 0.5,
        "high": 0.8,
        "critical": 1.0,
    }
    return mapping.get(str(level or "").strip().lower(), 0.5)


def _risk_appetite_threshold(appetite: str) -> float:
    mapping = {
        "low": 0.4,
        "medium": 0.7,
        "high": 1.0,
    }
    return mapping.get(str(appetite or "").strip().lower(), 0.4)

