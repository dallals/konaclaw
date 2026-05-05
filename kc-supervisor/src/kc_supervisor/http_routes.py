from __future__ import annotations
import re
import time
from dataclasses import asdict
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

_AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class CreateAgentRequest(BaseModel):
    name: str
    system_prompt: str
    model: Optional[str] = None


class CreateConversationRequest(BaseModel):
    channel: str = "dashboard"


class UpdateConversationRequest(BaseModel):
    pinned: bool


def _message_to_dict(m) -> dict:
    """Serialize a kc_core.messages dataclass for JSON."""
    return {"type": m.__class__.__name__, **asdict(m)}


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

    @app.post("/agents")
    def create_agent(req: CreateAgentRequest):
        deps = app.state.deps
        if not _AGENT_NAME_PATTERN.match(req.name):
            raise HTTPException(
                422,
                detail=f"name must match {_AGENT_NAME_PATTERN.pattern}",
            )
        agent_dir = deps.home / "agents"
        target = agent_dir / f"{req.name}.yaml"
        if target.exists():
            raise HTTPException(409, detail=f"agent {req.name!r} already exists")

        # Build YAML content (model is optional; load_agent_config falls back to default).
        lines = [f"name: {req.name}", "system_prompt: |"]
        for pl in req.system_prompt.splitlines() or [""]:
            lines.append(f"  {pl}")
        if req.model:
            if "\n" in req.model or "\r" in req.model:
                raise HTTPException(422, detail="model must not contain newlines")
            lines.append(f"model: {req.model}")
        yaml_content = "\n".join(lines) + "\n"

        # Atomic write: tempfile + rename
        tmp = target.with_suffix(".yaml.tmp")
        tmp.write_text(yaml_content)
        tmp.rename(target)

        # Reload registry; new agent appears as IDLE or DEGRADED
        deps.registry.load_all()
        rt = deps.registry.get(req.name)
        return rt.to_dict()

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

    @app.patch("/conversations/{cid}")
    def update_conversation(cid: int, req: UpdateConversationRequest):
        deps = app.state.deps
        if not deps.storage.set_conversation_pinned(cid, req.pinned):
            raise HTTPException(404, detail=f"unknown conversation: {cid}")
        return deps.storage.get_conversation(cid)

    @app.delete("/conversations/{cid}", status_code=204)
    def delete_conversation(cid: int):
        from fastapi import Response
        deps = app.state.deps
        if not deps.storage.delete_conversation(cid):
            raise HTTPException(404, detail=f"unknown conversation: {cid}")
        return Response(status_code=204)

    @app.get("/conversations/{cid}/messages")
    def list_messages(cid: int):
        if app.state.deps.storage.get_conversation(cid) is None:
            raise HTTPException(404, detail=f"unknown conversation: {cid}")
        msgs = app.state.deps.conversations.list_messages(cid)
        return {"messages": [_message_to_dict(m) for m in msgs]}

    @app.get("/audit")
    def list_audit(
        agent: Optional[str] = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        rows = app.state.deps.storage.list_audit(agent=agent, limit=limit)
        return {"entries": rows}

    @app.post("/undo/{audit_id}")
    def undo(audit_id: int):
        from fastapi.responses import JSONResponse
        from kc_sandbox.undo import Undoer
        deps = app.state.deps

        # 1. Find the audit row
        rows = deps.storage.list_audit(limit=1000000)
        row = next((r for r in rows if r["id"] == audit_id), None)
        if row is None:
            raise HTTPException(404, detail=f"unknown audit_id: {audit_id}")

        # 2. Find the linked eid
        eid = deps.storage.get_undo_op_for_audit(audit_id)
        if eid is None:
            raise HTTPException(
                422,
                detail="this audit row has no journal op (only mutating/destructive file ops journal)",
            )

        # 3. Find the agent's AssembledAgent
        try:
            rt = deps.registry.get(row["agent"])
        except KeyError:
            raise HTTPException(
                404,
                detail=f"agent {row['agent']!r} (from audit row) no longer exists",
            )
        if rt.assembled is None:
            raise HTTPException(409, detail=f"agent {row['agent']!r} is degraded; cannot undo")

        # 4. Run the Undoer
        undoer = Undoer(journals=rt.assembled.journals, log=rt.assembled.undo_log)
        try:
            undoer.undo(eid)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": f"undo failed: {type(e).__name__}: {e}",
                    "audit_id": audit_id,
                },
            )

        # 5. Synthesize the reversed action description from the UndoEntry
        entry = rt.assembled.undo_log.get(eid)
        return {
            "reversed": {
                "kind": entry.reverse_kind,
                "details": entry.reverse_payload,
            },
        }
