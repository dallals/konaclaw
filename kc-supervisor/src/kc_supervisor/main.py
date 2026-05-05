from __future__ import annotations
import os
from pathlib import Path
import uvicorn
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.service import Deps, create_app
from kc_supervisor.storage import Storage


def main() -> None:
    home = Path(os.environ.get("KC_HOME", str(Path.home() / "KonaClaw")))
    default_model = os.environ.get("KC_DEFAULT_MODEL", "qwen2.5:7b")
    ollama_url = os.environ.get("KC_OLLAMA_URL", "http://localhost:11434")

    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    if not (home / "config" / "shares.yaml").exists():
        (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "konaclaw.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    conv_locks = ConversationLocks()

    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url=ollama_url,
        default_model=default_model,
        undo_db_path=home / "data" / "undo.db",
    )
    registry.load_all()

    deps = Deps(
        storage=storage,
        registry=registry,
        conversations=ConversationManager(storage),
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=conv_locks,
    )
    app = create_app(deps)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_PORT", "8765")))


if __name__ == "__main__":
    main()
