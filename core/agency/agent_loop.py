from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from core.agency.context_governor import ContextGovernor, ContextPack
from core.agency.goal_queue import GoalQueue
from core.agency.goals import Goal
from core.agency.governance import GovernanceDecision, PolicyEngine
from core.agency.identity_anchor import IdentityAnchor
from core.agency.planner import ActionProposal, DeterministicPlanner, Plan


@dataclass(frozen=True)
class AgentSuggestion:
    goal_id: str
    summary: str
    plan: Plan
    allowed_actions: List[ActionProposal]
    blocked_actions: List[tuple[ActionProposal, GovernanceDecision]]
    context: ContextPack


class AgentLoop:
    def __init__(self, goal_queue: GoalQueue, anchor: IdentityAnchor, state_manager=None, user_id: str = "default"):
        self.goal_queue = goal_queue
        self.anchor = anchor
        self.governor = ContextGovernor()
        self.planner = DeterministicPlanner()
        self.policy = PolicyEngine(anchor)
        self.state_manager = state_manager
        self.user_id = str(user_id or "default")

    def run_once(self, tool_inventory: Optional[set[str]] = None) -> Optional[AgentSuggestion]:
        tool_inventory = {str(name).strip() for name in (tool_inventory or set()) if str(name).strip()}
        self.goal_queue.enforce_anchor(self.anchor)
        goal = self.goal_queue.choose_next(self.anchor)
        if goal is None:
            return None
        decision = self.policy.check_goal(goal)
        if not decision.allowed:
            self.goal_queue.block(goal.goal_id)
            return AgentSuggestion(
                goal_id=goal.goal_id,
                summary=f"Goal blocked by policy ({decision.reason}).",
                plan=Plan(goal_id=goal.goal_id, steps=[], actions=[], confidence=0.0),
                allowed_actions=[],
                blocked_actions=[],
                context=ContextPack(system="", task="", evidence=""),
            )

        goals = self.goal_queue.list()
        notes = self._recent_notes()
        context = self.governor.build_context(anchor=self.anchor, active_goal=goal, queue=goals, recent_notes=notes)
        plan = self.planner.build_plan(goal, tool_inventory)
        allowed, blocked = self._govern_actions(plan.actions, goal, tool_inventory)

        max_actions = int(getattr(self.anchor, "max_actions_per_cycle", 0) or 0)
        if max_actions > 0 and len(allowed) > max_actions:
            overflow = allowed[max_actions:]
            allowed = allowed[:max_actions]
            for action in overflow:
                blocked.append((action, GovernanceDecision(False, "action_budget_exceeded", requires_approval=False)))

        approval_count = sum(1 for action in allowed if action.requires_approval)
        summary = (
            f"Goal aktif: {goal.description}. {len(plan.steps)} langkah, "
            f"{len(allowed)} aksi siap"
            + (f" ({approval_count} butuh approval)" if approval_count else "")
            + "."
        )
        suggestion = AgentSuggestion(
            goal_id=goal.goal_id,
            summary=summary,
            plan=plan,
            allowed_actions=allowed,
            blocked_actions=blocked,
            context=context,
        )
        self._store_suggestion(suggestion)
        return suggestion

    def _govern_actions(
        self, actions: List[ActionProposal], goal: "Goal", tool_inventory: set[str]
    ) -> tuple[List[ActionProposal], List[tuple[ActionProposal, GovernanceDecision]]]:
        allowed: List[ActionProposal] = []
        blocked: List[tuple[ActionProposal, GovernanceDecision]] = []
        for action in actions:
            if action.tool_name and tool_inventory and action.tool_name not in tool_inventory:
                blocked.append((action, GovernanceDecision(False, "tool_unavailable", requires_approval=False)))
                continue
            decision = self.policy.check_action(action, goal)
            if decision.allowed:
                if decision.requires_approval or action.requires_approval:
                    action = ActionProposal(
                        action_type=action.action_type,
                        tool_name=action.tool_name,
                        args=action.args,
                        reason=action.reason,
                        risk_level=action.risk_level,
                        requires_approval=True,
                    )
                allowed.append(action)
            else:
                blocked.append((action, decision))
        return allowed, blocked

    def _recent_notes(self) -> List[str]:
        if not self.state_manager:
            return []
        notes = list(self.state_manager.get_state("goal_episode_notes", user_id=self.user_id) or [])
        return [str(item) for item in notes][-5:]

    def _store_suggestion(self, suggestion: AgentSuggestion) -> None:
        if not self.state_manager:
            return
        payload = {
            "goal_id": suggestion.goal_id,
            "summary": suggestion.summary,
            "steps": list(suggestion.plan.steps),
            "plan_confidence": suggestion.plan.confidence,
            "created_at": str(datetime.now(timezone.utc).isoformat()),
            "actions": [
                {
                    "action_type": action.action_type,
                    "tool_name": action.tool_name,
                    "args": action.args,
                    "reason": action.reason,
                    "risk_level": action.risk_level,
                    "requires_approval": action.requires_approval,
                }
                for action in suggestion.allowed_actions
            ],
            "blocked_actions": [
                {
                    "action_type": action.action_type,
                    "reason": decision.reason,
                    "requires_approval": decision.requires_approval,
                }
                for action, decision in suggestion.blocked_actions
            ],
            "context": {
                "system": suggestion.context.system,
                "task": suggestion.context.task,
                "evidence": suggestion.context.evidence,
            },
        }
        self.state_manager.set_state("agent_last_suggestion", payload, user_id=self.user_id)

