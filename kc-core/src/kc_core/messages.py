from __future__ import annotations
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union


@dataclass(frozen=True)
class ImageRef:
    """Path + mime for an image attached to a user turn."""

    path: Path
    mime: str


@dataclass(frozen=True)
class UserMessage:
    content: str
    images: tuple[ImageRef, ...] = ()


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


def _encode_image_data_url(ref: ImageRef) -> str:
    data = ref.path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{ref.mime};base64,{b64}"


def _encode_image_b64(ref: ImageRef) -> str:
    return base64.b64encode(ref.path.read_bytes()).decode("ascii")


def to_openai_dict(m: Message) -> dict[str, Any]:
    """OpenAI-compatible message dict. Multimodal user turns produce a content list."""
    if isinstance(m, UserMessage):
        if not m.images:
            return {"role": "user", "content": m.content}
        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": m.content},
        ]
        for ref in m.images:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": _encode_image_data_url(ref)},
            })
        return {"role": "user", "content": content_blocks}
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


def to_native_dict(m: Message) -> dict[str, Any]:
    """Ollama native /api/chat dict. User turns with images use the `images` field."""
    if isinstance(m, UserMessage) and m.images:
        return {
            "role": "user",
            "content": m.content,
            "images": [_encode_image_b64(r) for r in m.images],
        }
    return to_openai_dict(m)
