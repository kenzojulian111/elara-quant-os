from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Entity:
    type: str
    value: Any
    span: Any = None


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ElaraTool:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def as_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class NLPUnderstanding:
    intent: str
    confidence: float
    entities: list[Entity] = field(default_factory=list)
    response: str = ""
    source: str = ""
    raw_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    def entities_as_dict(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.entities]


class Skill(abc.ABC):
    def get_intents(self) -> list[str]:
        return []

    def get_tools(self) -> list[ElaraTool]:
        return []

    @abc.abstractmethod
    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        raise NotImplementedError

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        _ = name, args, user_id
        return f"Tool '{name}' tidak didukung."

