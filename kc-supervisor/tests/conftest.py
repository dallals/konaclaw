from pathlib import Path
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.service import Deps, create_app


@pytest.fixture
def deps(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)

    # Two minimal agents
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: hi from alice\n"
    )
    (home / "agents" / "bob.yaml").write_text(
        "name: bob\nmodel: fake-model\nsystem_prompt: hi from bob\n"
    )

    # Empty shares.yaml
    (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "kc.db"); storage.init()
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares_yaml=home / "config" / "shares.yaml",
        undo_db=home / "data" / "undo.db",
        default_model="fake-model",
    )
    registry.load_all()
    convs = ConversationManager(storage=storage)
    broker = ApprovalBroker()
    return Deps(
        storage=storage,
        registry=registry,
        conversations=convs,
        approvals=broker,
        home=home,
    )


@pytest.fixture
def app(deps):
    return create_app(deps)
