"""Tests for the delegate_to_agent tool: happy path, loop guard, depth cap,
unknown/degraded targets, and audit row capture."""
import asyncio
import pytest
from dataclasses import dataclass
from kc_core.ollama_client import ChatResponse
from kc_core.stream_frames import TextDelta, Done


@dataclass
class FakeClient:
    """Minimal kc-core-compatible client. Returns a fixed reply."""
    reply: str = "ok"
    model: str = "fake-model"
    calls: list = None

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    async def chat(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        return ChatResponse(text=self.reply, tool_calls=[], finish_reason="stop")

    async def chat_stream(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        yield TextDelta(content=self.reply)
        yield Done(finish_reason="stop")


def _swap_client(deps, name: str, client: FakeClient) -> None:
    rt = deps.registry.get(name)
    assert rt.assembled is not None
    rt.assembled.core_agent.client = client


def _delegate_tool(deps, parent: str):
    return deps.registry.get(parent).assembled.registry.get("delegate_to_agent")


def test_delegate_tool_is_registered_with_safe_tier(deps):
    rt = deps.registry.get("alice")
    assert rt.assembled is not None
    assert "delegate_to_agent" in rt.assembled.registry.names()
    from kc_sandbox.permissions import Tier
    assert rt.assembled.engine.tier_map.get("delegate_to_agent") == Tier.SAFE


def test_delegate_to_unknown_agent_returns_error_string(deps):
    tool = _delegate_tool(deps, "alice")
    out = asyncio.run(tool.impl(target="ghost", message="hello"))
    assert "unknown agent" in out
    assert "ghost" in out


def test_delegate_to_self_is_rejected(deps):
    tool = _delegate_tool(deps, "alice")
    out = asyncio.run(tool.impl(target="alice", message="hello"))
    assert "cannot delegate to self" in out


def test_delegate_returns_target_reply(deps):
    _swap_client(deps, "bob", FakeClient(reply="from-bob"))
    tool = _delegate_tool(deps, "alice")
    out = asyncio.run(tool.impl(target="bob", message="please help"))
    assert out == "from-bob"


def test_delegate_does_not_pollute_target_history(deps):
    _swap_client(deps, "bob", FakeClient(reply="from-bob"))
    bob_history = list(deps.registry.get("bob").assembled.core_agent.history)
    tool = _delegate_tool(deps, "alice")
    asyncio.run(tool.impl(target="bob", message="please help"))
    # History restored after delegation — no UserMessage("please help") left behind.
    assert deps.registry.get("bob").assembled.core_agent.history == bob_history


def test_delegate_depth_limit_blocks_nested_calls(deps):
    """With depth_limit=1, a child agent inside an in-flight delegation
    cannot delegate further — the chain contextvar enforces it."""
    _swap_client(deps, "bob", FakeClient(reply="from-bob"))
    alice_tool = _delegate_tool(deps, "alice")
    bob_tool = _delegate_tool(deps, "bob")

    async def scenario():
        # Simulate alice's delegation to bob being in-flight by setting the
        # contextvar before bob tries to delegate further.
        from kc_supervisor.delegation import _delegation_chain
        token = _delegation_chain.set(("bob",))
        try:
            return await bob_tool.impl(target="alice", message="x")
        finally:
            _delegation_chain.reset(token)

    out = asyncio.run(scenario())
    assert "depth limit" in out


def test_delegate_loop_guard_rejects_cycle(deps):
    """If A → B → A is attempted (A appears in the in-flight chain when B
    tries to delegate to A), the second hop is rejected before running."""
    bob_tool = _delegate_tool(deps, "bob")

    async def scenario():
        from kc_supervisor.delegation import _delegation_chain
        # Pretend alice is the original parent (chain holds "bob"); bob
        # then tries to delegate back to alice — that's a cycle since
        # alice is the user-facing parent.
        # NB: depth_limit=1 also rejects this; to exercise the loop branch
        # we raise the limit first.
        token = _delegation_chain.set(("alice", "bob"))
        try:
            # Use a higher limit just for this scenario by re-creating the tool
            from kc_supervisor.delegation import make_delegate_tool
            t = make_delegate_tool(
                deps.registry._resolve_assembled,
                parent_name="bob",
                depth_limit=10,
            )
            return await t.impl(target="alice", message="x")
        finally:
            _delegation_chain.reset(token)

    out = asyncio.run(scenario())
    assert "loop detected" in out
    assert "alice" in out


def test_delegate_to_degraded_agent_returns_error_string(deps):
    from kc_supervisor.agents import AgentStatus
    rt = deps.registry.get("bob")
    rt.assembled = None
    rt.set_status(AgentStatus.DEGRADED)
    tool = _delegate_tool(deps, "alice")
    out = asyncio.run(tool.impl(target="bob", message="hello"))
    assert "degraded" in out


def test_delegate_writes_audit_row(deps):
    _swap_client(deps, "bob", FakeClient(reply="from-bob"))
    tool = _delegate_tool(deps, "alice")
    asyncio.run(tool.impl(target="bob", message="please help"))
    rows = deps.storage.list_audit(agent="alice")
    delegate_rows = [r for r in rows if r["tool"] == "delegate_to_agent"]
    assert len(delegate_rows) == 1
    assert delegate_rows[0]["result"] == "from-bob"


def test_delegate_chain_is_per_task(deps):
    """The chain contextvar must reset between independent asyncio.run calls
    so a previous run's depth doesn't leak into the next one."""
    _swap_client(deps, "bob", FakeClient(reply="from-bob"))
    tool = _delegate_tool(deps, "alice")
    out1 = asyncio.run(tool.impl(target="bob", message="x"))
    out2 = asyncio.run(tool.impl(target="bob", message="y"))
    assert out1 == "from-bob" and out2 == "from-bob"
