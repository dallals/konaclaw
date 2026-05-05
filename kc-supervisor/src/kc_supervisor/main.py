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

    # MCP integration is optional — if kc-mcp is installed, instantiate the
    # bookkeeping objects here so AgentRegistry sees them. The actual MCP
    # subprocess spawning happens on the FastAPI startup hook in service.py
    # (anyio-scope correctness — see kc_mcp.handle docstring).
    mcp_manager = None
    mcp_install_store = None
    try:
        from kc_mcp.manager import MCPManager
        from kc_mcp.store import MCPInstallStore
        mcp_manager = MCPManager()
        mcp_install_store = MCPInstallStore(storage)
    except ImportError:
        pass

    # Memory layer — if kc-memory is installed, point assembly at
    # ~/KonaClaw/memory/. Each agent gets memory.read/append/replace tools
    # plus its memory prefix injected into the system prompt.
    memory_root = None
    try:
        import kc_memory  # noqa: F401  — presence check
        memory_root = home / "memory"
        memory_root.mkdir(parents=True, exist_ok=True)
    except ImportError:
        pass

    # Google connectors (Gmail + Calendar) — optional. Reads
    # ~/KonaClaw/config/secrets.yaml for the OAuth credentials path. If the
    # creds file is missing or kc-connectors isn't installed, agents simply
    # don't get Google tools; the supervisor still boots.
    gmail_service = None
    gcal_service = None
    try:
        from kc_connectors.secrets import load_secrets
        from kc_connectors.gmail_adapter import build_gmail_service, GMAIL_SCOPES
        from kc_connectors.gcal_adapter import build_gcal_service, GCAL_SCOPES
        secrets = load_secrets()
        creds_path = secrets.get("google_credentials_json_path")
        token_path = secrets.get("google_token_json_path",
                                str(home / "config" / "google_token.json"))
        if creds_path and Path(creds_path).exists():
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            scopes = GMAIL_SCOPES + GCAL_SCOPES
            creds = None
            if Path(token_path).exists():
                creds = Credentials.from_authorized_user_file(token_path, scopes)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                    creds = flow.run_local_server(port=0)
                Path(token_path).write_text(creds.to_json())
            gmail_service = build_gmail_service(creds)
            gcal_service = build_gcal_service(creds)
    except ImportError:
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("google connectors disabled: %s", e)

    # Channel connectors (Telegram, iMessage). Built only when secrets.yaml
    # supplies the relevant config and kc-connectors is importable. Failures
    # are non-fatal — supervisor still boots without channel connectors.
    connector_registry = None
    routing_table = None
    try:
        from kc_connectors.base import ConnectorRegistry as _ConnReg
        from kc_connectors.routing import RoutingTable as _RT
        from kc_connectors.secrets import load_secrets as _ls
        secrets = _ls()
        connector_registry = _ConnReg()
        routing_path = home / "config" / "routing.yaml"
        if routing_path.exists():
            routing_table = _RT.load_from_yaml(routing_path)
        else:
            routing_table = _RT(default_agent=os.environ.get("KC_DEFAULT_AGENT", "kona"))

        tg_token = secrets.get("telegram_bot_token")
        tg_allow = secrets.get("telegram_allowlist") or []
        if tg_token and tg_allow:
            from kc_connectors.telegram_adapter import TelegramConnector
            connector_registry.register(TelegramConnector(
                token=tg_token, allowlist=set(str(x) for x in tg_allow),
            ))

        # iMessage — only attempt on macOS where chat.db lives at the standard path.
        import platform as _plat
        if _plat.system() == "Darwin":
            im_allow = secrets.get("imessage_allowlist") or []
            chat_db = Path.home() / "Library" / "Messages" / "chat.db"
            if im_allow and chat_db.exists():
                from kc_connectors.imessage_adapter import IMessageConnector
                connector_registry.register(IMessageConnector(
                    chat_db_path=chat_db,
                    allowlist=set(str(x) for x in im_allow),
                ))
    except ImportError:
        connector_registry = None
        routing_table = None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("connectors disabled: %s", e)
        connector_registry = None
        routing_table = None

    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url=ollama_url,
        default_model=default_model,
        undo_db_path=home / "data" / "undo.db",
        mcp_manager=mcp_manager,
        mcp_install_store=mcp_install_store,
        memory_root=memory_root,
        gmail_service=gmail_service,
        gcal_service=gcal_service,
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
        mcp_manager=mcp_manager,
        mcp_install_store=mcp_install_store,
    )
    # InboundRouter is created after Deps so it has access to the same
    # registry/conversations/conv_locks. Stored on Deps so service.py can
    # start connectors at FastAPI startup.
    if connector_registry is not None and routing_table is not None and connector_registry.all():
        from kc_supervisor.inbound import InboundRouter
        deps.inbound_router = InboundRouter(
            registry=registry,
            conversations=deps.conversations,
            conv_locks=conv_locks,
            routing_table=routing_table,
            connector_registry=connector_registry,
        )
        deps.connector_registry = connector_registry

    app = create_app(deps)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_PORT", "8765")))


if __name__ == "__main__":
    main()
