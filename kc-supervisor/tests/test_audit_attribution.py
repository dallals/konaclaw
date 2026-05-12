from pathlib import Path
import json
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.approvals import subagent_attribution_var


def _new(tmp_path: Path) -> Storage:
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init()
    return s


def test_append_audit_accepts_attribution_kwargs(tmp_path):
    s = _new(tmp_path)
    audit_id = s.append_audit(
        agent="Kona-AI/ep_abc/coder", tool="terminal_run",
        args_json=json.dumps({"argv": ["ls"]}),
        decision="tier", result="ok", undoable=False,
        parent_agent="Kona-AI", subagent_id="ep_abc",
        subagent_template="coder",
    )
    assert audit_id > 0
    with s.connect() as c:
        row = c.execute(
            "SELECT parent_agent, subagent_id, subagent_template FROM audit WHERE id=?",
            (audit_id,),
        ).fetchone()
    assert row["parent_agent"]      == "Kona-AI"
    assert row["subagent_id"]       == "ep_abc"
    assert row["subagent_template"] == "coder"


def test_append_audit_without_attribution_leaves_cols_null(tmp_path):
    s = _new(tmp_path)
    audit_id = s.append_audit(
        agent="Kona-AI", tool="terminal_run",
        args_json=json.dumps({"argv": ["ls"]}),
        decision="tier", result="ok", undoable=False,
    )
    with s.connect() as c:
        row = c.execute(
            "SELECT parent_agent, subagent_id, subagent_template FROM audit WHERE id=?",
            (audit_id,),
        ).fetchone()
    assert row["parent_agent"] is None
    assert row["subagent_id"] is None
    assert row["subagent_template"] is None


@pytest.mark.asyncio
async def test_auditing_tool_registry_picks_up_attribution(tmp_path):
    """When the contextvar is set, a tool's audit row carries the attribution."""
    from kc_core.tools import Tool
    from kc_supervisor.audit_tools import AuditingToolRegistry

    s = _new(tmp_path)
    reg = AuditingToolRegistry(audit_storage=s, agent_name="Kona-AI/ep_abc/coder")

    async def impl(): return "ok"
    reg.register(Tool(name="terminal_run", description="", parameters={"type":"object"}, impl=impl))

    token = subagent_attribution_var.set(
        {"parent_agent": "Kona-AI", "subagent_id": "ep_abc"}
    )
    try:
        wrapped = reg.get("terminal_run")
        result = await wrapped.impl()
        assert result == "ok"
    finally:
        subagent_attribution_var.reset(token)

    with s.connect() as c:
        rows = c.execute(
            "SELECT agent, parent_agent, subagent_id, subagent_template FROM audit ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    assert rows["parent_agent"]      == "Kona-AI"
    assert rows["subagent_id"]       == "ep_abc"
    # Template extracted from the synthetic agent name "Kona-AI/ep_abc/coder"
    assert rows["subagent_template"] == "coder"
