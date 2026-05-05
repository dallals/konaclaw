from pathlib import Path
import pytest
import yaml
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.service import Deps, create_app


@pytest.fixture
def deps(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)

    # Two minimal agents
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: hi from alice\n"
    )
    (home / "agents" / "bob.yaml").write_text(
        "name: bob\nmodel: fake-model\nsystem_prompt: hi from bob\n"
    )

    # shares.yaml — one share so assembly succeeds
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))

    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
    )
    registry.load_all()
    convs = ConversationManager(storage=storage)
    return Deps(
        storage=storage,
        registry=registry,
        conversations=convs,
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=ConversationLocks(),
    )


@pytest.fixture
def app(deps):
    return create_app(deps)
