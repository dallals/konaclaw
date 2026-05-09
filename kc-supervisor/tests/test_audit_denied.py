from __future__ import annotations
import json
from pathlib import Path

import pytest

from kc_sandbox.permissions import Decision, PermissionEngine, Tier
from kc_supervisor.audit_tools import make_audit_aware_callback
from kc_supervisor.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "db.sqlite")
    s.init()
    return s


@pytest.fixture
def engine_denying() -> PermissionEngine:
    # DESTRUCTIVE tier + callback that always denies → Decision.allowed=False
    return PermissionEngine(
        tier_map={"dangerous_tool": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (False, "policy"),
    )


@pytest.fixture
def engine_allowing() -> PermissionEngine:
    return PermissionEngine(
        tier_map={"safe_tool": Tier.SAFE},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (True, None),
    )


@pytest.mark.asyncio
async def test_denied_tier_writes_audit_row(storage, engine_denying) -> None:
    cb = make_audit_aware_callback(
        engine_denying, agent_name="kona", storage=storage,
    )
    allowed, reason = await cb("kona", "dangerous_tool", {"x": 1})

    assert allowed is False
    rows = storage.list_audit(agent="kona")
    assert len(rows) == 1
    assert rows[0]["tool"] == "dangerous_tool"
    assert rows[0]["decision"] == "denied"
    parsed = json.loads(rows[0]["result"])
    assert "reason" in parsed and "source" in parsed
    assert rows[0]["undoable"] == 0


@pytest.mark.asyncio
async def test_user_rejected_approval_writes_audit_row(storage) -> None:
    engine = PermissionEngine(
        tier_map={"sketchy_tool": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (False, "user said no"),
    )
    cb = make_audit_aware_callback(engine, agent_name="kona", storage=storage)
    allowed, _ = await cb("kona", "sketchy_tool", {})

    assert allowed is False
    rows = storage.list_audit(agent="kona")
    assert len(rows) == 1
    assert rows[0]["decision"] == "denied"


@pytest.mark.asyncio
async def test_allowed_call_does_not_write_denied_row(storage, engine_allowing) -> None:
    cb = make_audit_aware_callback(
        engine_allowing, agent_name="kona", storage=storage,
    )
    allowed, _ = await cb("kona", "safe_tool", {})

    # Allowed → tool runs, AuditingToolRegistry writes its own row later.
    # The callback itself MUST NOT write a duplicate row.
    assert allowed is True
    assert storage.list_audit(agent="kona") == []
