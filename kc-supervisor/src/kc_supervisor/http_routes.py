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
    pinned: Optional[bool] = None
    title: Optional[str] = None  # explicit "" clears the title


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

    @app.delete("/agents/{name}", status_code=204)
    def delete_agent(name: str):
        from fastapi import Response
        deps = app.state.deps
        target = deps.home / "agents" / f"{name}.yaml"
        if not target.exists():
            raise HTTPException(404, detail=f"unknown agent: {name}")
        target.unlink()
        deps.registry.load_all()
        return Response(status_code=204)

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
        if deps.storage.get_conversation(cid) is None:
            raise HTTPException(404, detail=f"unknown conversation: {cid}")
        fields = req.model_fields_set
        if not fields:
            raise HTTPException(422, detail="must set at least one of: pinned, title")
        if "pinned" in fields and req.pinned is not None:
            deps.storage.set_conversation_pinned(cid, req.pinned)
        if "title" in fields:
            t = req.title.strip() if req.title is not None else None
            deps.storage.set_conversation_title(cid, t or None)
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

        # 4. Run the Undoer. Memory writes record share="memory" in their
        # UndoEntry; merge the memory journal alongside the file-share journals
        # so /undo works for both file ops and memory.{append,replace}.
        journals = dict(rt.assembled.journals)
        if rt.assembled.memory_journal is not None:
            journals.setdefault("memory", rt.assembled.memory_journal)
        undoer = Undoer(journals=journals, log=rt.assembled.undo_log)
        try:
            undoer.undo(eid)
        except ValueError as e:
            # "already applied" — the journal op was reverted previously (possibly
            # before audit_undo_link.undone_at existed). Backfill the stamp so
            # the dashboard stops offering an Undo button on this row, and
            # report 200 with a note instead of 500. The user's intent ("undo
            # this") is already satisfied; surfacing an error would be confusing.
            if "already applied" in str(e):
                deps.storage.mark_audit_undone(audit_id)
                return {"reversed": {"kind": "noop", "details": {"reason": "already undone"}}}
            return JSONResponse(
                status_code=500,
                content={
                    "detail": f"undo failed: {type(e).__name__}: {e}",
                    "audit_id": audit_id,
                },
            )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": f"undo failed: {type(e).__name__}: {e}",
                    "audit_id": audit_id,
                },
            )

        # 5. Stamp the link as undone so subsequent list_audit calls can hide
        # the Undo button. (Idempotent — safe even if the link row vanished.)
        deps.storage.mark_audit_undone(audit_id)

        # 6. Synthesize the reversed action description from the UndoEntry.
        entry = rt.assembled.undo_log.get(eid)
        return {
            "reversed": {
                "kind": entry.reverse_kind,
                "details": entry.reverse_payload,
            },
        }
