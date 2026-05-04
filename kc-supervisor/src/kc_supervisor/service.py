from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from fastapi import FastAPI
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker


@dataclass
class Deps:
    """Dependency bundle injected into the FastAPI app at construction.

    Tests build their own Deps with in-memory or tmp_path-backed components.
    Production wiring lives in main.py.
    """
    storage: Storage
    registry: AgentRegistry
    conversations: ConversationManager
    approvals: ApprovalBroker
    home: Path
    started_at: float = field(default_factory=time.time)


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kc-supervisor")
    app.state.deps = deps

    from kc_supervisor.http_routes import register_http_routes
    register_http_routes(app)

    return app
