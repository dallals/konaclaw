from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse
import httpx
from kc_core.stream_frames import TextDelta, ReasoningDelta, ToolCallsBlock, Done, ChatUsage


def _native_message(m: dict[str, Any]) -> dict[str, Any]:
    """Translate an OpenAI /v1-shaped message to Ollama /api/chat shape.

    The only meaningful difference today is `tool_calls[].function.arguments`:
    /v1 encodes it as a JSON string; /api/chat expects a dict. Everything
    else passes through. Returns a NEW dict — does not mutate input.
    """
    if not m.get("tool_calls"):
        return m
    out = dict(m)
    out["tool_calls"] = [_native_tool_call(tc) for tc in m["tool_calls"]]
    return out


def _native_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn = tc.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            args = {}
    elif not isinstance(args, dict):
        args = {}
    return {
        **tc,
        "function": {**fn, "arguments": args},
    }


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
            self._native_chat_url = None  # remote OpenAI-compat, no native API
        else:
            self._completions_url = f"{stripped}/v1/chat/completions"
            self._native_chat_url = f"{stripped}/api/chat"
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
        think: Optional[bool] = None,
    ):
        """Stream chat frames.

        When `think` is None, uses Ollama's OpenAI-compatible SSE endpoint at
        /v1/chat/completions (the default; required for remote providers like
        NVIDIA NIM, OpenRouter, etc.).

        When `think` is True/False, uses Ollama's native JSON-lines endpoint
        at /api/chat which honors the `think` parameter. The /v1 shim ignores
        `think`, so reasoning models will always reason on /v1 regardless —
        this is the only way to actually suppress (or force) reasoning.
        Falls back silently to /v1 if the base_url is a remote OpenAI-compat
        endpoint where /api/chat doesn't exist.
        """
        if think is not None and self._native_chat_url is not None:
            async for f in self._chat_stream_native(messages, tools, think=think):
                yield f
        else:
            async for f in self._chat_stream_openai(messages, tools):
                yield f

    async def _chat_stream_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        """OpenAI-compatible SSE path (/v1/chat/completions).

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

                    # Yield reasoning content first (reasoning models like
                    # gemma4 emit `reasoning` deltas before any `content`).
                    # TTFB is stamped here too — from the user's perspective
                    # bytes have started flowing as soon as reasoning streams,
                    # even though `content` is still empty.
                    reasoning = delta.get("reasoning")
                    if reasoning:
                        if t_first_byte is None:
                            t_first_byte = time.monotonic()
                        yield ReasoningDelta(content=reasoning)

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
                yield ChatUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    ttfb_ms=ttfb_ms,
                    generation_ms=gen_ms,
                    usage_reported=usage_reported,
                )

    async def _chat_stream_native(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        think: bool,
    ):
        """Ollama native /api/chat path. JSON-lines stream where each line is
        a full chat completion chunk: {"message":{...},"done":bool, ...}.

        Honors `think: false` to suppress reasoning on capable models.
        """
        assert self._native_chat_url is not None  # guarded by caller
        # Native /api/chat expects tool_calls[].function.arguments as an
        # object/dict; the OpenAI /v1 wire format encodes it as a JSON string.
        # Agent._build_wire_messages produces the /v1 shape, so rehydrate the
        # arguments back to dicts here when forwarding to /api/chat. Without
        # this, Ollama 400s with "Value looks like object, but can't find
        # closing '}' symbol" the moment any prior tool call lands in history.
        wire_messages = [_native_message(m) for m in messages]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "stream": True,
            "think": think,
        }
        if tools:
            body["tools"] = tools

        t_request_start = time.monotonic()
        t_first_byte: float | None = None
        prompt_eval_count = 0
        eval_count = 0
        prompt_eval_duration_ns = 0
        eval_duration_ns = 0
        usage_reported = False
        done_emitted = False
        tool_calls_pending: list[dict[str, Any]] = []
        call_idx = 0

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST",
                self._native_chat_url,
                json=body,
                headers=self._headers(),
            ) as r:
                if r.status_code != 200:
                    body_bytes = await r.aread()
                    raise RuntimeError(f"Ollama returned {r.status_code}: {body_bytes!r}")
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = chunk.get("message") or {}

                    thinking = msg.get("thinking")
                    if thinking:
                        if t_first_byte is None:
                            t_first_byte = time.monotonic()
                        yield ReasoningDelta(content=thinking)

                    content = msg.get("content")
                    if content:
                        if t_first_byte is None:
                            t_first_byte = time.monotonic()
                        yield TextDelta(content=content)

                    raw_calls = msg.get("tool_calls") or []
                    for tc in raw_calls:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments")
                        # /api/chat sends arguments as either dict or JSON string.
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        elif not isinstance(args, dict):
                            args = {}
                        tool_calls_pending.append({
                            "id": tc.get("id") or f"call_{call_idx}",
                            "name": fn.get("name", ""),
                            "arguments": args,
                        })
                        call_idx += 1

                    if chunk.get("done") and not done_emitted:
                        prompt_eval_count = int(chunk.get("prompt_eval_count") or 0)
                        eval_count = int(chunk.get("eval_count") or 0)
                        prompt_eval_duration_ns = int(chunk.get("prompt_eval_duration") or 0)
                        eval_duration_ns = int(chunk.get("eval_duration") or 0)
                        usage_reported = prompt_eval_count > 0 or eval_count > 0
                        if tool_calls_pending:
                            yield ToolCallsBlock(calls=tool_calls_pending)
                            tool_calls_pending = []
                        finish = chunk.get("done_reason") or "stop"
                        yield Done(finish_reason=finish)
                        done_emitted = True

                t_done = time.monotonic()
                if t_first_byte is None:
                    t_first_byte = t_done  # tool-only or empty turn
                ttfb_ms = (t_first_byte - t_request_start) * 1000.0
                gen_ms = (t_done - t_first_byte) * 1000.0
                yield ChatUsage(
                    input_tokens=prompt_eval_count,
                    output_tokens=eval_count,
                    ttfb_ms=ttfb_ms,
                    generation_ms=gen_ms,
                    usage_reported=usage_reported,
                )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        think: Optional[bool] = None,
    ) -> ChatResponse:
        """Non-streaming convenience: accumulate chat_stream into a ChatResponse."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish: str = ""
        async for frame in self.chat_stream(messages=messages, tools=tools, think=think):
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
