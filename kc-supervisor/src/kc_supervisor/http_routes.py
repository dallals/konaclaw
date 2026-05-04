from __future__ import annotations
import time
from dataclasses import asdict, is_dataclass
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CreateConversationRequest(BaseModel):
    channel: str = "dashboard"


def _message_to_dict(m: object) -> dict:
    """Serialize a kc_core.messages dataclass for JSON."""
    if is_dataclass(m):
        return {"type": m.__class__.__name__, **asdict(m)}
    raise TypeError(f"cannot serialize message of type {type(m).__name__}")


def register_http_routes(app: FastAPI) -> None:

    @app.get("/health")
    def health():
        deps = app.state.deps
        return {
            "status": "ok",
            "uptime_s": round(time.time() - deps.started_at, 2),
            "agents": len(deps.registry.names()),
        }

    @app.get("/agents")
    def list_agents():
        return {"agents": app.state.deps.registry.snapshot()}

    @app.get("/conversations")
    def list_conversations(agent: Optional[str] = None):
        cm = app.state.deps.conversations
        if agent is not None:
            return {"conversations": cm.list_for_agent(agent)}
        return {"conversations": cm.list_all()}

    @app.post("/agents/{name}/conversations")
    def create_conversation(name: str, req: CreateConversationRequest):
        try:
            app.state.deps.registry.get(name)
        except KeyError:
            raise HTTPException(404, detail=f"unknown agent: {name}")
        cid = app.state.deps.conversations.start(agent=name, channel=req.channel)
        return {"conversation_id": cid}

    @app.get("/conversations/{cid}/messages")
    def list_messages(cid: int):
        msgs = app.state.deps.conversations.list_messages(cid)
        return {"messages": [_message_to_dict(m) for m in msgs]}

    @app.get("/audit")
    def list_audit(agent: Optional[str] = None, limit: int = 100):
        rows = app.state.deps.storage.list_audit(agent=agent, limit=limit)
        return {"entries": rows}

    @app.post("/undo/{audit_id}")
    def undo(audit_id: int):
        # v1 stub: kc-sandbox Undoer wiring lands in v0.2 once shares are
        # configured at boot. The audit_undo_link table (Storage) already
        # supports the lookup; the missing piece is access to the per-share
        # Journal instances.
        raise HTTPException(
            501, detail="Undo not yet wired in kc-supervisor v1 — see roadmap.",
        )
