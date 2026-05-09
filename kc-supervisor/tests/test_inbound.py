from __future__ import annotations
import pytest
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from kc_core.messages import UserMessage, AssistantMessage
from kc_core.stream_frames import Complete, ToolCallStart, ToolResult

from kc_supervisor.agents import AgentRuntime, AgentStatus
from kc_supervisor.inbound import InboundRouter


@dataclass
class _Env:
    channel: str
    chat_id: str
    sender_id: str
    content: str


def _make_async_iter(frames):
    async def _gen(_content):
        for f in frames:
            yield f
    return _gen


class _FakeRoutingTable:
    def __init__(self, default_agent: str, overrides: dict | None = None):
        self.default = default_agent
        self.overrides = overrides or {}

    def route(self, channel: str, chat_id: str) -> str:
        return self.overrides.get((channel, chat_id), self.default)


class _FakeConnector:
    def __init__(self, name: str = "telegram"):
        self.name = name
        self.send = AsyncMock()


class _FakeConnectorRegistry:
    def __init__(self, connectors: dict):
        self._by_name = connectors

    def get(self, name: str):
        return self._by_name[name]

    def all(self):
        return list(self._by_name.values())


def _build_runtime(name: str, frames: list) -> AgentRuntime:
    """Return an AgentRuntime whose assembled.core_agent.send_stream yields frames."""
    assembled = MagicMock()
    assembled.core_agent.send_stream = _make_async_iter(frames)
    assembled.core_agent.history = []
    assembled.core_agent.system_prompt = "base"
    assembled.base_system_prompt = "base"
    assembled.memory_reader = None
    rt = AgentRuntime(
        name=name, model="fake", system_prompt="base",
        yaml_path=None, status=AgentStatus.IDLE, last_error=None,
        assembled=assembled,
    )
    return rt


def _make_registry(name_to_rt: dict):
    reg = MagicMock()

    def _get(n):
        if n not in name_to_rt:
            raise KeyError(n)
        return name_to_rt[n]

    reg.get.side_effect = _get
    return reg


def _make_router(deps, registry, conn_registry, routing_table):
    return InboundRouter(
        registry=registry,
        conversations=deps.conversations,
        conv_locks=deps.conv_locks,
        routing_table=routing_table,
        connector_registry=conn_registry,
    )


@pytest.mark.asyncio
async def test_handle_inbound_routes_to_default_agent(deps):
    reply = AssistantMessage(content="hi back")
    rt = _build_runtime("alice", [Complete(reply=reply)])
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env(channel="telegram", chat_id="123", sender_id="u1", content="hello")
    await router.handle_inbound(env)

    connector.send.assert_awaited_once_with("123", "hi back")


@pytest.mark.asyncio
async def test_handle_inbound_creates_conversation_per_chat(deps):
    reply = AssistantMessage(content="ok")
    # Need a runtime whose send_stream returns a fresh iterator each call
    assembled = MagicMock()
    call_history_rebuilds = []

    async def _gen(content):
        # capture how many history items were assigned by this call
        call_history_rebuilds.append(list(assembled.core_agent.history))
        yield Complete(reply=reply)

    assembled.core_agent.send_stream = _gen
    assembled.core_agent.history = []
    assembled.core_agent.system_prompt = "base"
    assembled.base_system_prompt = "base"
    assembled.memory_reader = None
    rt = AgentRuntime(
        name="alice", model="fake", system_prompt="base",
        yaml_path=None, assembled=assembled,
    )
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env_a1 = _Env("telegram", "AAA", "u1", "first")
    env_a2 = _Env("telegram", "AAA", "u1", "second")
    env_b = _Env("telegram", "BBB", "u2", "hello-other")

    await router.handle_inbound(env_a1)
    await router.handle_inbound(env_a2)
    await router.handle_inbound(env_b)

    storage = deps.conversations.s
    cid_a = storage.get_conv_for_chat("telegram", "AAA", "alice")
    cid_b = storage.get_conv_for_chat("telegram", "BBB", "alice")
    assert cid_a is not None
    assert cid_b is not None
    assert cid_a != cid_b

    msgs_a = deps.conversations.list_messages(cid_a)
    # Two user turns + two assistant replies persisted to A's conversation
    user_msgs = [m for m in msgs_a if isinstance(m, UserMessage)]
    asst_msgs = [m for m in msgs_a if isinstance(m, AssistantMessage)]
    assert len(user_msgs) == 2
    assert len(asst_msgs) == 2


