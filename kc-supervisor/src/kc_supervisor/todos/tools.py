from __future__ import annotations
import json
from typing import Any, Callable, Optional

from kc_core.tools import Tool

from kc_supervisor.todos.storage import TodoStorage


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


_LIST_DESC = (
    "List todos visible from this conversation. Returns conversation-scoped "
    "items plus your persistent (agent-scoped) items by default. Each item "
    "includes a `scope` field. Use `status` to filter by open/done/all."
)

_ADD_DESC = (
    "Add a todo. Conversation-scoped by default. Pass persist=true to lift "
    "the item to agent-scope so it survives across conversations. Items have "
    "a title (required), notes (optional), and start in `open` status."
)

_COMPLETE_DESC = (
    "Mark a todo done. Idempotent — completing a done item is a no-op success. "
    "To rename, use todo.update; to delete entirely, use todo.delete."
)

_UPDATE_DESC = (
    "Edit a todo's title and/or notes. Cannot change status (use todo.complete). "
    "At least one of title or notes must be provided."
)

_DELETE_DESC = (
    "Hard-delete a todo. No soft-delete. Use clear_done to bulk-remove "
    "completed items instead."
)

_CLEAR_DONE_DESC = (
    "Delete all completed todos in the requested scope. scope='all' (default) "
    "covers this conversation's items + your persistent items. Useful when "
    "wrapping up — 'clear out everything we finished'."
)


def build_todo_tools(
    storage: TodoStorage,
    current_context: Callable[[], dict],
    broadcast: Callable[[dict], None],
) -> list[Tool]:
    """Build the six todo tools.

    `current_context` returns {conversation_id, agent, channel, chat_id} — set
    by ws_routes/inbound before invoking the agent (reused from scheduling).

    `broadcast` is called after each mutation (add/complete/update/delete/
    clear_done) with a {"action": ..., "item": ..., "conversation_id": ...,
    "agent": ...} dict. The dashboard WS broadcaster wires this to a
    `todo_event` frame.
    """

    def _emit(action: str, *, item: Optional[dict] = None, deleted_count: Optional[int] = None) -> None:
        ctx = current_context()
        event = {
            "action":          action,
            "conversation_id": ctx["conversation_id"],
            "agent":           ctx["agent"],
        }
        if item is not None:
            event["item"] = item
        if deleted_count is not None:
            event["deleted_count"] = deleted_count
        try:
            broadcast(event)
        except Exception:
            pass  # never let WS issues break the tool call

    def _add(title: str = "", notes: str = "", persist: bool = False) -> str:
        ctx = current_context()
        if not isinstance(title, str) or not title.strip():
            return _json({"error": "missing_title"})
        try:
            item = storage.add(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"],
                title=title, notes=notes, persist=bool(persist),
            )
        except ValueError as e:
            return _json({"error": str(e)})
        _emit("added", item=item)
        return _json(item)

    def _list(status: str = "open", scope: str = "all") -> str:
        ctx = current_context()
        try:
            items = storage.list(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"],
                status=status, scope=scope,
            )
        except ValueError as e:
            msg = str(e)
            if msg.startswith("invalid_status"):
                return _json({"error": "invalid_status", "value": status})
            if msg.startswith("invalid_scope"):
                return _json({"error": "invalid_scope", "value": scope})
            return _json({"error": msg})
        return _json({"items": items, "count": len(items)})

    def _complete(id: Optional[int] = None) -> str:
        ctx = current_context()
        if id is None:
            return _json({"error": "missing_id"})
        try:
            item = storage.complete(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"], todo_id=int(id),
            )
        except LookupError:
            return _json({"error": "not_found", "id": int(id)})
        except PermissionError as e:
            msg = str(e).split(":")[0]
            return _json({"error": msg, "id": int(id)})
        _emit("updated", item=item)
        return _json({"id": item["id"], "status": item["status"], "completed_at": item["updated_at"]})

    def _update(id: Optional[int] = None, title: Optional[str] = None, notes: Optional[str] = None) -> str:
        ctx = current_context()
        if id is None:
            return _json({"error": "missing_id"})
        if title is None and notes is None:
            return _json({"error": "missing_fields"})
        try:
            item = storage.update(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"], todo_id=int(id),
                title=title, notes=notes,
            )
        except LookupError:
            return _json({"error": "not_found", "id": int(id)})
        except PermissionError as e:
            msg = str(e).split(":")[0]
            return _json({"error": msg, "id": int(id)})
        except ValueError as e:
            return _json({"error": str(e)})
        _emit("updated", item=item)
        return _json(item)

    def _delete(id: Optional[int] = None) -> str:
        ctx = current_context()
        if id is None:
            return _json({"error": "missing_id"})
        try:
            r = storage.delete(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"], todo_id=int(id),
            )
        except LookupError:
            return _json({"error": "not_found", "id": int(id)})
        except PermissionError as e:
            msg = str(e).split(":")[0]
            return _json({"error": msg, "id": int(id)})
        _emit("deleted", item={"id": int(id)})
        return _json(r)

    def _clear_done(scope: str = "all") -> str:
        ctx = current_context()
        try:
            n = storage.clear_done(
                agent=ctx["agent"], conversation_id=ctx["conversation_id"], scope=scope,
            )
        except ValueError as e:
            msg = str(e)
            if msg.startswith("invalid_scope"):
                return _json({"error": "invalid_scope", "value": scope})
            return _json({"error": msg})
        _emit("cleared_done", deleted_count=n)
        return _json({"deleted_count": n})

    def make(name: str, desc: str, params: dict, impl) -> Tool:
        return Tool(name=name, description=desc, parameters=params, impl=impl)

    return [
        make(
            "todo.add", _ADD_DESC,
            {"type": "object",
             "properties": {
                 "title":   {"type": "string", "description": "REQUIRED. The task."},
                 "notes":   {"type": "string", "description": "Optional context."},
                 "persist": {"type": "boolean",
                             "description": "If true, item is agent-scoped (persistent). Default false."}},
             "required": ["title"]},
            _add,
        ),
        make(
            "todo.list", _LIST_DESC,
            {"type": "object",
             "properties": {
                 "status": {"type": "string", "enum": ["open", "done", "all"]},
                 "scope":  {"type": "string", "enum": ["all", "conversation", "agent"]}}},
            _list,
        ),
        make(
            "todo.complete", _COMPLETE_DESC,
            {"type": "object",
             "properties": {"id": {"type": "integer"}},
             "required": ["id"]},
            _complete,
        ),
        make(
            "todo.update", _UPDATE_DESC,
            {"type": "object",
             "properties": {
                 "id":    {"type": "integer"},
                 "title": {"type": "string"},
                 "notes": {"type": "string"}},
             "required": ["id"]},
            _update,
        ),
        make(
            "todo.delete", _DELETE_DESC,
            {"type": "object",
             "properties": {"id": {"type": "integer"}},
             "required": ["id"]},
            _delete,
        ),
        make(
            "todo.clear_done", _CLEAR_DONE_DESC,
            {"type": "object",
             "properties": {"scope": {"type": "string", "enum": ["all", "conversation", "agent"]}}},
            _clear_done,
        ),
    ]
