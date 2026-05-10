from __future__ import annotations
import pytest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from kc_core.messages import AssistantMessage
from kc_core.stream_frames import Complete

from kc_supervisor.agents import AgentRuntime, AgentStatus
from kc_supervisor.inbound import InboundRouter
from kc_skills import SkillIndex


@dataclass
class _Env:
    channel: str
    chat_id: str
    sender_id: str
    content: str


class _FakeRoutingTable:
    def __init__(self, default_agent: str):
        self.default = default_agent

    def route(self, channel: str, chat_id: str) -> str:
        return self.default


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


def _build_runtime_capturing(captured: list, reply_text: str = "ok") -> AgentRuntime:
    """Runtime whose send_stream captures the content arg and yields one Complete."""
    assembled = MagicMock()

    async def _gen(content):
        captured.append(content)
        yield Complete(reply=AssistantMessage(content=reply_text))

    assembled.core_agent.send_stream = _gen
    assembled.core_agent.history = []
    assembled.core_agent.system_prompt = "base"
    assembled.base_system_prompt = "base"
    assembled.memory_reader = None
    return AgentRuntime(
        name="alice", model="fake", system_prompt="base",
        yaml_path=None, status=AgentStatus.IDLE, last_error=None,
        assembled=assembled,
    )


def _make_registry(name_to_rt: dict):
    reg = MagicMock()

    def _get(n):
        if n not in name_to_rt:
            raise KeyError(n)
        return name_to_rt[n]

    reg.get.side_effect = _get
    return reg


def _seed_skill(root: Path, name: str = "hello", body: str = "Skill body.") -> SkillIndex:
    sdir = root / name
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: D\n---\n\n{body}\n"
    )
    return SkillIndex(root)


@pytest.mark.asyncio
async def test_inbound_slash_command_loads_skill(deps, tmp_path):
    """Telegram /hello → loaded body to send_stream, original text persisted."""
    captured: list[str] = []
    rt = _build_runtime_capturing(captured)
    registry = _make_registry({"alice": rt})
    conn_registry = _FakeConnectorRegistry({"telegram": _FakeConnector("telegram")})

    skill_index = _seed_skill(tmp_path / "skills", body="Load this skill body.")

    router = InboundRouter(
        registry=registry,
        conversations=deps.conversations,
        conv_locks=deps.conv_locks,
        routing_table=_FakeRoutingTable("alice"),
        connector_registry=conn_registry,
        skill_index=skill_index,
    )

    env = _Env(channel="telegram", chat_id="123", sender_id="u1",
               content="/hello foo")
    await router.handle_inbound(env)

    # send_stream saw the loaded body.
    assert len(captured) == 1
    assert "[Skill activation: hello]" in captured[0]
    assert "Load this skill body." in captured[0]

    # Persisted user message is the original /hello foo.
    cid = deps.conversations.get_or_create(
        channel="telegram", chat_id="123", agent="alice",
    )
    msgs = deps.conversations.list_messages(cid)
    user_msgs = [m for m in msgs if type(m).__name__ == "UserMessage"]
    assert any(m.content == "/hello foo" for m in user_msgs)


@pytest.mark.asyncio
async def test_inbound_unknown_slash_passes_through(deps, tmp_path):
    """An unrecognized /command is sent verbatim to send_stream and persisted as-is."""
    captured: list[str] = []
    rt = _build_runtime_capturing(captured)
    registry = _make_registry({"alice": rt})
    conn_registry = _FakeConnectorRegistry({"telegram": _FakeConnector("telegram")})

    (tmp_path / "skills").mkdir()
    skill_index = SkillIndex(tmp_path / "skills")

    router = InboundRouter(
        registry=registry,
        conversations=deps.conversations,
        conv_locks=deps.conv_locks,
        routing_table=_FakeRoutingTable("alice"),
        connector_registry=conn_registry,
        skill_index=skill_index,
    )

    env = _Env(channel="telegram", chat_id="456", sender_id="u1",
               content="/nope foo")
    await router.handle_inbound(env)

    assert captured == ["/nope foo"]
