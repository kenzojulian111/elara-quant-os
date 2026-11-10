from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.agency.goals import Goal


@dataclass(frozen=True)
class ActionProposal:
    action_type: str
    tool_name: Optional[str]
    args: dict
    reason: str
    risk_level: str = "low"
    requires_approval: bool = False


@dataclass(frozen=True)
class Plan:
    goal_id: str
    steps: List[str]
    actions: List[ActionProposal]
    confidence: float = 0.5


class DeterministicPlanner:
    def build_plan(self, goal: Goal, available_tools: Optional[set[str]] = None) -> Plan:
        description = str(goal.description or "").strip()
        steps = [f"Validate context for goal: {description}"]
        actions: List[ActionProposal] = []

        toolset = {str(name).strip() for name in (available_tools or set()) if str(name).strip()}

        if _contains_any(description, ("jadwal", "schedule", "meeting", "calendar")):
            steps.append("Check upcoming calendar constraints.")
            tool = _pick_tool(toolset, {"get_schedule", "calendar_get", "get_calendar"})
            actions.append(
                ActionProposal(
                    action_type="review_calendar",
                    tool_name=tool,
                    args={},
                    reason="Goal references scheduling.",
                    risk_level="low",
                )
            )

        if _contains_any(description, ("trade", "trading", "portfolio", "btc", "crypto")):
            steps.append("Review trading status before proposing actions.")
            tool = _pick_tool(toolset, {"get_trading_status"})
            actions.append(
                ActionProposal(
                    action_type="review_trading_status",
                    tool_name=tool,
                    args={},
                    reason="Trading-related goal.",
                    risk_level="low",
                )
            )

        if _contains_any(description, ("cuaca", "weather")):
            steps.append("Check weather context for external constraints.")
            tool = _pick_tool(toolset, {"get_weather"})
            actions.append(
                ActionProposal(
                    action_type="review_weather",
                    tool_name=tool,
                    args={},
                    reason="Weather-related goal.",
                    risk_level="low",
                )
            )

        if not actions:
            steps.append("Collect missing context from user.")
            actions.append(
                ActionProposal(
                    action_type="ask_user",
                    tool_name=None,
                    args={"prompt": "Apa detail tambahan yang kamu inginkan untuk goal ini?"},
                    reason="No deterministic tool match found.",
                    risk_level="low",
                )
            )

        steps.append("Prepare execution proposal for approval.")
        confidence = max(0.2, min(0.85, float(goal.confidence)))
        return Plan(goal_id=goal.goal_id, steps=steps, actions=actions, confidence=confidence)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def _pick_tool(toolset: set[str], candidates: set[str]) -> Optional[str]:
    for name in candidates:
        if name in toolset:
            return name
    return None

