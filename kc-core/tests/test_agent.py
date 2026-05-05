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


@pytest.mark.asyncio
async def test_agent_serializes_multiple_tool_calls_as_one_assistant_message(fake_ollama):
    """When the model emits multiple tool calls in a single turn, the wire
    format must be ONE assistant message with tool_calls=[a, b], followed
    by SEPARATE tool result messages — not N interleaved pairs."""
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[
                {"id": "c1", "name": "echo", "arguments": {"text": "first"}},
                {"id": "c2", "name": "echo", "arguments": {"text": "second"}},
            ],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="done", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    await agent.send("call both")

    # Inspect the second wire payload — must have ONE assistant tool-call message
    # with TWO tool_calls, then TWO separate tool-result messages.
    second = client.calls[1]["messages"]
    # Filter to the call/result section (skip system + initial user)
    relevant = [m for m in second if m["role"] in ("assistant", "tool")]
    assert len(relevant) == 3, f"Expected 1 assistant + 2 tools, got {len(relevant)}: {relevant}"
    assert relevant[0]["role"] == "assistant"
    assert "tool_calls" in relevant[0]
    assert len(relevant[0]["tool_calls"]) == 2
    assert relevant[0]["tool_calls"][0]["id"] == "c1"
    assert relevant[0]["tool_calls"][1]["id"] == "c2"
    assert relevant[1] == {"role": "tool", "tool_call_id": "c1", "content": "first"}
    assert relevant[2] == {"role": "tool", "tool_call_id": "c2", "content": "second"}


@pytest.mark.asyncio
async def test_agent_permission_check_can_deny_tool(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="couldn't run it", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    seen_calls = []
    def deny_all(agent_name: str, tool_name: str, arguments: dict) -> tuple[bool, str | None]:
        seen_calls.append((agent_name, tool_name, arguments))
        return (False, "this is a test")

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=deny_all,
    )
    reply = await agent.send("please echo hi")
    assert reply.content == "couldn't run it"
    # The denied tool result should have been surfaced back to the model
    second = client.calls[1]["messages"]
    err = next(m for m in second if m["role"] == "tool")
    assert "Denied" in err["content"]
    assert seen_calls == [("kc", "echo", {"text": "hi"})]


@pytest.mark.asyncio
async def test_agent_no_permission_check_allows_all(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="done", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    reply = await agent.send("echo")
    assert reply.content == "done"


@pytest.mark.asyncio
async def test_agent_permission_check_supports_async_callback(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="ok done", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    async def async_allow(agent_name, tool_name, args):
        return (True, None)

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=async_allow,
    )
    reply = await agent.send("echo hi")
    assert reply.content == "ok done"


@pytest.mark.asyncio
async def test_agent_permission_check_supports_async_deny(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="couldn't run it", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    async def async_deny(agent_name, tool_name, args):
        return (False, "async denied")

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=async_deny,
    )
    reply = await agent.send("echo hi")
    assert reply.content == "couldn't run it"
    # The denied tool result must surface to the model — proves the async
    # callback was awaited (a non-awaited coroutine would not produce a
    # (False, reason) tuple to unpack).
    second = client.calls[1]["messages"]
    err = next(m for m in second if m["role"] == "tool")
    assert "Denied" in err["content"]
    assert "async denied" in err["content"]


from kc_core.stream_frames import (
    TextDelta, ToolCallsBlock, Done,
    TokenDelta, ToolCallStart, ToolResult, Complete,
)


@pytest.mark.asyncio
async def test_agent_send_stream_yields_token_deltas_and_complete(fake_ollama):
    """Single-turn text-only response: tokens stream, then Complete."""
    client = fake_ollama(
        stream_responses=[[
            TextDelta(content="hello "),
            TextDelta(content="world"),
            Done(finish_reason="stop"),
        ]],
    )
    reg = ToolRegistry()
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    frames = []
    async for f in agent.send_stream("hi"):
        frames.append(f)

    assert isinstance(frames[0], TokenDelta)
    assert frames[0].content == "hello "
    assert isinstance(frames[1], TokenDelta)
    assert frames[1].content == "world"
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "hello world"


@pytest.mark.asyncio
async def test_agent_send_stream_runs_tool_call_between_turns(fake_ollama):
    """First turn: tool call. Second turn: text. Frame sequence covers all phases."""
    client = fake_ollama(
        stream_responses=[
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            [
                TextDelta(content="echoed: hi"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)

    frames = [f async for f in agent.send_stream("please echo")]
    types = [type(f).__name__ for f in frames]
    assert "ToolCallStart" in types
    assert "ToolResult" in types
    assert types.index("ToolCallStart") < types.index("ToolResult")
    assert types.index("ToolResult") < types.index("TokenDelta")
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "echoed: hi"


@pytest.mark.asyncio
async def test_agent_send_stream_propagates_permission_deny(fake_ollama):
    """Sync deny callback short-circuits the tool — ToolResult content begins with 'Denied'."""
    client = fake_ollama(
        stream_responses=[
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            [
                TextDelta(content="couldn't run it"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    def deny_all(agent_name, tool_name, args):
        return (False, "no")

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=deny_all,
    )
    frames = [f async for f in agent.send_stream("please echo")]
    tool_results = [f for f in frames if isinstance(f, ToolResult)]
    assert len(tool_results) == 1
    assert "Denied" in tool_results[0].content


@pytest.mark.asyncio
async def test_agent_send_stream_supports_async_permission_check(fake_ollama):
    """Async permission check works with streaming."""
    client = fake_ollama(
        stream_responses=[
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            [
                TextDelta(content="ok"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    async def async_allow(agent_name, tool_name, args):
        return (True, None)

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=async_allow,
    )
    frames = [f async for f in agent.send_stream("please echo")]
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "ok"


@pytest.mark.asyncio
async def test_agent_send_stream_raises_on_max_iterations(fake_ollama):
    """If the model loops forever asking for tool calls, send_stream raises."""
    looping = [
        ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "x"}}]),
        Done(finish_reason="tool_calls"),
    ]
    client = fake_ollama(
        stream_responses=[looping] * 11,
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg, max_tool_iterations=10)
    with pytest.raises(RuntimeError):
        async for _ in agent.send_stream("loop"):
            pass


@pytest.mark.asyncio
async def test_agent_send_stream_appends_history_same_as_send(fake_ollama):
    """After streaming, agent.history should match what send() leaves behind."""
    client = fake_ollama(
        stream_responses=[[
            TextDelta(content="hi back"),
            Done(finish_reason="stop"),
        ]],
    )
    reg = ToolRegistry()
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    async for _ in agent.send_stream("hello"):
        pass
    assert len(agent.history) == 2
    assert agent.history[0].__class__.__name__ == "UserMessage"
    assert agent.history[1].__class__.__name__ == "AssistantMessage"
    assert agent.history[1].content == "hi back"


@pytest.mark.asyncio
async def test_agent_send_stream_uses_json_in_text_fallback(fake_ollama):
    """If the model emits a tool call as JSON-in-text (no native tool_calls), send_stream
    detects it via parse_text_tool_calls and runs the tool, same as send."""
    json_call = '{"tool": "echo", "arguments": {"text": "hi"}}'
    client = fake_ollama(
        stream_responses=[
            [
                TextDelta(content=json_call),
                Done(finish_reason="stop"),
            ],
            [
                TextDelta(content="echoed: hi"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    frames = [f async for f in agent.send_stream("please echo")]
    types = [type(f).__name__ for f in frames]
    assert "ToolCallStart" in types
    assert "ToolResult" in types
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "echoed: hi"
