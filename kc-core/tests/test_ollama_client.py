import pytest
import respx
from httpx import Response
from kc_core.messages import UserMessage, to_openai_dict
from kc_core.ollama_client import OllamaClient, ChatResponse


@pytest.mark.asyncio
@respx.mock
async def test_chat_returns_text_response():
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "id": "chatcmpl-test",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello back"},
                "finish_reason": "stop",
            }],
        })
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
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text":"hi"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })
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
