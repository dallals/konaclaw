import pytest
import respx
from httpx import Response
from kc_core.messages import UserMessage, to_openai_dict
from kc_core.ollama_client import OllamaClient, ChatResponse


@pytest.mark.asyncio
@respx.mock
async def test_chat_returns_text_response():
    import json as _j
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hello back"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    msgs = [to_openai_dict(UserMessage(content="hi"))]
    resp = await client.chat(messages=msgs, tools=[])
    assert isinstance(resp, ChatResponse)
    assert resp.text == "Hello back"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
@respx.mock
async def test_chat_returns_tool_calls():
    import json as _j
    sse = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"echo","arguments":"{\\"text\\":\\"hi\\"}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    resp = await client.chat(messages=[], tools=[])
    assert resp.tool_calls == [{"id": "call_1", "name": "echo", "arguments": {"text": "hi"}}]
    assert resp.text == ""


@pytest.mark.asyncio
@respx.mock
async def test_chat_raises_on_http_error():
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(500, text="boom")
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    with pytest.raises(RuntimeError, match="Ollama"):
        await client.chat(messages=[], tools=[])


@pytest.mark.asyncio
@respx.mock
async def test_chat_sends_authorization_header_when_api_key_set():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
        })
    )
    client = OllamaClient(
        base_url="https://openrouter.ai/api/v1",
        model="qwen/qwen-2.5-72b-instruct",
        api_key="sk-or-test-key",
    )
    await client.chat(messages=[], tools=[])
    assert route.called
    assert route.calls.last.request.headers.get("authorization") == "Bearer sk-or-test-key"


@pytest.mark.asyncio
@respx.mock
async def test_chat_omits_authorization_header_when_no_api_key():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
        })
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    await client.chat(messages=[], tools=[])
    assert route.called
    assert "authorization" not in {k.lower() for k in route.calls.last.request.headers.keys()}


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_yields_text_deltas():
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    from kc_core.stream_frames import TextDelta, Done
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    text_parts = [f.content for f in frames if isinstance(f, TextDelta)]
    assert "".join(text_parts) == "Hello!"
    done_frames = [f for f in frames if isinstance(f, Done)]
    assert len(done_frames) == 1
    assert done_frames[0].finish_reason == "stop"


import json as _json_mod


