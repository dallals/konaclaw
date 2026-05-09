from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
import httpx
from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done


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
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools

        # Per-index accumulators for tool call fragments
        tool_call_frags: dict[int, dict[str, Any]] = {}

        t_request_start = time.monotonic()
        t_first_byte: float | None = None
        usage_obj: dict[str, Any] | None = None
        done_emitted = False

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
                    if chunk.get("usage"):
                        usage_obj = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
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
                        if t_first_byte is None:
                            t_first_byte = time.monotonic()
                        yield TextDelta(content=text)

                    # On finish_reason, flush accumulated tool calls then Done (once)
                    if finish_reason and not done_emitted:
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
                        done_emitted = True
                        # do NOT return — usage chunk may follow

                # End of stream — emit ChatUsage with whatever timing we have.
                t_done = time.monotonic()
                if t_first_byte is None:
                    t_first_byte = t_done  # tool-only or empty turn
                ttfb_ms = (t_first_byte - t_request_start) * 1000.0
                gen_ms = (t_done - t_first_byte) * 1000.0
                usage_reported = False
                input_tokens = 0
                output_tokens = 0
                if usage_obj:
                    pt = usage_obj.get("prompt_tokens")
                    ct = usage_obj.get("completion_tokens")
                    if isinstance(pt, int) and pt >= 0 and isinstance(ct, int) and ct >= 0:
                        input_tokens = pt
                        output_tokens = ct
                        usage_reported = True
                from kc_core.stream_frames import ChatUsage
                yield ChatUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    ttfb_ms=ttfb_ms,
                    generation_ms=gen_ms,
                    usage_reported=usage_reported,
                )

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
