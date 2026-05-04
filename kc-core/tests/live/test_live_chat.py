import pytest
from kc_core.agent import Agent
from kc_core.ollama_client import OllamaClient
from kc_core.tools import ToolRegistry


@pytest.mark.asyncio
async def test_live_round_trip(live_ollama_url, live_model):
    client = OllamaClient(base_url=live_ollama_url, model=live_model)
    agent = Agent(
        name="livetest",
        client=client,
        system_prompt="You are terse. Reply with a single short sentence.",
        tools=ToolRegistry(),
    )
    reply = await agent.send("Say the word 'pong'.")
    assert "pong" in reply.content.lower()
