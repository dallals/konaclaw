import asyncio
import pytest
import yaml
from pathlib import Path
from kc_core.config import AgentConfig
from kc_sandbox.shares import SharesRegistry, ShareError
from kc_sandbox.permissions import Tier
from kc_supervisor.storage import Storage
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.assembly import AssembledAgent, assemble_agent


@pytest.fixture
def home(tmp_path):
    """A populated KC_HOME with one share and one agent yaml."""
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)

    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: qwen2.5:7b\nsystem_prompt: I am alice.\n"
    )
    return home


def test_assemble_agent_happy_path(home):
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="I am alice.")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )

    assert isinstance(a, AssembledAgent)
    assert a.name == "alice"
    assert a.system_prompt == "I am alice."
    tool_names = a.registry.names()
    assert "file.read" in tool_names
    assert "file.write" in tool_names
    assert "file.list" in tool_names
    assert "file.delete" in tool_names
    assert a.ollama_client.model == "qwen2.5:7b"
    assert "main" in a.journals
    assert a.undo_log is not None
    assert a.engine.tier_map["file.delete"] == Tier.DESTRUCTIVE
    assert a.core_agent.name == "alice"


def test_assemble_agent_uses_default_model_when_cfg_omits(home):
    """If cfg.model is empty, default_model is used for the OllamaClient."""
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
        ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    assert a.ollama_client.model == "qwen2.5:7b"


def test_assemble_agent_applies_permission_overrides(home):
    """permission_overrides arg gets wired into engine.agent_overrides[agent_name]."""
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        permission_overrides={"file.read": Tier.DESTRUCTIVE},
    )
    assert a.engine.agent_overrides["alice"]["file.read"] == Tier.DESTRUCTIVE


def test_assemble_agent_raises_on_bad_share_path(home):
    """Bad share path: SharesRegistry.from_yaml itself raises (Share.__post_init__ validates is_dir)."""
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "nonexistent_share"), "mode": "read-write"}],
    }))
    with pytest.raises(ShareError):
        SharesRegistry.from_yaml(home / "config" / "shares.yaml")


def test_assemble_agent_uses_audit_aware_callback(home):
    """The Agent's permission_check is the audit-aware variant — calling it sets the decision contextvar."""
    from kc_supervisor.audit_tools import _decision_contextvar
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
        ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    cb = a.core_agent.permission_check
    assert cb is not None
    coro = cb("alice", "file.read", {"share": "main", "relpath": "x"})
    import inspect
    assert inspect.iscoroutine(coro)

    async def runner():
        _decision_contextvar.set(None)
        await coro
        d = _decision_contextvar.get()
        assert d is not None
        # file.read is SAFE in the default tier map; broker isn't called
        assert d.tier == Tier.SAFE

    asyncio.run(runner())


def test_assemble_agent_with_memory_root_registers_memory_tools_and_prepends_prefix(home, tmp_path):
    """When memory_root is supplied, agents get memory.read/append/replace
    tools and any existing user.md/agent MEMORY.md is injected as a system
    prompt prefix."""
    from kc_memory.store import MemoryStore
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    mem_root = tmp_path / "memory"
    mem_root.mkdir()
    s = MemoryStore(mem_root); s.init()
    s.write_user("Name: Sammy")
    s.write_agent("alice", "alice remembers things.")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="I am alice.")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        memory_root=mem_root,
    )

    tool_names = a.registry.names()
    assert {"memory.read", "memory.append", "memory.replace"} <= set(tool_names)
    assert a.engine.tier_map["memory.read"] == Tier.SAFE
    assert a.engine.tier_map["memory.append"] == Tier.MUTATING
    assert a.engine.tier_map["memory.replace"] == Tier.MUTATING
    assert "Name: Sammy" in a.system_prompt
    assert "alice remembers things" in a.system_prompt
    assert "I am alice." in a.system_prompt
    assert a.base_system_prompt == "I am alice."
    assert a.memory_reader is not None


def test_assemble_agent_without_memory_root_does_not_register_memory_tools(home):
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    assert "memory.read" not in a.registry.names()
    assert a.memory_reader is None
    assert a.base_system_prompt == "hi"  # equals system_prompt when no memory
    assert a.system_prompt == "hi"


# ------------------------------------------------------------------ Google
# (kc-connectors integration — wave 3a)

GMAIL_TOOL_NAMES = {"gmail.search", "gmail.read_thread", "gmail.draft", "gmail.send"}
GCAL_TOOL_NAMES = {"gcal.list_events", "gcal.create_event", "gcal.update_event", "gcal.delete_event"}


