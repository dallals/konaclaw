import json
import os
import pytest
import asyncio
from pathlib import Path

from kc_core.tools import ToolRegistry
from kc_sandbox.permissions import PermissionEngine, Tier, AlwaysAllow, AlwaysDeny

from kc_terminal.config import TerminalConfig
from kc_terminal.tools import build_terminal_tool, terminal_tier_resolver


@pytest.fixture
def cfg(tmp_path):
    return TerminalConfig(
        roots=(tmp_path,),
        secret_prefixes=("KC_TEST_",),
        default_timeout_seconds=10,
        max_timeout_seconds=30,
        output_cap_bytes=4096,
    )


@pytest.mark.asyncio
async def test_safe_call_via_resolver_short_circuits(cfg, tmp_path):
    """SAFE-tier call (ls) resolves to Tier.SAFE — engine auto-allows without callback."""
    deny_called = []
    def deny(agent, tool, args):
        deny_called.append((agent, tool))
        return (False, "should not be called for SAFE")
    engine = PermissionEngine(
        tier_map={}, agent_overrides={}, approval_callback=deny,
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(
        agent="a",
        tool="terminal_run",
        arguments={"argv": ["ls"], "cwd": str(tmp_path)},
    )
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    assert deny_called == []


@pytest.mark.asyncio
async def test_destructive_call_routes_through_engine(cfg, tmp_path):
    """DESTRUCTIVE tier (rm) routes to approval callback."""
    seen = []
    def cb(agent, tool, args):
        seen.append(args)
        return (True, None)
    engine = PermissionEngine(
        tier_map={}, agent_overrides={}, approval_callback=cb,
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(
        agent="a",
        tool="terminal_run",
        arguments={"argv": ["rm", "nothing-here"], "cwd": str(tmp_path)},
    )
    assert d.tier == Tier.DESTRUCTIVE
    assert d.allowed is True
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_mutating_collapses_to_destructive(cfg, tmp_path):
    """`git commit` RawTier is MUTATING. Resolver maps it to engine Tier.DESTRUCTIVE
    so MUTATING commands still prompt under the existing engine."""
    cb_called = []
    def cb(agent, tool, args):
        cb_called.append(True)
        return (True, None)
    engine = PermissionEngine(
        tier_map={}, agent_overrides={}, approval_callback=cb,
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(
        agent="a",
        tool="terminal_run",
        arguments={"argv": ["git", "commit", "-m", "x"], "cwd": str(tmp_path)},
    )
    assert d.tier == Tier.DESTRUCTIVE
    assert len(cb_called) == 1


@pytest.mark.asyncio
async def test_tool_impl_safe_returns_success_json(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result_str = await tool.impl(argv=["ls"], cwd=str(tmp_path))
    result = json.loads(result_str)
    assert result["exit_code"] == 0
    assert result["tier"] == "SAFE"
    assert result["mode"] == "argv"
    assert result["timed_out"] is False
    assert result["cwd"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_tool_impl_records_raw_tier_in_result(cfg, tmp_path):
    """RawTier value appears in the result JSON for audit clarity."""
    tool = build_terminal_tool(cfg)
    # SAFE example (echo is in SAFE_COMMANDS for argv mode).
    result = json.loads(await tool.impl(argv=["echo", "x"], cwd=str(tmp_path)))
    assert result["tier"] == "SAFE"
    # MUTATING example (git commit is MUTATING via git subcommand rule).
    result = json.loads(await tool.impl(
        argv=["git", "commit", "-m", "x"],
        cwd=str(tmp_path),
    ))
    # Engine maps MUTATING -> DESTRUCTIVE but the impl preserves the rich
    # RawTier label in the result.
    assert result["tier"] == "MUTATING"


@pytest.mark.asyncio
async def test_tool_impl_bad_cwd_returns_error(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], cwd="/etc"))
    assert result["error"] == "cwd_outside_roots"
    assert "cwd" in result


@pytest.mark.asyncio
async def test_tool_impl_both_argv_and_command(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], command="ls", cwd=str(tmp_path)))
    assert result["error"] == "both_argv_and_command_provided"


@pytest.mark.asyncio
async def test_resolver_fails_closed_on_both_argv_and_command():
    """Regression: when both argv and command are set, the resolver must NOT
    silently classify the argv path. Engine must see DESTRUCTIVE."""
    tier = terminal_tier_resolver({"argv": ["ls"], "command": "ls", "cwd": "/tmp"})
    assert tier == Tier.DESTRUCTIVE


@pytest.mark.asyncio
async def test_tool_impl_neither_argv_nor_command(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(cwd=str(tmp_path)))
    assert result["error"] == "must_provide_argv_or_command"


@pytest.mark.asyncio
async def test_tool_impl_relative_cwd_rejected(cfg):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], cwd="relative/path"))
    assert result["error"] == "cwd_not_absolute"


