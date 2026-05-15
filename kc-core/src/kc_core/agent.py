from __future__ import annotations
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Protocol
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
    Message, to_openai_dict,
)
from kc_core.tools import ToolRegistry
from kc_core.tool_call_parser import parse_text_tool_calls
from kc_core.stream_frames import (
    ChatStreamFrame, TextDelta, ReasoningDelta, ToolCallsBlock, Done, ChatUsage,
    StreamFrame, TokenDelta, ReasoningTokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage,
)


PermissionCheck = Callable[[str, str, dict[str, Any]], tuple[bool, Optional[str]]]
# (agent_name, tool_name, arguments) -> (allowed, optional_deny_reason)


class _ChatClient(Protocol):
    model: str
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]): ...
    def chat_stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ChatStreamFrame]: ...


@dataclass
class Agent:
    name: str
    client: _ChatClient
    system_prompt: str
    tools: ToolRegistry
    max_tool_iterations: int = 10
    history: list[Message] = field(default_factory=list)
    permission_check: Optional[PermissionCheck] = None

    async def send(self, user_text: str) -> AssistantMessage:
        self.history.append(UserMessage(content=user_text))
        return await self._run_loop()

    async def send_stream(
        self,
        user_text: str,
        *,
        think: Optional[bool] = None,
    ) -> AsyncIterator[StreamFrame]:
        """Streaming variant of send(). Yields StreamFrame objects as the model produces them.

        Same ReAct loop semantics as send: permission denial preserved (deny path appends
        'Denied: ...' tool result and continues); multi-tool-call serialization invariant
        preserved (all ToolCallMessages first, then all ToolResultMessages).

        `think` controls whether the model's reasoning is enabled. None = client
        default (whatever the model would do); True = force reasoning on;
        False = force reasoning off. Only honored when the client talks to a
        local Ollama instance (remote OpenAI-compat endpoints ignore it).
        """
        self.history.append(UserMessage(content=user_text))
        call_index = 0
        for _ in range(self.max_tool_iterations + 1):
            wire = self._build_wire_messages()
            text_parts: list[str] = []
            tool_calls_block: list[dict[str, Any]] | None = None

            # Drain one model turn from chat_stream.
            # Only pass `think` when explicitly set, so legacy/mock clients
            # without the param keep working.
            client_kwargs: dict[str, Any] = {
                "messages": wire,
                "tools": self.tools.to_openai_schema(),
            }
            if think is not None:
                client_kwargs["think"] = think
            async for cs_frame in self.client.chat_stream(**client_kwargs):
                if isinstance(cs_frame, TextDelta):
                    text_parts.append(cs_frame.content)
                    yield TokenDelta(content=cs_frame.content)
                elif isinstance(cs_frame, ReasoningDelta):
                    # Reasoning is ephemeral — not appended to text_parts and not
                    # persisted in history. Just relay to the UI as a parallel channel.
                    yield ReasoningTokenDelta(content=cs_frame.content)
                elif isinstance(cs_frame, ToolCallsBlock):
                    tool_calls_block = cs_frame.calls
                elif isinstance(cs_frame, Done):
                    pass  # finish_reason consumed via the block end
                elif isinstance(cs_frame, ChatUsage):
                    yield TurnUsage(
                        call_index=call_index,
                        input_tokens=cs_frame.input_tokens,
                        output_tokens=cs_frame.output_tokens,
                        ttfb_ms=cs_frame.ttfb_ms,
                        generation_ms=cs_frame.generation_ms,
                        usage_reported=cs_frame.usage_reported,
                    )
            call_index += 1

            # Decide next phase: native tool calls, JSON-in-text fallback, or terminate
            calls: list[dict[str, Any]] = list(tool_calls_block) if tool_calls_block else []
            full_text = "".join(text_parts)
            if not calls and full_text:
                calls = parse_text_tool_calls(full_text, known_tools=self.tools.names())

            if not calls:
                reply = AssistantMessage(content=full_text)
                self.history.append(reply)
                yield Complete(reply=reply)
                return

            # Record all tool calls in history first, then tool results.
            # This matches send()'s ordering invariant.
            results: list[tuple[str, str]] = []
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                yield ToolCallStart(call=c)

                if self.permission_check is not None:
                    pc_result = self.permission_check(self.name, c["name"], c["arguments"])
                    if inspect.iscoroutine(pc_result):
                        pc_result = await pc_result
                    allowed, reason = pc_result
                    if not allowed:
                        deny_msg = f"Denied: {reason or 'permission_check returned False'}"
                        results.append((c["id"], deny_msg))
                        yield ToolResult(call_id=c["id"], content=deny_msg)
                        continue

                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    if inspect.iscoroutine(result):
                        result = await result
                    content = str(result)
                except KeyError:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                results.append((c["id"], content))
                yield ToolResult(call_id=c["id"], content=content)

            for call_id, content in results:
                self.history.append(ToolResultMessage(
                    tool_call_id=call_id,
                    content=content,
                ))
            # loop continues — call the model again
        raise RuntimeError(f"Agent {self.name} exceeded max_tool_iterations={self.max_tool_iterations}")

    async def _run_loop(self) -> AssistantMessage:
        for _ in range(self.max_tool_iterations + 1):
            wire = self._build_wire_messages()
            resp = await self.client.chat(messages=wire, tools=self.tools.to_openai_schema())

            # Determine tool calls: prefer native, fall back to JSON-in-text
            calls = list(resp.tool_calls)
            if not calls and resp.text:
                calls = parse_text_tool_calls(resp.text, known_tools=self.tools.names())

            if not calls:
                reply = AssistantMessage(content=resp.text)
                self.history.append(reply)
                return reply

            # Record ALL tool calls from this turn first (so they're consecutive
            # in history), then append results. This matches the OpenAI wire
            # format: one assistant message with tool_calls=[a,b,...] followed
            # by N separate tool result messages.
            results: list[tuple[str, str]] = []
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                # NEW: permission check — short-circuits before tool execution.
                # On deny, push the deny message into `results` so it lands in
                # the second loop alongside any allowed results.
                if self.permission_check is not None:
                    result = self.permission_check(self.name, c["name"], c["arguments"])
                    if inspect.iscoroutine(result):
                        result = await result
                    allowed, reason = result
                    if not allowed:
                        results.append((c["id"], f"Denied: {reason or 'permission_check returned False'}"))
                        continue
                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    if inspect.iscoroutine(result):
                        result = await result
                    content = str(result)
                except KeyError:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                results.append((c["id"], content))
            for call_id, content in results:
                self.history.append(ToolResultMessage(
                    tool_call_id=call_id,
                    content=content,
                ))
            # Loop continues — call the model again with the new tool results
        raise RuntimeError(f"Agent {self.name} exceeded max_tool_iterations={self.max_tool_iterations}")

    def _build_wire_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        i = 0
        while i < len(self.history):
            m = self.history[i]
            if isinstance(m, ToolCallMessage):
                # Collect all consecutive ToolCallMessages — they were emitted in
                # the same model turn and must serialize as ONE assistant message.
                batch: list[ToolCallMessage] = []
                while i < len(self.history) and isinstance(self.history[i], ToolCallMessage):
                    batch.append(self.history[i])
                    i += 1
                msgs.append({
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in batch
                    ],
                })
            else:
                msgs.append(to_openai_dict(m))
                i += 1
        return msgs