def _basic_assemble_kwargs(home):
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")
    return dict(
        cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
        ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )


def test_assemble_with_gmail_service_registers_4_tools(home):
    from unittest.mock import MagicMock
    a = assemble_agent(**_basic_assemble_kwargs(home), gmail_service=MagicMock())
    names = set(a.registry.names())
    assert GMAIL_TOOL_NAMES <= names
    # gcal not provided → gcal tools absent
    assert not (GCAL_TOOL_NAMES & names)


def test_assemble_with_gcal_service_registers_4_tools(home):
    from unittest.mock import MagicMock
    a = assemble_agent(**_basic_assemble_kwargs(home), gcal_service=MagicMock())
    names = set(a.registry.names())
    assert GCAL_TOOL_NAMES <= names
    assert not (GMAIL_TOOL_NAMES & names)


def test_assemble_without_google_services_no_google_tools(home):
    a = assemble_agent(**_basic_assemble_kwargs(home))
    names = set(a.registry.names())
    assert not (GMAIL_TOOL_NAMES & names)
    assert not (GCAL_TOOL_NAMES & names)


def test_google_tool_tiers_match_expected(home):
    from unittest.mock import MagicMock
    a = assemble_agent(
        **_basic_assemble_kwargs(home),
        gmail_service=MagicMock(),
        gcal_service=MagicMock(),
    )
    expected = {
        "gmail.search":      Tier.SAFE,
        "gmail.read_thread": Tier.SAFE,
        "gmail.draft":       Tier.MUTATING,
        "gmail.send":        Tier.DESTRUCTIVE,
        "gcal.list_events":  Tier.SAFE,
        "gcal.create_event": Tier.DESTRUCTIVE,
        "gcal.update_event": Tier.DESTRUCTIVE,
        "gcal.delete_event": Tier.DESTRUCTIVE,
    }
    for name, tier in expected.items():
        assert a.engine.tier_map[name] == tier, f"{name} tier mismatch"


# ------------------------------------------------------------------ Zapier
# (kc-zapier integration — wave 2)

def _make_fake_mcp_tool(name: str, description: str = "fake"):
    """A minimal kc_core.tools.Tool-shaped object, sufficient for registry."""
    from kc_core.tools import Tool

    async def _impl(**kwargs):
        return "ok"

    return Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
        impl=_impl,
    )


class _FakeMCPManager:
    """Mocks the slice of MCPManager that assembly.py touches: names() and all_tools()."""
    def __init__(self, server_names: list[str], tools: list):
        self._names = server_names
        self._tools = tools

    def names(self) -> list[str]:
        return list(self._names)

    def all_tools(self) -> list:
        return list(self._tools)


def test_assemble_with_zapier_in_manager_registers_meta_tool(home):
    fake_zap_tool = _make_fake_mcp_tool("mcp.zapier.send_slack", "send a slack message")
    mgr = _FakeMCPManager(server_names=["zapier"], tools=[fake_zap_tool])

    a = assemble_agent(**_basic_assemble_kwargs(home), mcp_manager=mgr)
    names = set(a.registry.names())
    assert "find_or_install_zap" in names
    assert a.engine.tier_map["find_or_install_zap"] == Tier.SAFE


def test_assemble_without_zapier_no_meta_tool(home):
    fake_other_tool = _make_fake_mcp_tool("mcp.fs.read", "read fs")
    mgr = _FakeMCPManager(server_names=["fs"], tools=[fake_other_tool])

    a = assemble_agent(**_basic_assemble_kwargs(home), mcp_manager=mgr)
    names = set(a.registry.names())
    assert "find_or_install_zap" not in names


def test_skill_tools_registered_when_skill_index_provided(home, tmp_path):
    """If skill_index is passed to assemble_agent, the three skill tools
    are registered on the agent's tool registry."""
    from kc_skills import SkillIndex
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    idx = SkillIndex(skills_root)

    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        skill_index=idx,
    )
    names = set(a.registry.names())
    assert {"skills_list", "skill_view", "skill_run_script"} <= names
    assert a.engine.tier_map["skills_list"] == Tier.SAFE
    assert a.engine.tier_map["skill_view"] == Tier.SAFE
    assert a.engine.tier_map["skill_run_script"] == Tier.DESTRUCTIVE


def test_skill_tools_absent_when_skill_index_none(home):
    """Without skill_index the three tools are not registered."""
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    names = set(a.registry.names())
    assert "skills_list" not in names
    assert "skill_view" not in names
    assert "skill_run_script" not in names


