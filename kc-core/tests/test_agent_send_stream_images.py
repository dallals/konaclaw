from pathlib import Path

import pytest

from kc_core.agent import Agent
from kc_core.messages import ImageRef, UserMessage
from kc_core.stream_frames import TextDelta, Done, ChatUsage
from kc_core.tools import ToolRegistry


class _FakeClient:
    model = "fake:model"

    def __init__(self):
        self.last_messages = None

    async def chat_stream(self, *, messages, tools, think=None):
        self.last_messages = messages
        yield TextDelta(content="ok")
        yield ChatUsage(
            input_tokens=1,
            output_tokens=1,
            ttfb_ms=0.1,
            generation_ms=0.1,
            usage_reported=True,
        )
        yield Done(finish_reason="stop")

    async def chat(self, messages, tools):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_send_stream_passes_images_into_history(tmp_path: Path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    client = _FakeClient()
    agent = Agent(
        name="x",
        client=client,
        system_prompt="",
        tools=ToolRegistry(),
        max_tool_iterations=1,
    )
    refs = (ImageRef(path=img, mime="image/png"),)

    frames = []
    async for f in agent.send_stream("describe", images=refs):
        frames.append(f)

    # History last user message has images.
    assert isinstance(agent.history[0], UserMessage)
    assert agent.history[0].content == "describe"
    assert agent.history[0].images == refs

    # Wire format passed to the client: content is a multipart list.
    assert client.last_messages is not None
    user_wire = next(m for m in client.last_messages if m.get("role") == "user")
    assert isinstance(user_wire["content"], list)
    assert any(
        block.get("type") == "image_url"
        and block["image_url"]["url"].startswith("data:image/png;base64,")
        for block in user_wire["content"]
    )


@pytest.mark.asyncio
async def test_send_stream_without_images_keeps_string_content():
    client = _FakeClient()
    agent = Agent(
        name="x",
        client=client,
        system_prompt="",
        tools=ToolRegistry(),
        max_tool_iterations=1,
    )
    frames = []
    async for f in agent.send_stream("hi"):
        frames.append(f)
    user_wire = next(m for m in client.last_messages if m.get("role") == "user")
    assert user_wire["content"] == "hi"  # plain string, not a list
