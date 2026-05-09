from pathlib import Path
from typing import Optional

import pytest
import yaml
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.secrets_store import SecretsStore
from kc_supervisor.service import Deps, create_app


class FakeKeychain:
    """In-memory keychain for tests; mirrors the one in test_secrets_store.py."""

    def __init__(self, value: Optional[str] = None) -> None:
        self._value = value

    def get(self) -> Optional[str]:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


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
    secrets_store = SecretsStore(config_dir=home / "config", keychain=FakeKeychain())
    google_token_path = home / "data" / "google_token.json"
    return Deps(
        storage=storage,
        registry=registry,
        conversations=convs,
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=ConversationLocks(),
        secrets_store=secrets_store,
        google_token_path=google_token_path,
    )


@pytest.fixture
def app(deps):
    return create_app(deps)