# ------------------------------------------------------------------ Terminal
# (kc-terminal integration — phase A)


def test_terminal_tool_absent_when_disabled(home, monkeypatch):
    """When KC_TERMINAL_ENABLED is unset (or false), terminal_run is not registered."""
    monkeypatch.delenv("KC_TERMINAL_ENABLED", raising=False)
    a = assemble_agent(**_basic_assemble_kwargs(home))
    assert "terminal_run" not in a.registry.names()
    assert "terminal_run" not in a.engine.tier_resolvers


def test_terminal_tool_present_when_enabled(home, monkeypatch, tmp_path):
    """When KC_TERMINAL_ENABLED=true, terminal_run is registered with DESTRUCTIVE fallback tier."""
    monkeypatch.setenv("KC_TERMINAL_ENABLED", "true")
    monkeypatch.setenv("KC_TERMINAL_ROOTS", str(tmp_path))
    a = assemble_agent(**_basic_assemble_kwargs(home))
    assert "terminal_run" in a.registry.names()
    assert a.engine.tier_map["terminal_run"] == Tier.DESTRUCTIVE


def test_terminal_tier_resolver_registered_when_enabled(home, monkeypatch, tmp_path):
    """When KC_TERMINAL_ENABLED=true, the PermissionEngine has a tier_resolver for terminal_run."""
    monkeypatch.setenv("KC_TERMINAL_ENABLED", "true")
    monkeypatch.setenv("KC_TERMINAL_ROOTS", str(tmp_path))
    a = assemble_agent(**_basic_assemble_kwargs(home))
    assert "terminal_run" in a.engine.tier_resolvers


# ------------------------------------------------------------------ Web (kc-web)
# (kc-web integration — phase B)


def test_web_tools_absent_when_web_config_none(home, tmp_path):
    """When web_config kwarg is None (the default), web tools are not registered."""
    a = assemble_agent(**_basic_assemble_kwargs(home))
    assert "web_search" not in a.registry.names()
    assert "web_fetch" not in a.registry.names()


def test_web_tools_present_when_web_config_provided(home, tmp_path):
    """When a WebConfig is supplied (which main.py builds iff KC_WEB_ENABLED=true
    AND firecrawl_api_key is in the secrets store), both tools register at SAFE."""
    from kc_web import WebConfig
    web_config = WebConfig(
        firecrawl_api_key="sk-test-fake",
        session_soft_cap=10,
        daily_hard_cap=100,
        fetch_cap_bytes=1024,
        default_search_max_results=5,
        default_fetch_timeout_s=30,
        budget_db_path=tmp_path / "web_budget.sqlite",
        extra_blocked_hosts=(),
        session_id="test-session",
    )
    a = assemble_agent(**_basic_assemble_kwargs(home), web_config=web_config)
    names = a.registry.names()
    assert "web_search" in names
    assert "web_fetch" in names
    assert a.engine.tier_map["web_search"] == Tier.SAFE
    assert a.engine.tier_map["web_fetch"] == Tier.SAFE


def test_zapier_mcp_tools_are_mutating_not_destructive(home):
    """Zapier MCP tools are user-authorized at mcp.zapier.com (per-app OAuth),
    so KonaClaw treats them as MUTATING (audited, no popup) rather than
    DESTRUCTIVE. Other MCP servers stay DESTRUCTIVE by default."""
    fake_zap_tool = _make_fake_mcp_tool("mcp.zapier.send_slack", "send a slack message")
    fake_other_tool = _make_fake_mcp_tool("mcp.fs.delete", "delete a file")
    mgr = _FakeMCPManager(
        server_names=["zapier", "fs"],
        tools=[fake_zap_tool, fake_other_tool],
    )

    a = assemble_agent(**_basic_assemble_kwargs(home), mcp_manager=mgr)
    assert a.engine.tier_map["mcp.zapier.send_slack"] == Tier.MUTATING
    assert a.engine.tier_map["mcp.fs.delete"] == Tier.DESTRUCTIVE
    # The meta-tool stays SAFE.
    assert a.engine.tier_map["find_or_install_zap"] == Tier.SAFE


# ------------------------------------------------------------------ Phase C
# (todo + clarify integration)


