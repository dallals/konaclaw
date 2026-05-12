import asyncio, pytest
from kc_subagents.runner import EphemeralInstance, SubagentRunner, InstanceResult
from pathlib import Path
from kc_subagents.templates import SubagentTemplate
from kc_subagents.runner import template_to_agent_config
from unittest.mock import MagicMock, AsyncMock

def test_template_to_agent_config_basic():
    t = SubagentTemplate(
        name="web-researcher", model="claude-opus-4-7",
        system_prompt="research things",
        tools={"web_search": {"budget": 20}, "skill_view": {}},
        timeout_seconds=300, max_tool_calls=30,
        source_path=Path("/tmp/web-researcher.yaml"),
    )
    cfg = template_to_agent_config(t, instance_id="ep_abc123", parent_agent="Kona-AI")
    assert cfg.name == "Kona-AI/ep_abc123/web-researcher"
    assert cfg.model == "claude-opus-4-7"
    assert cfg.system_prompt == "research things"
    assert set(cfg.tool_whitelist) == {"web_search", "skill_view"}
    assert cfg.tool_config == {"web_search": {"budget": 20}, "skill_view": {}}


class FakeAssembledAgent:
    def __init__(self, reply_text="hello world"):
        self._reply = reply_text
        self.core_agent = MagicMock()
        async def send(message):
            return MagicMock(content=self._reply)
        self.core_agent.send = send
        self.core_agent.history = []

@pytest.mark.asyncio
async def test_instance_run_ok_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))
    fake = FakeAssembledAgent("answer text")
    emitted = []
    inst = EphemeralInstance(
        instance_id="ep_a",
        template=t,
        parent_agent="Kona-AI",
        parent_conversation_id="conv_1",
        task="do thing",
        context=None,
        label="t1",
        effective_timeout=10,
        assembled=fake,
        on_frame=emitted.append,
        audit_start=lambda **kw: None,
        audit_finish=lambda **kw: None,
    )
    result: InstanceResult = await inst.run()
    assert result.status == "ok"
    assert result.reply == "answer text"
    assert any(f["type"] == "subagent_started"  for f in emitted)
    assert any(f["type"] == "subagent_finished" for f in emitted)

@pytest.mark.asyncio
async def test_runner_spawn_and_get_future():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))
    fake = FakeAssembledAgent("done")
    runner = SubagentRunner(
        build_assembled=lambda cfg: fake,
        audit_start=lambda **kw: None,
        audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="go", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    assert handle.startswith("ep_")
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "ok"
    assert result.reply == "done"

@pytest.mark.asyncio
async def test_instance_run_error_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class BadAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                raise RuntimeError("boom")
            self.core_agent.send = send
            self.core_agent.history = []

    inst = EphemeralInstance(
        instance_id="ep_e", template=t, parent_agent="Kona-AI",
        parent_conversation_id="conv_1", task="x", context=None, label=None,
        effective_timeout=5, assembled=BadAgent(),
        on_frame=lambda f: None,
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
    )
    result = await inst.run()
    assert result.status == "error"
    assert "boom" in (result.error or "")

@pytest.mark.asyncio
async def test_instance_run_timeout_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class SlowAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(10)
            self.core_agent.send = send
            self.core_agent.history = []

    inst = EphemeralInstance(
        instance_id="ep_t", template=t, parent_agent="Kona-AI",
        parent_conversation_id="conv_1", task="x", context=None, label=None,
        effective_timeout=1, assembled=SlowAgent(),
        on_frame=lambda f: None,
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
    )
    result = await inst.run()
    assert result.status == "timeout"
    assert "1s" in (result.error or "")

@pytest.mark.asyncio
async def test_runner_stop_yields_stopped_status():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class HangAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(60)
            self.core_agent.send = send
            self.core_agent.history = []

    runner = SubagentRunner(
        build_assembled=lambda cfg: HangAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="x", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    await asyncio.sleep(0.05)
    assert runner.stop(handle) is True
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "stopped"

@pytest.mark.asyncio
async def test_per_conversation_cap(monkeypatch):
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class HangAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(60)
            self.core_agent.send = send
            self.core_agent.history = []

    runner = SubagentRunner(
        build_assembled=lambda cfg: HangAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handles = []
    for _ in range(runner.PER_CONV_CAP):
        handles.append(runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=None,
        ))
    with pytest.raises(RuntimeError, match="too many in-flight"):
        runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=None,
        )
    # Cleanup so test process exits cleanly.
    for h in handles:
        runner.stop(h)

def test_timeout_override_too_large_rejected():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         timeout_seconds=120, source_path=Path("/tmp/x.yaml"))
    runner = SubagentRunner(
        build_assembled=lambda cfg: MagicMock(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    with pytest.raises(RuntimeError, match="exceeds template max"):
        runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=999,
        )