# ---------------------------------------------------------------------------
# Image sentinel translation — for tool results returning the kc_attachments
# image sentinel dict. Used by the supervisor's agent step loop (wiring in
# the supervisor, not in this kc-core file).
# ---------------------------------------------------------------------------

import json as _json_ais
from dataclasses import dataclass as _ais_dataclass
from pathlib import Path as _AisPath
from typing import Optional as _AisOptional

from kc_core.messages import ImageRef as _AisImageRef
from kc_core.messages import ToolResultMessage as _AisToolResultMessage
from kc_core.messages import UserMessage as _AisUserMessage


@_ais_dataclass(frozen=True)
class SentinelTranslation:
    """Result of translating an image sentinel: the ToolResultMessage to emit,
    plus an optional follow-up UserMessage carrying the image content."""

    tool_result: _AisToolResultMessage
    follow_up: _AisOptional[_AisUserMessage]


def _ais_guess_mime_from_path(path: _AisPath) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "heic": "image/heic",
    }.get(suffix, "application/octet-stream")


def translate_image_sentinel(
    raw_content: str,
    *,
    tool_call_id: str,
    vision_for_active_model: bool,
) -> SentinelTranslation:
    """If raw_content is an image sentinel dict, translate to (ack, follow-up).

    Sentinel shape: `{"type": "image", "path": <abs-path>, "ocr_markdown": <str>}`.

    On vision-capable models, the tool result is replaced with an
    acknowledgement string and a follow-up UserMessage carrying the image is
    returned. On non-vision models, the tool result is replaced with the
    OCR markdown; no follow-up. Non-sentinel input passes through unchanged.
    """
    try:
        payload = _json_ais.loads(raw_content)
    except (_json_ais.JSONDecodeError, ValueError):
        return SentinelTranslation(
            tool_result=_AisToolResultMessage(tool_call_id=tool_call_id, content=raw_content),
            follow_up=None,
        )
    if not isinstance(payload, dict) or payload.get("type") != "image":
        return SentinelTranslation(
            tool_result=_AisToolResultMessage(tool_call_id=tool_call_id, content=raw_content),
            follow_up=None,
        )

    img_path = _AisPath(str(payload.get("path", "")))
    ocr_md = str(payload.get("ocr_markdown", ""))

    if not vision_for_active_model:
        return SentinelTranslation(
            tool_result=_AisToolResultMessage(tool_call_id=tool_call_id, content=ocr_md),
            follow_up=None,
        )

    ack = "image rendered for the model in the next turn"
    return SentinelTranslation(
        tool_result=_AisToolResultMessage(tool_call_id=tool_call_id, content=ack),
        follow_up=_AisUserMessage(
            content="[image attachment]",
            images=(_AisImageRef(path=img_path, mime=_ais_guess_mime_from_path(img_path)),),
        ),
    )
