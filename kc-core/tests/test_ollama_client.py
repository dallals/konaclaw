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
