from __future__ import annotations
import os
from pathlib import Path
import uvicorn
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
from kc_supervisor.secrets_store import SecretsStore, SecurityCliKeychain
from kc_supervisor.service import Deps, create_app
from kc_supervisor.storage import Storage


def main() -> None:
    home = Path(os.environ.get("KC_HOME", str(Path.home() / "KonaClaw")))
    default_model = os.environ.get("KC_DEFAULT_MODEL", "qwen2.5:7b")
    ollama_url = os.environ.get("KC_OLLAMA_URL", "http://localhost:11434")
    ollama_api_key = os.environ.get("KC_OLLAMA_API_KEY") or None

    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    if not (home / "config" / "shares.yaml").exists():
        (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "konaclaw.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    conv_locks = ConversationLocks()

    # Secrets store — manages encrypted secrets.yaml.enc with keychain-backed AES-GCM.
    # On first run, migrates plaintext secrets.yaml to encrypted store.
    secrets_store = SecretsStore(config_dir=home / "config", keychain=SecurityCliKeychain())
    secrets = secrets_store.load()

    # News tool-provider — optional. Built only when secrets supplies newsapi_api_key.
    # Lazy-imports kc_connectors so kc-supervisor doesn't take a hard dep when News
    # isn't configured.
    news_client = None
    newsapi_key = secrets.get("newsapi_api_key")
    if newsapi_key:
        try:
            from kc_connectors.news_adapter import NewsClient
            news_client = NewsClient(api_key=newsapi_key)
        except ImportError:
            pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("news client disabled: %s", e)

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

    # Skills layer — if kc-skills is installed, point assembly at
    # ~/KonaClaw/skills/. Each agent gets skills_list/skill_view/skill_run_script.
    skill_index = None
    try:
        from kc_skills import SkillIndex
        skills_root = home / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        skill_index = SkillIndex(skills_root)
    except ImportError:
        pass

    # Google OAuth paths — read here so they reach Deps even when kc-connectors
    # isn't importable (the dashboard's Connect-with-Google flow only needs the
    # paths + google-auth-oauthlib, not kc_connectors).
    google_creds_path_str = secrets.get("google_credentials_json_path")
    google_token_path_str = secrets.get("google_token_json_path",
                                        str(home / "config" / "google_token.json"))

    # Google connectors (Gmail + Calendar) — optional. Uses secrets loaded above
    # for the OAuth credentials path. If the creds file is missing or
    # kc-connectors isn't installed, agents simply don't get Google tools;
    # the supervisor still boots.
    gmail_service = None
    gcal_service = None
    try:
        from kc_connectors.gmail_adapter import build_gmail_service, GMAIL_SCOPES
        from kc_connectors.gcal_adapter import build_gcal_service, GCAL_SCOPES
        creds_path = google_creds_path_str
        token_path = google_token_path_str
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

    # Channel connectors (Telegram, iMessage). Built only when secrets
    # supplies the relevant config and kc-connectors is importable. Failures
    # are non-fatal — supervisor still boots without channel connectors.
    #
    # We use a builder + mutable holder pattern so the hot-restart hooks
    # (wired below, after Deps construction) can rebuild a connector from
    # fresh secrets, swap the live instance, and re-register on the same
    # ConnectorRegistry without a supervisor reboot. See Task 7 of v0.2.1.
    connector_registry = None
    routing_table = None
    _telegram_holder: list = [None]
    _imessage_holder: list = [None]
    _build_telegram = None
    _build_imessage = None
    try:
        from kc_connectors.base import ConnectorRegistry as _ConnReg
        from kc_connectors.routing import RoutingTable as _RT
        connector_registry = _ConnReg()
        routing_path = home / "config" / "routing.yaml"
        if routing_path.exists():
            routing_table = _RT.load_from_yaml(routing_path)
        else:
            routing_table = _RT(default_agent=os.environ.get("KC_DEFAULT_AGENT", "kona"))

        # Telegram — builder + holder + hot-restart pair.
        try:
            from kc_connectors.telegram_adapter import TelegramConnector
        except ImportError:
            TelegramConnector = None  # type: ignore

        def _build_telegram(secrets_dict: dict):
            if TelegramConnector is None:
                return None
            tok = secrets_dict.get("telegram_bot_token")
            allow = secrets_dict.get("telegram_allowlist") or []
            if not tok or not allow:
                return None
            return TelegramConnector(token=tok, allowlist=set(str(x) for x in allow))

        _telegram_holder[0] = _build_telegram(secrets)
        if _telegram_holder[0] is not None:
            connector_registry.register(_telegram_holder[0])

        # iMessage — same pattern, only on Darwin where chat.db exists.
        import platform as _plat
        IMessageConnector = None
        chat_db = Path.home() / "Library" / "Messages" / "chat.db"
        if _plat.system() == "Darwin":
            try:
                from kc_connectors.imessage_adapter import IMessageConnector
            except ImportError:
                IMessageConnector = None  # type: ignore

        def _build_imessage(secrets_dict: dict):
            if IMessageConnector is None or not chat_db.exists():
                return None
            allow = secrets_dict.get("imessage_allowlist") or []
            if not allow:
                return None
            return IMessageConnector(
                chat_db_path=chat_db,
                allowlist=set(str(x) for x in allow),
            )

        _imessage_holder[0] = _build_imessage(secrets)
        if _imessage_holder[0] is not None:
            connector_registry.register(_imessage_holder[0])
    except ImportError:
        connector_registry = None
        routing_table = None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("connectors disabled: %s", e)
        # Do NOT reset connector_registry here — it was successfully constructed
        # as an empty ConnectorRegistry() before the exception. Always having a
        # non-None registry is required for Phase 2 cross-channel reminders:
        # ReminderRunner.fire() calls connector_registry.get(channel) at fire
        # time, so missing connectors fail the individual row (logged + marked
        # failed) rather than crashing the supervisor at boot.
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
        news_client=news_client,
        ollama_api_key=ollama_api_key,
        skill_index=skill_index,
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
        secrets_store=secrets_store,
        google_credentials_path=Path(google_creds_path_str) if google_creds_path_str else None,
        google_token_path=Path(google_token_path_str),
        news_client=news_client,
        skill_index=skill_index,
    )
    # Always wire the registry to deps so ReminderRunner.fire() can call
    # connector_registry.get(channel) at fire time. The InboundRouter still
    # requires routing_table + at least one connector — only it stays gated.
    if connector_registry is not None:
        deps.connector_registry = connector_registry
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

    # Hot-restart hooks for PATCH /connectors/{name} (Task 7 of v0.2.1).
    # The hook is sync (called from a sync FastAPI handler in a threadpool),
    # so we dispatch the async stop/start via run_coroutine_threadsafe back
    # to the main event loop captured at FastAPI startup (deps.event_loop).
    #
    # Wired even when no connector was registered at boot: if the user PATCHes
    # /connectors/telegram with a fresh token, the hook needs to build and
    # register the connector for the first time.
    if connector_registry is not None and _build_telegram is not None and _build_imessage is not None:
        import asyncio as _asyncio
        import concurrent.futures as _futures
        import logging as _logging

        async def _stop_then_start(old_conn, new_conn, supervisor):
            """Stop the previous connector, then start the new one. Sequential
            on the event loop so we don't hit a transient 409 against the same
            long-poll endpoint. Errors during stop are logged but don't prevent
            start.
            """
            if old_conn is not None:
                try:
                    await old_conn.stop()
                except Exception as exc:
                    _logging.getLogger(__name__).warning(
                        "stop() failed during restart: %s", exc, exc_info=True,
                    )
            if new_conn is not None and supervisor is not None:
                await new_conn.start(supervisor)

        def _make_restart(name: str, holder: list, builder):
            def _restart() -> None:
                loop = deps.event_loop
                fresh = deps.secrets_store.load() if deps.secrets_store else {}
                old = holder[0]
                if old is not None:
                    connector_registry.unregister(name)
                new = builder(fresh)
                holder[0] = new
                if new is not None:
                    connector_registry.register(new)
                if loop is not None and loop.is_running():
                    try:
                        fut = _asyncio.run_coroutine_threadsafe(
                            _stop_then_start(old, new, deps.inbound_router), loop,
                        )
                        try:
                            fut.result(timeout=2.0)
                        except _futures.TimeoutError:
                            _logging.getLogger(__name__).warning(
                                "%s restart did not complete within 2s; continuing anyway",
                                name, exc_info=True,
                            )
                        except Exception as exc:
                            _logging.getLogger(__name__).warning(
                                "%s restart failed: %s", name, exc, exc_info=True,
                            )
                    except Exception as exc:
                        _logging.getLogger(__name__).warning(
                            "%s restart dispatch failed: %s", name, exc, exc_info=True,
                        )
            return _restart

        deps.restart_telegram = _make_restart("telegram", _telegram_holder, _build_telegram)
        deps.restart_imessage = _make_restart("imessage", _imessage_holder, _build_imessage)

    # Phase-1 scheduling. Constructed here but started inside FastAPI's startup
    # hook (see service.py) so it picks up the running event loop. The
    # ReminderRunner bridges from APS's worker thread back to the FastAPI event
    # loop captured at startup.
    import asyncio as _asyncio_sched
    import tzlocal as _tzlocal
    from kc_supervisor.scheduling.service import ScheduleService
    from kc_supervisor.scheduling.runner import ReminderRunner, set_active_runner

    _tz_name = str(_tzlocal.get_localzone())

    def _coroutine_runner(coro):
        if deps.event_loop is None:
            raise RuntimeError("ScheduleService fired before FastAPI startup")
        fut = _asyncio_sched.run_coroutine_threadsafe(coro, deps.event_loop)
        return fut.result(timeout=30)

    deps.reminders_broadcaster = RemindersBroadcaster()
    _reminder_runner = ReminderRunner(
        storage=deps.storage,
        conversations=deps.conversations,
        connector_registry=deps.connector_registry,
        coroutine_runner=_coroutine_runner,
        agent_registry=registry,
        broadcaster=deps.reminders_broadcaster,
    )
    # Register as the module-level active runner so APS's pickled module-level
    # `fire_reminder` can dispatch to this instance. (See runner.py for why we
    # avoid bound methods in APS jobstores.)
    set_active_runner(_reminder_runner)
    deps.schedule_service = ScheduleService(
        storage=deps.storage,
        runner=_reminder_runner,
        db_path=home / "data" / "konaclaw.db",
        timezone=_tz_name,
        broadcaster=deps.reminders_broadcaster,
    )
    # Now that schedule_service exists, wire it into the AgentRegistry and
    # reload so Kona's AssembledAgent picks up the four scheduling tools.
    registry.schedule_service = deps.schedule_service
    registry.load_all()

    app = create_app(deps)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_PORT", "8765")))


if __name__ == "__main__":
    main()
