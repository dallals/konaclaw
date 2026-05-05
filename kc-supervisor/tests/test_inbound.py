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

    cid_a = router._conv_by_chat[("telegram", "AAA")]
    cid_b = router._conv_by_chat[("telegram", "BBB")]
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

    cid = router._conv_by_chat[("telegram", "C")]
    msgs = deps.conversations.list_messages(cid)
    assert len(msgs) == 2
    assert isinstance(msgs[0], UserMessage)
    assert msgs[0].content == "ping"
    assert isinstance(msgs[1], AssistantMessage)
    assert msgs[1].content == "answer"
