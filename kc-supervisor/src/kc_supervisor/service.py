from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.storage import Storage


@dataclass
class Deps:
    """Dependency bundle injected into the FastAPI app at construction.

    Tests build their own Deps with tmp_path-backed components.
    Production wiring lives in main.py.
    """
    storage: Storage
    registry: AgentRegistry
    conversations: ConversationManager
    approvals: ApprovalBroker
    home: Path
    shares: SharesRegistry
    conv_locks: ConversationLocks
    started_at: float = field(default_factory=time.time)


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

    return app