def _sse_bytes(*chunks) -> bytes:
    """Serialize dicts/strings as OpenAI-style SSE."""
    out = []
    for c in chunks:
        if isinstance(c, str):
            out.append(f"data: {c}\n\n".encode())
        else:
            out.append(f"data: {_json_mod.dumps(c)}\n\n".encode())
    return b"".join(out)


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_yields_text_deltas_then_done():
    """Three text-delta SSE chunks then [DONE]."""
    body = _sse_bytes(
        {"choices": [{"delta": {"content": "hello "}}]},
        {"choices": [{"delta": {"content": "world"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    from kc_core.stream_frames import TextDelta, Done, ChatUsage
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)

    # Filter out ChatUsage (always emitted at end) for the equality check.
    non_usage = [f for f in frames if not isinstance(f, ChatUsage)]
    assert non_usage == [
        TextDelta(content="hello "),
        TextDelta(content="world"),
        Done(finish_reason="stop"),
    ]


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_accumulates_tool_call_fragments_into_one_block():
    """OpenAI streams function.arguments as concatenated JSON-string fragments
    across multiple delta chunks. The client should accumulate and emit ONE
    ToolCallsBlock when finish_reason='tool_calls'."""
    body = _sse_bytes(
        # First fragment: id + name + start of arguments
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "echo", "arguments": "{\"text\":"}
        }]}}]},
        # Second fragment: rest of arguments
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": " \"hi\"}"}
        }]}}]},
        # Done with tool_calls
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    from kc_core.stream_frames import ToolCallsBlock, Done
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]

    block_frames = [f for f in frames if isinstance(f, ToolCallsBlock)]
    assert len(block_frames) == 1
    block = block_frames[0]
    assert len(block.calls) == 1
    assert block.calls[0]["id"] == "call_1"
    assert block.calls[0]["name"] == "echo"
    assert block.calls[0]["arguments"] == {"text": "hi"}

    done_frames = [f for f in frames if isinstance(f, Done)]
    assert len(done_frames) == 1
    assert done_frames[0].finish_reason == "tool_calls"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_skips_keepalive_lines():
    """Empty deltas (keepalives) are skipped."""
    body = _sse_bytes(
        {"choices": [{"delta": {}}]},  # keepalive
        {"choices": [{"delta": {"content": "hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    from kc_core.stream_frames import TextDelta, Done, ChatUsage
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    non_usage = [f for f in frames if not isinstance(f, ChatUsage)]
    assert non_usage == [TextDelta(content="hi"), Done(finish_reason="stop")]


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_skips_malformed_data_lines():
    """A non-JSON data line is skipped (matches existing client's tolerant behavior)."""
    body = (
        b"data: not-json\n\n"
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    from kc_core.stream_frames import TextDelta, Done
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    assert TextDelta(content="hi") in frames
    assert Done(finish_reason="stop") in frames


@pytest.mark.asyncio
@respx.mock
async def test_chat_uses_chat_stream_internally_and_returns_accumulated_response():
    """The non-streaming chat() should still work — backed by chat_stream."""
    body = _sse_bytes(
        {"choices": [{"delta": {"content": "hi "}}]},
        {"choices": [{"delta": {"content": "there"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    resp = await client.chat(messages=[], tools=[])
    assert resp.text == "hi there"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
@respx.mock
async def test_chat_via_chat_stream_surfaces_tool_calls():
    """chat() should surface accumulated tool_calls from chat_stream."""
    body = _sse_bytes(
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "echo", "arguments": "{\"text\":\"hi\"}"}
        }]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )

    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    resp = await client.chat(messages=[], tools=[])
    assert resp.text == ""
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["name"] == "echo"
    assert resp.tool_calls[0]["arguments"] == {"text": "hi"}
    assert resp.finish_reason == "tool_calls"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_when_provider_reports():
    from kc_core.stream_frames import TextDelta, Done, ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":17,"completion_tokens":4,"total_tokens":21}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    types = [type(f).__name__ for f in frames]
    assert types == ["TextDelta", "TextDelta", "Done", "ChatUsage"]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.input_tokens == 17
    assert usage.output_tokens == 4
    assert usage.usage_reported is True
    assert usage.ttfb_ms >= 0.0
    assert usage.generation_ms >= 0.0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_request_body_includes_stream_options():
    sse = (
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    captured = {}
    def _capture(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return Response(200, content=sse, headers={"content-type": "text/event-stream"})
    respx.post("http://localhost:11434/v1/chat/completions").mock(side_effect=_capture)
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    async for _ in client.chat_stream(messages=[], tools=[]):
        pass
    assert captured["body"].get("stream_options") == {"include_usage": True}


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_when_provider_silent():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.usage_reported is False
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.ttfb_ms >= 0.0
    assert usage.generation_ms >= 0.0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_treats_garbage_usage_as_unreported():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":-3,"completion_tokens":4}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.usage_reported is False
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_for_tool_only_turn():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"echo","arguments":"{}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":8}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = [f for f in frames if isinstance(f, ChatUsage)][0]
    assert usage.usage_reported is True
    assert usage.input_tokens == 50
    assert usage.output_tokens == 8
    # tool-only turn: no text was emitted, so generation_ms should be 0.0
    assert usage.generation_ms == 0.0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_yields_reasoning_deltas_for_reasoning_models():
    """Reasoning models (gemma4, deepseek-r1, qwq, etc.) emit a `reasoning`
    field on delta — parallel to `content`. The client should surface those
    as ReasoningDelta frames so the UI can render thinking in a separate
    channel."""
    body = _sse_bytes(
        {"choices": [{"delta": {"reasoning": "The "}}]},
        {"choices": [{"delta": {"reasoning": "user "}}]},
        {"choices": [{"delta": {"reasoning": "asks."}}]},
        {"choices": [{"delta": {"content": "Hello"}}]},
        {"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )
    from kc_core.stream_frames import TextDelta, ReasoningDelta, Done
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    reasoning = "".join(f.content for f in frames if isinstance(f, ReasoningDelta))
    text = "".join(f.content for f in frames if isinstance(f, TextDelta))
    assert reasoning == "The user asks."
    assert text == "Hello!"
    # Reasoning frames must arrive before the first text frame, mirroring
    # the order the model emits them — UI relies on order for auto-collapse.
    first_text_idx = next(i for i, f in enumerate(frames) if isinstance(f, TextDelta))
    first_reasoning_idx = next(i for i, f in enumerate(frames) if isinstance(f, ReasoningDelta))
    assert first_reasoning_idx < first_text_idx


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_handles_streams_without_reasoning():
    """Non-reasoning models (qwen, gemma3, llama) never emit `reasoning`.
    The client must not yield any ReasoningDelta frames in that case."""
    body = _sse_bytes(
        {"choices": [{"delta": {"content": "plain "}}]},
        {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )
    from kc_core.stream_frames import ReasoningDelta
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    assert not any(isinstance(f, ReasoningDelta) for f in frames)


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_ttfb_captures_first_reasoning_byte_not_first_content():
    """For reasoning models, bytes start flowing as soon as `reasoning` deltas
    arrive — even though `content` may stay empty for many seconds. TTFB must
    reflect when the user starts seeing output, not when the final answer
    begins. Otherwise the badge reads '31s to start' when the user actually
    saw streaming reasoning text from second 1."""
    import asyncio
    # Two reasoning chunks then a content chunk. We can't control wall time
    # in tests; instead we just assert generation_ms (which measures
    # done - first_byte) is non-zero, proving first_byte was stamped early.
    body = _sse_bytes(
        {"choices": [{"delta": {"reasoning": "thinking"}}]},
        {"choices": [{"delta": {"reasoning": " more"}}]},
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        "[DONE]",
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )
    from kc_core.stream_frames import ChatUsage
    # Insert a tiny sleep between client.chat_stream startup and consumption
    # to ensure measurable time passes; respx returns instantly so this is
    # the only way to make ttfb meaningfully > 0.
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
        if len(frames) == 1:
            # After the first frame (a ReasoningDelta), wait briefly so the
            # gap between first_byte and t_done is measurable.
            await asyncio.sleep(0.02)
    usage = next(f for f in frames if isinstance(f, ChatUsage))
    # If first_byte was stamped on the first reasoning delta (as it should be),
    # generation_ms will include the 20ms sleep above. If it was stamped on
    # first content (the current bug), generation_ms ≈ 0 because content and
    # done arrived in the same chunk.
    assert usage.generation_ms >= 15.0, (
        f"TTFB should be stamped on first reasoning byte, not first content. "
        f"generation_ms={usage.generation_ms} suggests t_first_byte was set "
        f"on the content delta (which arrived after the sleep), not the "
        f"reasoning delta (which arrived before)."
    )


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_routes_to_native_api_chat_when_think_is_false():
    """When think=False is passed, the client must POST to Ollama's native
    /api/chat endpoint (which honors `think: false`), not /v1/chat/completions
    (which ignores it). The native endpoint's JSON-lines stream format
    differs: each line is a full JSON object with a `message` field."""
    ndjson_body = (
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"Hello"},"done":false}\n'
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"!"},"done":true,'
        b'"done_reason":"stop","prompt_eval_count":10,"eval_count":2,'
        b'"prompt_eval_duration":1000000,"eval_duration":2000000,"total_duration":4000000}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body, headers={"content-type": "application/x-ndjson"})
    )
    from kc_core.stream_frames import TextDelta, Done, ChatUsage
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[], think=False):
        frames.append(f)
    assert route.called
    # Body must include the think field so Ollama suppresses reasoning.
    sent = _json_mod.loads(route.calls.last.request.content)
    assert sent.get("think") is False
    assert sent.get("model") == "gemma4:31b"
    # Frame translation must preserve the OpenAI-compat semantics.
    text = "".join(f.content for f in frames if isinstance(f, TextDelta))
    assert text == "Hello!"
    done = [f for f in frames if isinstance(f, Done)]
    assert len(done) == 1
    assert done[0].finish_reason == "stop"
    usage = next(f for f in frames if isinstance(f, ChatUsage))
    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
    assert usage.usage_reported is True


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_path_yields_reasoning_for_think_true():
    """When think=True, the client uses /api/chat and Ollama emits `thinking`
    in message — translate to ReasoningDelta frames."""
    ndjson_body = (
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"","thinking":"Let me "},"done":false}\n'
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"","thinking":"see..."},"done":false}\n'
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"Hi"},"done":true,'
        b'"done_reason":"stop","prompt_eval_count":5,"eval_count":1}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    from kc_core.stream_frames import TextDelta, ReasoningDelta
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[], think=True):
        frames.append(f)
    sent = _json_mod.loads(route.calls.last.request.content)
    assert sent.get("think") is True
    reasoning = "".join(f.content for f in frames if isinstance(f, ReasoningDelta))
    text = "".join(f.content for f in frames if isinstance(f, TextDelta))
    assert reasoning == "Let me see..."
    assert text == "Hi"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_path_parses_tool_calls():
    """Ollama's /api/chat emits tool_calls as a complete array on a single
    line (no fragment accumulation needed)."""
    ndjson_body = (
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"",'
        b'"tool_calls":[{"function":{"name":"echo","arguments":{"text":"hi"}}}]},'
        b'"done":true,"done_reason":"tool_calls","prompt_eval_count":5,"eval_count":1}\n'
    )
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    from kc_core.stream_frames import ToolCallsBlock
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[{"type": "function", "function": {"name": "echo"}}], think=False):
        frames.append(f)
    tool_blocks = [f for f in frames if isinstance(f, ToolCallsBlock)]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].calls[0]["name"] == "echo"
    assert tool_blocks[0].calls[0]["arguments"] == {"text": "hi"}


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_stays_on_openai_compat_when_think_is_none():
    """think=None (default) preserves the existing /v1 path. No regression
    against existing consumers (openrouter, NIM, etc.)."""
    body = _sse_bytes(
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    v1_route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=body)
    )
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    assert v1_route.called


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_rehydrates_tool_call_arguments_from_string():
    """Agent._build_wire_messages encodes tool_call arguments as a JSON
    string (OpenAI /v1 convention). When we route to native /api/chat the
    arguments must be re-parsed into a dict, otherwise Ollama 400s with
    'Value looks like object, but can't find closing }' — observed live
    on 2026-05-14 the first time gemma4 made a tool call with think=False."""
    ndjson_body = (
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"ok"},'
        b'"done":true,"done_reason":"stop","prompt_eval_count":5,"eval_count":1}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    history_with_prior_tool_call = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "echo",
                    # Encoded as a JSON STRING — the /v1 convention.
                    "arguments": '{"text": "hi"}',
                },
            }],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "hi"},
        {"role": "user", "content": "what next?"},
    ]
    frames = []
    async for f in client.chat_stream(
        messages=history_with_prior_tool_call, tools=[], think=False,
    ):
        frames.append(f)
    # Inspect the request body sent to /api/chat: tool_call arguments must
    # be a DICT, not a string.
    sent = _json_mod.loads(route.calls.last.request.content)
    asst = next(m for m in sent["messages"] if m.get("tool_calls"))
    args = asst["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, dict), f"arguments must be dict, got {type(args).__name__}: {args!r}"
    assert args == {"text": "hi"}


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_tolerates_already_dict_arguments():
    """If the caller already passes arguments as a dict (e.g. a future call
    site that skips the /v1 stringification), the rehydration must be a no-op
    and not double-convert."""
    ndjson_body = (
        b'{"model":"gemma4:31b","message":{"role":"assistant","content":"ok"},'
        b'"done":true,"done_reason":"stop","prompt_eval_count":5,"eval_count":1}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:31b")
    msgs = [{
        "role": "assistant",
        "tool_calls": [{
            "id": "c0", "type": "function",
            "function": {"name": "x", "arguments": {"k": "v"}},
        }],
    }]
    frames = []
    async for f in client.chat_stream(messages=msgs, tools=[], think=False):
        frames.append(f)
    sent = _json_mod.loads(route.calls.last.request.content)
    args = sent["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert args == {"k": "v"}


# --- keep_alive tests (feels-faster phase) -----------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_keep_alive_default_is_30m(monkeypatch):
    monkeypatch.delenv("KC_OLLAMA_KEEP_ALIVE", raising=False)
    sse = (
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    captured = {}
    def _capture(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return Response(200, content=sse, headers={"content-type": "text/event-stream"})
    respx.post("http://localhost:11434/v1/chat/completions").mock(side_effect=_capture)
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    async for _ in client.chat_stream(messages=[], tools=[]):
        pass
    assert captured["body"]["keep_alive"] == "30m"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_keep_alive_env_override_minus_one(monkeypatch):
    monkeypatch.setenv("KC_OLLAMA_KEEP_ALIVE", "-1")
    sse = (
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    captured = {}
    def _capture(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return Response(200, content=sse, headers={"content-type": "text/event-stream"})
    respx.post("http://localhost:11434/v1/chat/completions").mock(side_effect=_capture)
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    async for _ in client.chat_stream(messages=[], tools=[]):
        pass
    assert captured["body"]["keep_alive"] == "-1"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_includes_keep_alive(monkeypatch):
    """The /api/chat native path also carries keep_alive."""
    monkeypatch.delenv("KC_OLLAMA_KEEP_ALIVE", raising=False)
    # Native /api/chat returns one JSON-line per chunk (not SSE).
    body_lines = (
        b'{"message":{"content":""},"done":false}\n'
        b'{"message":{"content":""},"done":true,"prompt_eval_count":1,"eval_count":1,"prompt_eval_duration":1,"eval_duration":1,"total_duration":1}\n'
    )
    captured = {}
    def _capture(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return Response(200, content=body_lines)
    respx.post("http://localhost:11434/api/chat").mock(side_effect=_capture)
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    async for _ in client.chat_stream(messages=[], tools=[], think=False):
        pass
    assert captured["body"]["keep_alive"] == "30m"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_translates_multimodal_content_to_images_field():
    """Agent._build_wire_messages emits OpenAI /v1 multimodal user content
    as a list of {type:text|image_url} blocks. /api/chat (native) rejects
    that with 'json: cannot unmarshal array into Go struct field
    ChatRequest.messages.content of type string'. The client must split it
    into `content` (string) + `images` (list of raw base64) before sending."""
    ndjson_body = (
        b'{"model":"gemma4:26b","message":{"role":"assistant","content":"ok"},'
        b'"done":true,"done_reason":"stop","prompt_eval_count":5,"eval_count":1}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:26b")
    user_with_image = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what's in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        },
    ]
    async for _ in client.chat_stream(messages=user_with_image, tools=[], think=False):
        pass

    sent = _json_mod.loads(route.calls.last.request.content)
    user_msg = next(m for m in sent["messages"] if m["role"] == "user")
    # content is now a STRING — no longer an array.
    assert isinstance(user_msg["content"], str), (
        f"content must be string, got {type(user_msg['content']).__name__}"
    )
    assert user_msg["content"] == "what's in this image?"
    # images is the raw base64 with the `data:image/...;base64,` prefix STRIPPED.
    assert user_msg["images"] == ["iVBORw0KGgo="]


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_native_passes_plain_string_content_through_unchanged():
    """Text-only messages must not pick up an empty images field, and the
    content must remain a plain string. Regression guard for the array→string
    translation accidentally rewriting non-array content."""
    ndjson_body = (
        b'{"message":{"content":"ok"},"done":true,"prompt_eval_count":1,"eval_count":1,'
        b'"prompt_eval_duration":1,"eval_duration":1,"total_duration":1}\n'
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=Response(200, content=ndjson_body)
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma4:26b")
    plain = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    async for _ in client.chat_stream(messages=plain, tools=[], think=False):
        pass

    sent = _json_mod.loads(route.calls.last.request.content)
    user_msg = next(m for m in sent["messages"] if m["role"] == "user")
    assert user_msg["content"] == "hello"
    assert "images" not in user_msg
