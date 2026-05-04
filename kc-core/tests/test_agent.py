import pytest
from kc_core.agent import Agent
from kc_core.messages import UserMessage, AssistantMessage
from kc_core.ollama_client import ChatResponse
from kc_core.tools import ToolRegistry


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
