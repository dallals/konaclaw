from pathlib import Path
import pytest
import yaml
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry, AgentRuntime, AgentStatus
from kc_supervisor.assembly import AssembledAgent
from kc_supervisor.storage import Storage
from kc_supervisor.approvals import ApprovalBroker


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: I am alice\n"
    )
    (home / "agents" / "bob.yaml").write_text(
        "name: bob\nmodel: fake-model\nsystem_prompt: I am bob\n"
    )
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))
    return home


def _build_registry(home: Path) -> AgentRegistry:
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    return AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
    )


def test_load_from_dir(home):
    reg = _build_registry(home)
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]
    rt = reg.get("alice")
    assert rt.status == AgentStatus.IDLE
    assert isinstance(rt.assembled, AssembledAgent)


def test_get_unknown_raises(home):
    reg = _build_registry(home)
    reg.load_all()
    with pytest.raises(KeyError):
        reg.get("ghost")


def test_status_transitions(home):
    reg = _build_registry(home)
    reg.load_all()
    rt = reg.get("alice")
    rt.set_status(AgentStatus.THINKING)
    snap = reg.snapshot()
    alice_entry = next(e for e in snap if e["name"] == "alice")
    assert alice_entry["status"] == "thinking"


def test_disable_and_enable(home):
    reg = _build_registry(home)
    reg.load_all()
    reg.disable("alice")
    assert reg.get("alice").status == AgentStatus.DISABLED
    reg.enable("alice")
    assert reg.get("alice").status == AgentStatus.IDLE


def test_load_all_idempotent(home):
    reg = _build_registry(home)
    reg.load_all()
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]


def test_snapshot_shape(home):
    reg = _build_registry(home)
    reg.load_all()
    snap = reg.snapshot()
    assert len(snap) == 2
    for entry in snap:
        assert set(entry.keys()) == {"name", "model", "status", "last_error"}


def test_load_all_degrades_on_assembly_failure(home):
    """If shares.yaml points at a missing path, SharesRegistry.from_yaml raises BEFORE
    we even build the registry. This test verifies the registry handles a different
    failure mode: a YAML that fails to parse as an AgentConfig."""
    # Inject a broken agent yaml and verify the registry survives + marks it DEGRADED
    (home / "agents" / "broken.yaml").write_text(":::not yaml:::")
    reg = _build_registry(home)
    reg.load_all()
    names = reg.names()
    assert "alice" in names
    assert "bob" in names
    assert "broken" in names
    rt = reg.get("broken")
    assert rt.status == AgentStatus.DEGRADED
    assert rt.last_error is not None
    assert rt.assembled is None


def test_load_all_per_yaml_failure_does_not_break_other_loads(home):
    """An agent yaml missing the required name field should produce a DEGRADED entry
    while alice/bob continue to load successfully."""
    (home / "agents" / "noname.yaml").write_text(
        "model: fake-model\nsystem_prompt: missing name\n"
    )
    reg = _build_registry(home)
    reg.load_all()
    names = reg.names()
    assert "alice" in names
    assert "bob" in names
    # The malformed yaml has no `name`, so load_agent_config raises ValueError;
    # the registry uses the file stem as a fallback name.
    assert "noname" in names
    assert reg.get("noname").status == AgentStatus.DEGRADED
