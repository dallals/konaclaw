from __future__ import annotations
import time
from typing import Any, Optional

from kc_supervisor.storage import Storage


_VALID_STATUS = {"open", "done"}
_VALID_LIST_STATUS = {"open", "done", "all"}
_VALID_SCOPE = {"all", "conversation", "agent"}


def _row_to_dict(row: Any) -> dict:
    return {
        "id":              row["id"],
        "agent":           row["agent"],
        "conversation_id": row["conversation_id"],
        "title":           row["title"],
        "notes":           row["notes"],
        "status":          row["status"],
        "scope":           "agent" if row["conversation_id"] is None else "conversation",
        "created_at":      row["created_at"],
        "updated_at":      row["updated_at"],
    }


class TodoStorage:
    """CRUD layer for todos. All write operations raise on validation failure,
    PermissionError on cross-agent/cross-conversation attempts, LookupError on
    missing ids. Returns plain dicts (one row each); the tool layer above
    serializes to JSON."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add(
        self,
        *,
        agent: str,
        conversation_id: int,
        title: str,
        notes: str = "",
        persist: bool = False,
    ) -> dict:
        if not title or not title.strip():
            raise ValueError("title must be non-empty")
        now = time.time()
        conv = None if persist else conversation_id
        with self._storage.connect() as c:
            cur = c.execute(
                "INSERT INTO todos (agent, conversation_id, title, notes, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (agent, conv, title.strip(), notes, now, now),
            )
            todo_id = cur.lastrowid
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return _row_to_dict(row)

    def list(
        self,
        *,
        agent: str,
        conversation_id: int,
        status: str = "open",
        scope: str = "all",
    ) -> list[dict]:
        if status not in _VALID_LIST_STATUS:
            raise ValueError(f"invalid_status: {status}")
        if scope not in _VALID_SCOPE:
            raise ValueError(f"invalid_scope: {scope}")
        sql_parts = ["SELECT * FROM todos WHERE agent = ?"]
        params: list[Any] = [agent]
        if scope == "conversation":
            sql_parts.append("AND conversation_id = ?")
            params.append(conversation_id)
        elif scope == "agent":
            sql_parts.append("AND conversation_id IS NULL")
        else:  # all
            sql_parts.append("AND (conversation_id = ? OR conversation_id IS NULL)")
            params.append(conversation_id)
        if status != "all":
            sql_parts.append("AND status = ?")
            params.append(status)
        sql_parts.append("ORDER BY created_at ASC")
        with self._storage.connect() as c:
            rows = c.execute(" ".join(sql_parts), params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _load_and_authz(
        self,
        c,
        *,
        agent: str,
        conversation_id: int,
        todo_id: int,
    ) -> Any:
        """Fetch + authz check. Raises LookupError if not found,
        PermissionError on cross-agent or cross-conversation."""
        row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        if row is None:
            raise LookupError(f"not_found: {todo_id}")
        if row["agent"] != agent:
            raise PermissionError(f"wrong_agent: {todo_id}")
        if row["conversation_id"] is not None and row["conversation_id"] != conversation_id:
            raise PermissionError(f"wrong_conversation: {todo_id}")
        return row

    def complete(self, *, agent: str, conversation_id: int, todo_id: int) -> dict:
        now = time.time()
        with self._storage.connect() as c:
            self._load_and_authz(c, agent=agent, conversation_id=conversation_id, todo_id=todo_id)
            c.execute("UPDATE todos SET status='done', updated_at=? WHERE id=?", (now, todo_id))
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return _row_to_dict(row)

    def update(
        self,
        *,
        agent: str,
        conversation_id: int,
        todo_id: int,
        title: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        if title is None and notes is None:
            raise ValueError("missing_fields: at least one of title/notes required")
        if title is not None and not title.strip():
            raise ValueError("title must be non-empty")
        now = time.time()
        with self._storage.connect() as c:
            self._load_and_authz(c, agent=agent, conversation_id=conversation_id, todo_id=todo_id)
            sets = []
            params: list[Any] = []
            if title is not None:
                sets.append("title = ?")
                params.append(title.strip())
            if notes is not None:
                sets.append("notes = ?")
                params.append(notes)
            sets.append("updated_at = ?")
            params.append(now)
            params.append(todo_id)
            c.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params)
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return _row_to_dict(row)

    def delete(self, *, agent: str, conversation_id: int, todo_id: int) -> dict:
        with self._storage.connect() as c:
            self._load_and_authz(c, agent=agent, conversation_id=conversation_id, todo_id=todo_id)
            c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        return {"id": todo_id, "deleted": True}

    def clear_done(self, *, agent: str, conversation_id: int, scope: str = "all") -> int:
        if scope not in _VALID_SCOPE:
            raise ValueError(f"invalid_scope: {scope}")
        sql_parts = ["DELETE FROM todos WHERE agent = ? AND status = 'done'"]
        params: list[Any] = [agent]
        if scope == "conversation":
            sql_parts.append("AND conversation_id = ?")
            params.append(conversation_id)
        elif scope == "agent":
            sql_parts.append("AND conversation_id IS NULL")
        else:  # all
            sql_parts.append("AND (conversation_id = ? OR conversation_id IS NULL)")
            params.append(conversation_id)
        with self._storage.connect() as c:
            cur = c.execute(" ".join(sql_parts), params)
            return cur.rowcount
