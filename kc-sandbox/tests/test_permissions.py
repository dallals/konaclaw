import pytest
from kc_sandbox.permissions import (
    Tier, PermissionEngine, Decision, AlwaysAllow, AlwaysDeny,
)


def test_safe_tool_auto_allowed():
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.source == "tier"


def test_destructive_tool_routes_to_callback():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={"share": "x", "relpath": "y"})
    assert d.allowed is True
    assert d.source == "callback"


def test_destructive_denied_by_callback():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=AlwaysDeny(reason="user said no"),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is False
    assert "user said no" in (d.reason or "")


def test_per_agent_override_promotes_safe_to_destructive():
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={"kc": {"file.read": Tier.DESTRUCTIVE}},
        approval_callback=AlwaysDeny(reason="nope"),
    )
    d = eng.check(agent="kc", tool="file.read", arguments={})
    assert d.allowed is False


def test_per_agent_override_demotes_destructive_to_safe():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={"kc": {"file.delete": Tier.SAFE}},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is True


def test_unknown_tool_defaults_to_destructive():
    """Spec rule: newly-installed/unknown tools must default destructive."""
    eng = PermissionEngine(
        tier_map={},  # tool unknown
        agent_overrides={},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="mcp.something_new", arguments={})
    assert d.allowed is False  # destructive + AlwaysDeny


def test_override_promote_records_combined_source():
    """When an override raises tier to DESTRUCTIVE and the callback is consulted,
    Decision.source must show both facts ('override+callback') so audit logs can
    distinguish this from a plain default-DESTRUCTIVE → callback flow."""
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={"kc": {"file.read": Tier.DESTRUCTIVE}},
        approval_callback=AlwaysAllow(),
    )
    d = eng.check(agent="kc", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.source == "override+callback"


def test_other_agent_override_does_not_apply():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={"EmailBot": {"file.delete": Tier.SAFE}},
        approval_callback=AlwaysDeny(reason="x"),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is False  # kc still destructive, callback denies


@pytest.mark.asyncio
async def test_engine_supports_async_callback():
    async def async_allow(agent, tool, arguments):
        return (True, None)
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=async_allow,
    )
    d = await eng.check_async(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is True
