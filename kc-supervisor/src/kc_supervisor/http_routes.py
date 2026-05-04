from __future__ import annotations
import time
from fastapi import FastAPI


def register_http_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        deps = app.state.deps
        return {
            "status": "ok",
            "uptime_s": round(time.time() - deps.started_at, 2),
            "agents": len(deps.registry.names()),
        }
