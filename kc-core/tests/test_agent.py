import pytest
from kc_core.agent import Agent
from kc_core.messages import UserMessage, AssistantMessage
from kc_core.ollama_client import ChatResponse
from kc_core.tools import ToolRegistry, Tool


@pytest.mark.asyncio
async def test_agent_replies_with_assistant_text(fake_ollama):
    client = fake_ollama(ChatResponse(text="Hi!", finish_reason="stop"))
    agent = Agent(name="kc", client=client, system_prompt="You are kc.", tools=ToolRegistry())
    reply = await agent.send("hello")
    assert isinstance(reply, AssistantMessage)
    assert reply.content == "Hi!"


@pytest.mark.asyncio
async def test_agent_keeps_history_across_turns(fake_ollama):
    client = fake_ollama(
        ChatResponse(text="one", finish_reason="stop"),
        ChatResponse(text="two", finish_reason="stop"),
    )
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=ToolRegistry())
    await agent.send("first")
    await agent.send("second")
    second_call_msgs = client.calls[1]["messages"]
    roles = [m["role"] for m in second_call_msgs]
    assert roles == ["system", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_agent_includes_system_prompt(fake_ollama):
    client = fake_ollama(ChatResponse(text="ok", finish_reason="stop"))
    agent = Agent(name="kc", client=client, system_prompt="be terse", tools=ToolRegistry())
    await agent.send("hi")
    first = client.calls[0]["messages"][0]
    assert first == {"role": "system", "content": "be terse"}


@pytest.mark.asyncio
async def test_agent_executes_native_tool_call(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "call_1", "name": "echo", "arguments": {"text": "hello"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="Done. Echoed: hello", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(
        name="echo", description="echo", parameters={},
        impl=lambda text: text,
    ))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    reply = await agent.send("please echo hello")
    assert reply.content == "Done. Echoed: hello"
    # Second call to Ollama must include the tool result
    second_msgs = client.calls[1]["messages"]
    assert any(m["role"] == "tool" and m["content"] == "hello" for m in second_msgs)


@pytest.mark.asyncio
async def test_agent_uses_json_in_text_fallback(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text='```json\n{"tool": "echo", "arguments": {"text": "hi"}}\n```',
            tool_calls=[],
            finish_reason="stop",
        ),
        ChatResponse(text="echoed: hi", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    reply = await agent.send("echo hi please")
    assert reply.content == "echoed: hi"


@pytest.mark.asyncio
async def test_agent_stops_at_max_iterations(fake_ollama):
    looping_response = ChatResponse(
        text="",
        tool_calls=[{"id": "x", "name": "echo", "arguments": {"text": "x"}}],
        finish_reason="tool_calls",
    )
    client = fake_ollama(*[looping_response] * 10)
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg, max_tool_iterations=3)
    with pytest.raises(RuntimeError, match="max_tool_iterations"):
        await agent.send("loop")


@pytest.mark.asyncio
async def test_agent_returns_tool_error_message_on_unknown_tool(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "nonexistent", "arguments": {}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="ok, that tool doesn't exist", finish_reason="stop"),
    )
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=ToolRegistry())
    reply = await agent.send("call missing tool")
    second_msgs = client.calls[1]["messages"]
    err_msg = next(m for m in second_msgs if m["role"] == "tool")
    assert "unknown_tool" in err_msg["content"]
    assert reply.content == "ok, that tool doesn't exist"