@pytest.mark.asyncio
async def test_handle_inbound_unknown_agent_logs_and_drops(deps):
    registry = _make_registry({})  # no agents
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="nonexistent")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env("telegram", "X", "u", "hi")
    await router.handle_inbound(env)  # must not raise
    connector.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_inbound_degraded_agent_drops(deps):
    rt = AgentRuntime(
        name="alice", model="fake", system_prompt="",
        yaml_path=None, status=AgentStatus.DEGRADED,
        last_error="boom", assembled=None,
    )
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env("telegram", "X", "u", "hi")
    await router.handle_inbound(env)
    connector.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_inbound_persists_user_and_assistant_messages(deps):
    reply = AssistantMessage(content="answer")
    rt = _build_runtime("alice", [Complete(reply=reply)])
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env("telegram", "C", "u", "ping")
    await router.handle_inbound(env)

    cid = deps.conversations.s.get_conv_for_chat("telegram", "C", "alice")
    assert cid is not None
    msgs = deps.conversations.list_messages(cid)
    assert len(msgs) == 2
    assert isinstance(msgs[0], UserMessage)
    assert msgs[0].content == "ping"
    assert isinstance(msgs[1], AssistantMessage)
    assert msgs[1].content == "answer"


@pytest.mark.asyncio
async def test_inbound_router_conversation_persists_across_reconstruction(deps):
    """A reconstructed InboundRouter (simulated supervisor restart) reuses
    the same conversation_id for the same (channel, chat_id, agent) tuple."""
    # Build a runtime whose send_stream can be called multiple times
    assembled = MagicMock()

    async def _gen(content):
        yield Complete(reply=AssistantMessage(content="ok"))

    assembled.core_agent.send_stream = _gen
    assembled.core_agent.history = []
    assembled.core_agent.system_prompt = "base"
    assembled.base_system_prompt = "base"
    assembled.memory_reader = None
    rt = AgentRuntime(
        name="alice", model="fake", system_prompt="base",
        yaml_path=None, assembled=assembled,
    )
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")

    # First router instance: handle a message from alice
    router1 = _make_router(deps, registry, conn_registry, routing)
    env = _Env("telegram", "alice_chat", "alice", "hello")
    await router1.handle_inbound(env)

    # Verify first conv was created and recorded in storage
    storage = deps.conversations.s
    cid_first = storage.get_conv_for_chat("telegram", "alice_chat", "alice")
    assert cid_first is not None, "storage should record the conv_id after first handle"

    # Count conversations before reconstruction
    rows_before = len(storage.list_conversations(agent="alice"))

    # Reconstruct router with the SAME storage (simulated supervisor restart)
    router2 = _make_router(deps, registry, conn_registry, routing)
    await router2.handle_inbound(env)

    # Verify the SAME conversation was reused — no new row created
    rows_after = len(storage.list_conversations(agent="alice"))
    assert rows_after == rows_before, (
        f"Expected no new conversation created after restart, "
        f"but row count went from {rows_before} to {rows_after}"
    )

    # Verify same cid is still in storage
    cid_second = storage.get_conv_for_chat("telegram", "alice_chat", "alice")
    assert cid_second == cid_first, (
        f"Expected same conv_id {cid_first} after restart, got {cid_second}"
    )


@pytest.mark.asyncio
async def test_inbound_persists_usage_on_assistant_message(deps):
    import json as _j
    from kc_core.stream_frames import TurnUsage

    reply = AssistantMessage(content="hi back")
    frames = [
        TurnUsage(call_index=0, input_tokens=100, output_tokens=4,
                  ttfb_ms=40.0, generation_ms=80.0, usage_reported=True),
        Complete(reply=reply),
    ]
    rt = _build_runtime("alice", frames)
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env(channel="telegram", chat_id="C1", sender_id="u1", content="hi")
    await router.handle_inbound(env)

    cid = deps.conversations.s.get_conv_for_chat("telegram", "C1", "alice")
    assert cid is not None
    rows = deps.storage.list_messages(cid)
    asst = [r for r in rows if r["role"] == "assistant"][-1]
    assert asst["usage_json"] is not None
    parsed = _j.loads(asst["usage_json"])
    assert parsed == {
        "input_tokens": 100,
        "output_tokens": 4,
        "ttfb_ms": 40.0,
        "generation_ms": 80.0,
        "calls": 1,
        "usage_reported": True,
    }


@pytest.mark.asyncio
async def test_inbound_no_usage_persisted_when_stream_errors(deps):
    from kc_core.stream_frames import TurnUsage

    async def _gen(_content):
        yield TurnUsage(call_index=0, input_tokens=10, output_tokens=2,
                        ttfb_ms=20.0, generation_ms=30.0, usage_reported=True)
        raise RuntimeError("boom")

    rt = _build_runtime("alice", [])
    rt.assembled.core_agent.send_stream = _gen
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env(channel="telegram", chat_id="C2", sender_id="u1", content="hi")
    await router.handle_inbound(env)  # must not raise

    cid = deps.conversations.s.get_conv_for_chat("telegram", "C2", "alice")
    if cid is not None:
        rows = deps.storage.list_messages(cid)
        assert all(r["role"] != "assistant" for r in rows)
