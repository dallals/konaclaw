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
