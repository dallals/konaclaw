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


@pytest.mark.asyncio
async def test_engine_check_async_deny_with_async_callback():
    async def async_deny(agent, tool, arguments):
        return (False, "user said no")
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=async_deny,
    )
    d = await eng.check_async(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is False
    assert d.reason == "user said no"
    assert d.source == "callback"


@pytest.mark.asyncio
async def test_engine_check_async_override_plus_callback_attribution():
    """When override raises tier to DESTRUCTIVE and callback is consulted, source is 'override+callback'."""
    async def async_allow(agent, tool, arguments):
        return (True, None)
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},  # default safe
        agent_overrides={"kc": {"file.read": Tier.DESTRUCTIVE}},  # raised for kc
        approval_callback=async_allow,
    )
    d = await eng.check_async(agent="kc", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.source == "override+callback"


@pytest.mark.asyncio
async def test_to_async_agent_callback_returns_async_callable():
    async def async_allow(agent, tool, arguments):
        return (True, None)
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=async_allow,
    )
    cb = eng.to_async_agent_callback("kc")
    # The closure binds to "kc" — runtime agent_name is ignored
    result = cb("ignored-runtime-name", "file.delete", {})
    import inspect as _inspect
    assert _inspect.iscoroutine(result)
    allowed, reason = await result
    assert allowed is True
    assert reason is None


def test_resolver_overrides_tier_map():
    """Tool not in tier_map, but resolver returns SAFE -> allowed without callback."""
    calls = []
    def cb(agent, tool, args):
        calls.append(("cb", agent, tool))
        return (True, None)
    engine = PermissionEngine(
        tier_map={},  # tool unknown — would default to DESTRUCTIVE
        agent_overrides={},
        approval_callback=cb,
        tier_resolvers={"terminal_run": lambda args: Tier.SAFE},
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={"argv": ["ls"]})
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    assert calls == []  # no callback invoked


def test_resolver_returns_destructive_invokes_callback():
    seen = []
    def cb(agent, tool, args):
        seen.append(args)
        return (True, None)
    engine = PermissionEngine(
        tier_map={},
        agent_overrides={},
        approval_callback=cb,
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={"argv": ["rm", "x"]})
    assert d.allowed is True
    assert d.tier == Tier.DESTRUCTIVE
    assert seen == [{"argv": ["rm", "x"]}]


def test_resolver_takes_precedence_over_tier_map():
    engine = PermissionEngine(
        tier_map={"terminal_run": Tier.SAFE},  # static says SAFE
        agent_overrides={},
        approval_callback=AlwaysDeny(reason="nope"),
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},  # dynamic says DESTRUCTIVE
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={})
    assert d.allowed is False
    assert d.tier == Tier.DESTRUCTIVE
    assert d.reason == "nope"


@pytest.mark.asyncio
async def test_resolver_works_in_async_path():
    engine = PermissionEngine(
        tier_map={},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},
    )
    d = await engine.check_async(agent="a", tool="terminal_run", arguments={})
    assert d.allowed is True
    assert d.tier == Tier.DESTRUCTIVE


def test_no_resolver_falls_back_to_tier_map():
    engine = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={},
    )
    d = engine.check(agent="a", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    assert d.source == "tier"
