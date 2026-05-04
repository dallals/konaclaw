from pathlib import Path
import pytest
from kc_supervisor.agents import AgentRegistry, AgentRuntime, AgentStatus


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"; d.mkdir()
    (d / "alice.yaml").write_text("name: alice\nmodel: m\nsystem_prompt: I am alice\n")
    (d / "bob.yaml").write_text("name: bob\nmodel: m\nsystem_prompt: I am bob\n")
    return d


def test_load_from_dir(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]
    assert reg.get("alice").status == AgentStatus.IDLE


def test_get_unknown_raises(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    with pytest.raises(KeyError):
        reg.get("ghost")


def test_status_transitions(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    rt = reg.get("alice")
    rt.set_status(AgentStatus.THINKING)
    assert rt.status == AgentStatus.THINKING
    snap = reg.snapshot()
    alice_entry = next(e for e in snap if e["name"] == "alice")
    assert alice_entry["status"] == "thinking"


def test_disable_and_enable(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    reg.disable("alice")
    assert reg.get("alice").status == AgentStatus.DISABLED
    reg.enable("alice")
    assert reg.get("alice").status == AgentStatus.IDLE


def test_load_all_idempotent(agents_dir, tmp_path):
    """Calling load_all twice should not duplicate entries."""
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]


def test_snapshot_shape(agents_dir, tmp_path):
    """snapshot() returns dicts with name, model, status, last_error keys."""
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    snap = reg.snapshot()
    assert len(snap) == 2
    for entry in snap:
        assert set(entry.keys()) == {"name", "model", "status", "last_error"}