@pytest.mark.asyncio
async def test_tool_impl_cwd_does_not_exist(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    missing = tmp_path / "no-such-dir"
    result = json.loads(await tool.impl(argv=["ls"], cwd=str(missing)))
    # Could be either cwd_does_not_exist (path-validation phase) or runtime — but
    # path validation runs before subprocess, so we should see the validation error.
    assert result["error"] == "cwd_does_not_exist"


@pytest.mark.asyncio
async def test_tool_impl_empty_argv(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=[], cwd=str(tmp_path)))
    assert result["error"] == "empty_argv"


@pytest.mark.asyncio
async def test_tool_registers_in_registry(cfg):
    tool = build_terminal_tool(cfg)
    reg = ToolRegistry()
    reg.register(tool)
    assert "terminal_run" in reg.names()


@pytest.mark.asyncio
async def test_env_secrets_stripped_in_child(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("KC_TEST_SECRET", "should-be-stripped")
    # Ensure PATH is set (subprocess needs it to find sh)
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(
        argv=["sh", "-c", "echo SECRET=${KC_TEST_SECRET:-UNSET}"],
        cwd=str(tmp_path),
    ))
    assert result["exit_code"] == 0
    assert "SECRET=UNSET" in result["stdout"]


@pytest.mark.asyncio
async def test_resolver_handles_missing_argv(cfg):
    """The resolver gets called by the engine BEFORE the impl validates args.
    If args are malformed (e.g. neither argv nor command), the resolver must
    return Tier.DESTRUCTIVE (fail-closed) rather than crash."""
    tier = terminal_tier_resolver({"cwd": "/tmp"})
    assert tier == Tier.DESTRUCTIVE


@pytest.mark.asyncio
async def test_resolver_returns_safe_for_safe_argv():
    tier = terminal_tier_resolver({"argv": ["ls"]})
    assert tier == Tier.SAFE


@pytest.mark.asyncio
async def test_resolver_returns_destructive_for_destructive_argv():
    tier = terminal_tier_resolver({"argv": ["rm", "x"]})
    assert tier == Tier.DESTRUCTIVE


@pytest.mark.asyncio
async def test_resolver_command_mode_destructive():
    tier = terminal_tier_resolver({"command": "rm -rf x"})
    assert tier == Tier.DESTRUCTIVE


@pytest.mark.asyncio
async def test_tool_impl_timeout(cfg, tmp_path):
    """Timeout under the full impl flow returns timed_out=True with tier annotated."""
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(
        argv=["sleep", "5"],
        cwd=str(tmp_path),
        timeout_seconds=1,
    ))
    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["tier"] == "MUTATING"  # sleep isn't in SAFE_COMMANDS


@pytest.mark.asyncio
async def test_tool_impl_shell_mode_success(cfg, tmp_path):
    """Shell-mode (command=...) success path through the impl."""
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(
        command="echo $((1+1))",
        cwd=str(tmp_path),
    ))
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "2"
    assert result["mode"] == "command"
    # Shell mode is never SAFE.
    assert result["tier"] in ("MUTATING", "DESTRUCTIVE")


@pytest.mark.asyncio
async def test_tool_impl_truncation_under_cap(cfg, tmp_path):
    """Output longer than cfg.output_cap_bytes gets head+tail truncated by the runner."""
    small_cap_cfg = TerminalConfig(
        roots=cfg.roots,
        secret_prefixes=cfg.secret_prefixes,
        default_timeout_seconds=cfg.default_timeout_seconds,
        max_timeout_seconds=cfg.max_timeout_seconds,
        output_cap_bytes=1024,  # small cap
    )
    tool = build_terminal_tool(small_cap_cfg)
    result = json.loads(await tool.impl(
        command="python3 -c \"import sys; sys.stdout.write('A'*4000); sys.stdout.write('B'*4000)\"",
        cwd=str(tmp_path),
    ))
    assert result["exit_code"] == 0
    assert result["stdout_truncated"] is True
    assert "[TRUNCATED" in result["stdout"]


@pytest.mark.asyncio
async def test_tool_impl_description_echoed(cfg, tmp_path):
    """description parameter is echoed into the result JSON when provided."""
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(
        argv=["ls"],
        cwd=str(tmp_path),
        description="list workdir",
    ))
    assert result["description"] == "list workdir"
    # When description is not provided, it should not appear in the result.
    result2 = json.loads(await tool.impl(argv=["ls"], cwd=str(tmp_path)))
    assert "description" not in result2