def test_todo_tools_registered_on_kona(home, tmp_path):
    """When todo_storage is supplied AND agent is named 'kona' or 'Kona-AI',
    the six todo.* tools register at Tier.SAFE."""
    from kc_supervisor.todos.storage import TodoStorage
    from kc_supervisor.storage import Storage
    db = Storage(tmp_path / "kc.db"); db.init()
    todo_storage = TodoStorage(db)

    # Need an agent named kona/Kona-AI for registration. The `home` fixture
    # creates "alice.yaml" — write a "kona.yaml" alongside it.
    (home / "agents" / "kona.yaml").write_text(
        "name: kona\nmodel: qwen2.5:7b\nsystem_prompt: I am kona.\n"
    )

    kwargs = _basic_assemble_kwargs(home)
    kwargs["cfg"] = AgentConfig(name="kona", model="qwen2.5:7b", system_prompt="I am kona.")
    a = assemble_agent(**kwargs, todo_storage=todo_storage)
    names = a.registry.names()
    for n in ("todo.add", "todo.list", "todo.complete", "todo.update", "todo.delete", "todo.clear_done"):
        assert n in names
        assert a.engine.tier_map[n] == Tier.SAFE


def test_todo_tools_absent_on_research_agent(home, tmp_path):
    from kc_supervisor.todos.storage import TodoStorage
    from kc_supervisor.storage import Storage
    db = Storage(tmp_path / "kc.db"); db.init()
    todo_storage = TodoStorage(db)
    # The 'alice' fixture is not kona — should not get todo tools.
    a = assemble_agent(**_basic_assemble_kwargs(home), todo_storage=todo_storage)
    names = a.registry.names()
    assert "todo.add" not in names


def test_clarify_tool_registered_on_kona(home, tmp_path):
    from kc_supervisor.clarify.broker import ClarifyBroker
    broker = ClarifyBroker()
    (home / "agents" / "kona.yaml").write_text(
        "name: kona\nmodel: qwen2.5:7b\nsystem_prompt: I am kona.\n"
    )
    kwargs = _basic_assemble_kwargs(home)
    kwargs["cfg"] = AgentConfig(name="kona", model="qwen2.5:7b", system_prompt="I am kona.")
    a = assemble_agent(**kwargs, clarify_broker=broker)
    assert "clarify" in a.registry.names()
    assert a.engine.tier_map["clarify"] == Tier.SAFE


def test_clarify_tool_absent_on_research_agent(home):
    from kc_supervisor.clarify.broker import ClarifyBroker
    broker = ClarifyBroker()
    a = assemble_agent(**_basic_assemble_kwargs(home), clarify_broker=broker)
    assert "clarify" not in a.registry.names()


# ------------------------------------------------------------------ Subagents
# (kc-subagents integration — Task 13)


def test_kona_gets_subagent_tools_when_enabled(home):
    """When subagent_index + subagent_runner are supplied and agent is Kona-AI,
    spawn_subagent and await_subagents are registered."""
    from unittest.mock import MagicMock
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="Kona-AI", model="qwen2.5:7b", system_prompt="I am Kona.")

    fake_index = MagicMock()
    fake_runner = MagicMock()

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        subagent_index=fake_index,
        subagent_runner=fake_runner,
    )
    names = a.registry.names()
    assert "spawn_subagent" in names
    assert "await_subagents" in names


def test_non_kona_does_not_get_subagent_tools(home):
    """Non-kona agents (e.g. 'Research-Agent') must NOT get spawn/await tools
    even when subagent_index + subagent_runner are supplied."""
    from unittest.mock import MagicMock
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="Research-Agent", model="qwen2.5:7b", system_prompt="I do research.")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        subagent_index=MagicMock(),
        subagent_runner=MagicMock(),
    )
    names = a.registry.names()
    assert "spawn_subagent" not in names
    assert "await_subagents" not in names


def test_ephemeral_instance_does_not_get_subagent_or_delegate_tools(home):
    """Ephemeral subagents (cfg.name contains '/ep_') must NOT get spawn_subagent,
    await_subagents, OR delegate_to_agent, even when all deps are provided."""
    from unittest.mock import MagicMock
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    # Synthetic ephemeral agent name — the pattern used by Task 14's runner.
    cfg = AgentConfig(
        name="Kona-AI/ep_abc/web-researcher",
        model="qwen2.5:7b",
        system_prompt="I am an ephemeral web researcher.",
    )

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        resolve_agent=lambda n: (None, "unknown"),   # provided so delegate WOULD register otherwise
        subagent_index=MagicMock(),
        subagent_runner=MagicMock(),
    )
    names = a.registry.names()
    assert "spawn_subagent"    not in names
    assert "await_subagents"   not in names
    assert "delegate_to_agent" not in names
