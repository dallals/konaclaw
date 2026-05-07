from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.storage import Storage


@dataclass
class GoogleOAuthState:
    """Tracks the in-process state of a Google OAuth installed-app flow.

    Lives on `Deps.google_oauth`. Mutated by the /connectors/google/*
    endpoints (connect kicks off a background thread, status reads, disconnect
    resets). Single-process only — restarting the supervisor resets to "idle"
    even if the on-disk token still exists; the next connect will re-detect.
    """
    state: Literal["idle", "pending", "connected"] = "idle"
    since: float = 0.0
    last_error: Optional[str] = None


@dataclass
class Deps:
    """Dependency bundle injected into the FastAPI app at construction.

    Tests build their own Deps with tmp_path-backed components.
    Production wiring lives in main.py.

    `mcp_manager` and `mcp_install_store` are duck-typed (Any) to avoid an
    import-time circular dep on kc-mcp; assembly.py only touches them when
    they're non-None and lazy-imports the install tool at that point.
    """
    storage: Storage
    registry: AgentRegistry
    conversations: ConversationManager
    approvals: ApprovalBroker
    home: Path
    shares: SharesRegistry
    conv_locks: ConversationLocks
    started_at: float = field(default_factory=time.time)
    mcp_manager: Optional[Any] = None
    mcp_install_store: Optional[Any] = None
    inbound_router: Optional[Any] = None
    connector_registry: Optional[Any] = None
    secrets_store: Optional[Any] = None
    google_oauth: GoogleOAuthState = field(default_factory=GoogleOAuthState)
    google_token_path: Optional[Path] = None
    google_credentials_path: Optional[Path] = None


async def _maybe_register_zapier(deps: "Deps") -> None:
    """Register the Zapier MCP server on `deps.mcp_manager` when configured.

    Silent skip when:
      - kc_zapier isn't importable (soft dep), or
      - secrets.yaml lacks `zapier_api_key`.

    On registration failure (e.g. bad API key), logs a warning and returns.
    On success, calls `deps.registry.load_all()` so agents pick up the new
    `mcp.zapier.*` tools and the `find_or_install_zap` meta-tool on the
    next turn.
    """
    if deps.mcp_manager is None:
        return
    try:
        from kc_zapier.config import load_config
        from kc_zapier.register import register_zapier_mcp
    except ImportError:
        return
    try:
        cfg = load_config()
    except KeyError:
        # zapier_api_key not in secrets.yaml — silent skip.
        return
    try:
        await register_zapier_mcp(deps.mcp_manager, cfg)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "zapier MCP registration failed: %s", e,
        )
        return
    # Re-assemble agents now that Zapier tools exist.
    deps.registry.load_all()


DASHBOARD_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8766",
    "http://localhost:8766",
)


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kc-supervisor")
    app.state.deps = deps

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DASHBOARD_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from kc_supervisor.http_routes import register_http_routes
    register_http_routes(app)

    from kc_supervisor.ws_routes import register_ws_routes
    register_ws_routes(app)

    from kc_supervisor import connectors_routes
    connectors_routes.install(app, deps)

    # MCP servers are spawned on the FastAPI startup event so their lifecycle
    # tasks live inside uvicorn's event loop (anyio-scope correctness — see
    # kc_mcp.handle module docstring). After loading, reload AgentRegistry so
    # every agent picks up the freshly-registered MCP tools.
    if deps.mcp_manager is not None and deps.mcp_install_store is not None:
        @app.on_event("startup")
        async def _startup_load_mcps() -> None:
            try:
                from kc_mcp.config_loader import load_static_mcp_servers_async
            except ImportError:
                return
            cfg = deps.home / "config" / "mcp.yaml"
            await load_static_mcp_servers_async(
                config_path=cfg,
                manager=deps.mcp_manager,
                store=deps.mcp_install_store,
            )
            # Re-assemble agents now that MCP tools exist; their AuditingToolRegistry
            # snapshots the tool list at assembly time, so a second load_all() is
            # required after the manager finishes registering.
            deps.registry.load_all()

    # Zapier MCP server — registered at startup if zapier_api_key is in
    # secrets.yaml AND kc-zapier is importable. Lives on the same MCPManager
    # as the file-system MCP servers. After registration, reload the agent
    # registry so every agent picks up the new mcp.zapier.* tools and the
    # find_or_install_zap meta-tool on its next turn.
    if deps.mcp_manager is not None:
        @app.on_event("startup")
        async def _startup_register_zapier() -> None:
            await _maybe_register_zapier(deps)

    # Channel connectors (Telegram, iMessage). Started/stopped on FastAPI
    # lifecycle so their long-running poll/listen tasks live inside uvicorn's
    # event loop. Failures are logged, never fatal.
    if deps.connector_registry is not None and deps.inbound_router is not None:
        @app.on_event("startup")
        async def _startup_start_connectors() -> None:
            for c in deps.connector_registry.all():
                try:
                    await c.start(deps.inbound_router)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "connector %s failed to start", c.name)

        @app.on_event("shutdown")
        async def _shutdown_stop_connectors() -> None:
            for c in deps.connector_registry.all():
                try:
                    await c.stop()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "connector %s failed to stop", c.name)

    return app
