import asyncio
import pytest
from pathlib import Path
from kc_core.tools import Tool, ToolRegistry
from kc_sandbox.permissions import PermissionEngine, Tier, Decision
from kc_sandbox.undo import UndoEntry
from kc_supervisor.storage import Storage
from kc_supervisor.audit_tools import (
    RecordingUndoLog, AuditingToolRegistry, make_audit_aware_callback,
    _decision_contextvar, _eid_contextvar,
)


def test_recording_undo_log_captures_eid_in_contextvar(tmp_path):
    log = RecordingUndoLog(tmp_path / "u.db"); log.init()
    _eid_contextvar.set(None)
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write",
        reverse_kind="git-revert", reverse_payload={"share": "r", "sha": "abc"},
    ))
    assert isinstance(eid, int)
    assert _eid_contextvar.get() == eid


def test_auditing_tool_registry_writes_audit_row_on_success(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")
    reg.register(Tool(name="echo", description="", parameters={},
                      impl=lambda text: f"echoed: {text}"))

    _decision_contextvar.set(Decision(allowed=True, tier=Tier.SAFE, source="tier", reason=None))
    _eid_contextvar.set(None)

    result = reg.invoke("echo", {"text": "hi"})
    assert result == "echoed: hi"

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["agent"] == "kc"
    assert rows[0]["tool"] == "echo"
    assert rows[0]["decision"] == "tier"
    assert rows[0]["result"] == "echoed: hi"
    assert rows[0]["undoable"] == 0
    assert storage.get_undo_op_for_audit(rows[0]["id"]) is None


def test_auditing_tool_registry_writes_link_row_when_eid_present(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")

    def journaling_tool(text):
        _eid_contextvar.set(99)  # simulate kc-sandbox tool calling RecordingUndoLog.record
        return f"wrote {text}"

    reg.register(Tool(name="file.write", description="", parameters={},
                      impl=journaling_tool))

    _decision_contextvar.set(Decision(
        allowed=True, tier=Tier.MUTATING, source="tier", reason=None,
    ))
    _eid_contextvar.set(None)

    reg.invoke("file.write", {"text": "x"})

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["undoable"] == 1
    assert storage.get_undo_op_for_audit(rows[0]["id"]) == 99


def test_auditing_tool_registry_writes_audit_row_on_exception(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")

    def boom(text):
        raise ValueError(f"boom: {text}")

    reg.register(Tool(name="bad", description="", parameters={}, impl=boom))

    _decision_contextvar.set(Decision(allowed=True, tier=Tier.SAFE, source="tier", reason=None))
    _eid_contextvar.set(None)

    with pytest.raises(ValueError):
        reg.invoke("bad", {"text": "x"})

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["result"].startswith("Error: ValueError: boom")
    assert rows[0]["undoable"] == 0


def test_auditing_tool_registry_records_destructive_decision_source(tmp_path):
    """Decision.source 'override+callback' is recorded verbatim in the audit row."""
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")
    reg.register(Tool(name="t", description="", parameters={}, impl=lambda: "ok"))

    _decision_contextvar.set(Decision(
        allowed=True, tier=Tier.DESTRUCTIVE, source="override+callback", reason=None,
    ))
    _eid_contextvar.set(None)

    reg.invoke("t", {})
    rows = storage.list_audit()
    assert rows[0]["decision"] == "override+callback"


@pytest.mark.asyncio
async def test_make_audit_aware_callback_sets_decision_contextvar():
    """The callback consults engine.check_async and stashes the Decision."""
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=lambda agent, tool, args: (True, None),
    )
    cb = make_audit_aware_callback(eng, agent_name="kc")
    _decision_contextvar.set(None)

    allowed, reason = await cb("ignored-runtime-name", "file.delete", {})
    assert allowed is True
    d = _decision_contextvar.get()
    assert d is not None
    assert d.tier == Tier.DESTRUCTIVE
    assert d.source == "callback"


@pytest.mark.asyncio
async def test_decision_contextvar_isolation_between_concurrent_tool_calls():
    """Two parallel async tasks don't cross-pollinate decision contextvars."""

    async def fake_tool_call(label: str, decision_source: str, results: dict):
        _decision_contextvar.set(Decision(
            allowed=True, tier=Tier.SAFE, source=decision_source, reason=None,
        ))
        await asyncio.sleep(0.01)
        results[label] = _decision_contextvar.get().source

    results: dict = {}
    await asyncio.gather(
        fake_tool_call("a", "source-a", results),
        fake_tool_call("b", "source-b", results),
    )
    assert results == {"a": "source-a", "b": "source-b"}
