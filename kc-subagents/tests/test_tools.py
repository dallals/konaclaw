import asyncio, pytest, json
from pathlib import Path
from unittest.mock import MagicMock
from kc_subagents.templates import SubagentTemplate, SubagentIndex
from kc_subagents.runner import SubagentRunner
from kc_subagents.tools import build_subagent_tools

class FakeOkAgent:
    def __init__(self):
        self.core_agent = MagicMock()
        async def send(m): return MagicMock(content="done")
        self.core_agent.send = send
        self.core_agent.history = []

def _index_with(tmp_path, body: str) -> SubagentIndex:
    (tmp_path / "x.yaml").write_text(body)
    return SubagentIndex(tmp_path)

@pytest.mark.asyncio
async def test_spawn_then_await_one(tmp_path):
    idx = _index_with(tmp_path, "name: x\nmodel: m\nsystem_prompt: y\n")
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    spawn = next(t for t in tools if t.name == "spawn_subagent")
    awaiter = next(t for t in tools if t.name == "await_subagents")
    spawn_out = json.loads(await spawn.impl(template="x", task="go"))
    assert spawn_out["status"] == "running"
    handle = spawn_out["subagent_id"]
    await_out = json.loads(await awaiter.impl(subagent_ids=[handle]))
    assert await_out[0]["status"] == "ok"
    assert await_out[0]["reply"]  == "done"

@pytest.mark.asyncio
async def test_spawn_unknown_template_returns_error_string(tmp_path):
    idx = SubagentIndex(tmp_path)
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    spawn = next(t for t in tools if t.name == "spawn_subagent")
    result = await spawn.impl(template="missing", task="x")
    assert "error: unknown template" in result

@pytest.mark.asyncio
async def test_await_unknown_handle_reports_error_row(tmp_path):
    idx = SubagentIndex(tmp_path)
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    awaiter = next(t for t in tools if t.name == "await_subagents")
    out = json.loads(await awaiter.impl(subagent_ids=["ep_nope"]))
    assert out[0]["status"] == "error"
    assert "unknown subagent_id" in out[0]["error"]
