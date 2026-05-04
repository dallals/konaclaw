from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Union
from kc_core.messages import AssistantMessage


# ---- Wire-level: what the model client yields ----

@dataclass(frozen=True)
class TextDelta:
    """Chunk of assistant text. May be empty."""
    content: str


@dataclass(frozen=True)
class ToolCallsBlock:
    """A complete set of tool calls emitted by the model in one turn."""
    calls: list[dict[str, Any]]


@dataclass(frozen=True)
class Done:
    """Final frame from the model client. finish_reason is e.g. 'stop' or 'tool_calls'."""
    finish_reason: str


ChatStreamFrame = Union[TextDelta, ToolCallsBlock, Done]


# ---- Agent-level: what Agent.send_stream yields ----

@dataclass(frozen=True)
class TokenDelta:
    """Forwarded text chunk during the model's text generation phase."""
    content: str


@dataclass(frozen=True)
class ToolCallStart:
    """A tool call about to be executed. `call` matches kc-core's tool-call dict shape."""
    call: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """The result of a tool call (or an Error: ... string on failure)."""
    call_id: str
    content: str


@dataclass(frozen=True)
class Complete:
    """Terminal frame. `reply` is the same AssistantMessage that `send` would return."""
    reply: AssistantMessage


StreamFrame = Union[TokenDelta, ToolCallStart, ToolResult, Complete]
