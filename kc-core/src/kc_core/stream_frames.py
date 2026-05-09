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


@dataclass(frozen=True)
class ChatUsage:
    """Per-chat_stream-call usage. Yielded after Done.

    `usage_reported=False` means the upstream provider did not include a usable
    `usage` object — caller should treat token counts as unknown but durations
    are still wall-clocked and valid.
    """
    input_tokens: int
    output_tokens: int
    ttfb_ms: float
    generation_ms: float
    usage_reported: bool


ChatStreamFrame = Union[TextDelta, ToolCallsBlock, Done, ChatUsage]


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


@dataclass(frozen=True)
class TurnUsage:
    """Agent-level usage frame, one per inner chat_stream call within a single send_stream.

    `call_index` starts at 0 for the first model call of the turn and increments
    for each subsequent call (multi-step tool-using turns).
    """
    call_index: int
    input_tokens: int
    output_tokens: int
    ttfb_ms: float
    generation_ms: float
    usage_reported: bool


StreamFrame = Union[TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage]
