from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
import httpx


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "gemma3:4b",
        timeout: float = 120.0,
        api_key: str | None = None,
    ) -> None:
        stripped = base_url.rstrip("/")
        parsed = urlparse(stripped)
        # If the path is non-empty (beyond root), assume it already includes /v1
        # and we only need to append /chat/completions.
        # If the path is empty or just "/", prepend /v1 as well.
        if parsed.path and parsed.path != "/":
            self._completions_url = f"{stripped}/chat/completions"
        else:
            self._completions_url = f"{stripped}/v1/chat/completions"
        self.base_url = stripped
        self.model = model
        self._timeout = timeout
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        """Stream OpenAI-compatible SSE frames as ChatStreamFrame.

        Tool calls in OpenAI's streaming format arrive as multi-chunk fragments
        (function.arguments is split across deltas). We accumulate them per index
        and emit ONE ToolCallsBlock when finish_reason='tool_calls' is seen.
        """
        from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        # Per-index accumulators for tool call fragments
        tool_call_frags: dict[int, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST",
                self._completions_url,
                json=body,
                headers=self._headers(),
            ) as r:
                if r.status_code != 200:
                    body_bytes = await r.aread()
                    raise RuntimeError(f"Ollama returned {r.status_code}: {body_bytes!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {}) or {}
                    finish_reason = choice.get("finish_reason")

                    # Accumulate tool-call fragments (arrive before finish_reason)
                    deltas = delta.get("tool_calls")
                    if deltas:
                        for tc in deltas:
                            idx = tc.get("index", 0)
                            slot = tool_call_frags.setdefault(idx, {
                                "id": "", "name": "", "arguments_str": "",
                            })
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            args_frag = fn.get("arguments")
                            if args_frag:
                                slot["arguments_str"] += args_frag

                    # Yield text content
                    text = delta.get("content")
                    if text:
                        yield TextDelta(content=text)

                    # On finish_reason, flush accumulated tool calls then Done
                    if finish_reason:
                        if tool_call_frags:
                            calls = []
                            for idx in sorted(tool_call_frags.keys()):
                                slot = tool_call_frags[idx]
                                args_str = slot["arguments_str"] or "{}"
                                try:
                                    args = json.loads(args_str)
                                except json.JSONDecodeError:
                                    args = {}
                                calls.append({
                                    "id": slot["id"] or f"call_{idx}",
                                    "name": slot["name"],
                                    "arguments": args,
                                })
                            yield ToolCallsBlock(calls=calls)
                            tool_call_frags.clear()
                        yield Done(finish_reason=finish_reason)
                        return

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        """Non-streaming convenience: accumulate chat_stream into a ChatResponse."""
        from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish: str = ""
        async for frame in self.chat_stream(messages=messages, tools=tools):
            if isinstance(frame, TextDelta):
                text_parts.append(frame.content)
            elif isinstance(frame, ToolCallsBlock):
                tool_calls.extend(frame.calls)
            elif isinstance(frame, Done):
                finish = frame.finish_reason
                break
        return ChatResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            raw={},  # not preserved when going through stream
        )
