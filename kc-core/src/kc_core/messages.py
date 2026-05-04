from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class UserMessage:
    content: str


@dataclass(frozen=True)
class AssistantMessage:
    content: str


@dataclass(frozen=True)
class ToolCallMessage:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    content: str


Message = Union[UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage]


def to_openai_dict(m: Message) -> dict[str, Any]:
    if isinstance(m, UserMessage):
        return {"role": "user", "content": m.content}
    if isinstance(m, AssistantMessage):
        return {"role": "assistant", "content": m.content}
    if isinstance(m, ToolCallMessage):
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": m.tool_call_id,
                "type": "function",
                "function": {
                    "name": m.tool_name,
                    "arguments": json.dumps(m.arguments),
                },
            }],
        }
    if isinstance(m, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "content": m.content,
        }
    raise TypeError(f"Unknown message type: {type(m)}")
