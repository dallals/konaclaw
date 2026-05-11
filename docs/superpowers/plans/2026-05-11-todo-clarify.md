# Todo + Clarify Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two conversation-internal tool families for Kona — `todo.*` (six ops for a scratch-pad task list) and `clarify` (blocking multiple-choice question). Both as subpackages inside kc-supervisor, parallel to `kc_supervisor.scheduling/`. Surfaces in the dashboard as a right-sidebar TodoWidget and an inline ClarifyCard. Phase C of the post-Skills tools rollout.

**Architecture:** New `todos` SQLite table with nullable `conversation_id` to support hybrid scope (conversation-default + `persist=true` for agent-scope). `ClarifyBroker` mirrors `ApprovalBroker` — async futures, in-memory pending state, WebSocket frames over the existing `/ws/chat/{conversation_id}` connection. All seven tools register at `Tier.SAFE`, no enable flag. Both subpackages read the current conversation/agent via the existing `kc_supervisor.scheduling.context` contextvar (set by ws_routes/inbound before each agent invocation).

**Tech Stack:** Python 3.11+, SQLite (existing supervisor DB at `~/KonaClaw/data/konaclaw.db`), `pytest` + `pytest-asyncio`, React + TypeScript + Vitest, existing `ApprovalBroker` / `ApprovalCard` patterns, existing `NewsWidget` sidebar pattern.

**Spec:** `docs/superpowers/specs/2026-05-11-todo-clarify-design.md` (commit `30a8026`).

---

## File Structure

**New supervisor files:**

```
kc-supervisor/src/kc_supervisor/
  todos/
    __init__.py
    storage.py             # TodoStorage class — SQLite CRUD
    tools.py               # build_todo_tools(storage, current_context, broadcast) -> list[Tool]
  clarify/
    __init__.py
    broker.py              # ClarifyBroker — mirrors ApprovalBroker
    tools.py               # build_clarify_tool(broker, current_context) -> Tool

kc-supervisor/tests/
  test_todos_storage.py
  test_todos_tools.py
  test_clarify_broker.py
  test_clarify_tool.py
  test_http_todos.py
  test_ws_clarify.py
```

**Modified supervisor files:**

- `kc-supervisor/src/kc_supervisor/storage.py` — add `todos` table to `SCHEMA` constant; add idempotent ALTER for existing DBs.
- `kc-supervisor/src/kc_supervisor/service.py` — add `todo_storage` and `clarify_broker` fields to `Deps`.
- `kc-supervisor/src/kc_supervisor/main.py` — construct `TodoStorage` and `ClarifyBroker`; pass through.
- `kc-supervisor/src/kc_supervisor/agents.py` — add `todo_storage` and `clarify_broker` to `AgentRegistry`; thread through to `assemble_agent`.
- `kc-supervisor/src/kc_supervisor/assembly.py` — accept the two new kwargs; register todo tools + clarify tool on Kona at `Tier.SAFE`.
- `kc-supervisor/src/kc_supervisor/http_routes.py` — add GET/POST/PATCH/DELETE `/todos` routes + bulk DELETE.
- `kc-supervisor/src/kc_supervisor/ws_routes.py` — handle inbound `clarify_response` frames; re-emit `clarify_request` for pending requests on WS reconnect; emit `todo_event` frames on storage mutations.
- `kc-supervisor/tests/test_assembly.py` — add 4 test cases for the new tool registrations.

**New dashboard files:**

```
kc-dashboard/src/
  api/todos.ts                          # typed wrapper around /todos routes
  components/TodoWidget.tsx             # right-sidebar widget (parallel to NewsWidget)
  components/TodoItem.tsx               # one row in the widget
  components/ClarifyCard.tsx            # inline card in chat transcript

kc-dashboard/tests/
  api/todos.test.ts
  components/TodoWidget.test.tsx
  components/TodoItem.test.tsx
  components/ClarifyCard.test.tsx
```

**Modified dashboard files:**

- `kc-dashboard/src/views/Chat.tsx` — mount `TodoWidget` next to `NewsWidget`; render `ClarifyCard` for pending clarify requests; handle `clarify_request` / `clarify_response` / `todo_event` WS frames.

**New doc:**

- `docs/superpowers/specs/2026-05-1X-todo-clarify-SMOKE.md` — manual smoke checklist (filename date filled in at SMOKE run time).

---

## Task 1: Storage schema migration

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Test: `kc-supervisor/tests/test_todos_storage.py` (new — schema portion only; CRUD comes in Task 2)

- [ ] **Step 1: Write the failing test**

Create `kc-supervisor/tests/test_todos_storage.py`:

```python
import pytest
from kc_supervisor.storage import Storage


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "kc.db"


def test_init_creates_todos_table(db_path):
    s = Storage(db_path)
    s.init()
    with s.connect() as c:
        rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='todos'").fetchall()
    assert len(rows) == 1


def test_todos_table_has_expected_columns(db_path):
    s = Storage(db_path)
    s.init()
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(todos)").fetchall()}
    expected = {"id", "agent", "conversation_id", "title", "notes", "status", "created_at", "updated_at"}
    assert expected <= cols, f"missing columns: {expected - cols}"


def test_todos_indices_present(db_path):
    s = Storage(db_path)
    s.init()
    with s.connect() as c:
        idx_names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='todos'"
        ).fetchall()}
    assert "idx_todos_agent_conv" in idx_names
    assert "idx_todos_status" in idx_names


def test_init_is_idempotent(db_path):
    s = Storage(db_path)
    s.init()
    s.init()  # second call must not raise
    with s.connect() as c:
        rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='todos'").fetchall()
    assert len(rows) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_todos_storage.py -v`
Expected: 4 failures — `no such table: todos`.

- [ ] **Step 3: Add the `todos` table to the `SCHEMA` constant**

In `kc-supervisor/src/kc_supervisor/storage.py`, find the `SCHEMA = """..."""` block (around line 10 — contains `CREATE TABLE IF NOT EXISTS conversations` and friends). Append this just before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS todos (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  agent           TEXT    NOT NULL,
  conversation_id INTEGER NULL,
  title           TEXT    NOT NULL,
  notes           TEXT    NOT NULL DEFAULT '',
  status          TEXT    NOT NULL CHECK (status IN ('open','done')) DEFAULT 'open',
  created_at      REAL    NOT NULL,
  updated_at      REAL    NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_todos_agent_conv ON todos (agent, conversation_id);
CREATE INDEX IF NOT EXISTS idx_todos_status     ON todos (status);
```

The `IF NOT EXISTS` ensures `Storage.init()` is safe to call repeatedly on existing DBs without raising.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest kc-supervisor/tests/test_todos_storage.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Run the full existing supervisor test suite to confirm no regression**

Run: `pytest kc-supervisor/tests/ -q 2>&1 | tail -5`
Expected: same passing count as before plus 4 new ones (375 → 379 or thereabouts).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_todos_storage.py
git commit -m "feat(kc-supervisor): add todos table to storage schema (Phase C)"
```

---

## Task 2: TodoStorage CRUD class

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/todos/__init__.py`
- Create: `kc-supervisor/src/kc_supervisor/todos/storage.py`
- Modify: `kc-supervisor/tests/test_todos_storage.py` (append CRUD tests)

- [ ] **Step 1: Write the failing CRUD tests**

Append to `kc-supervisor/tests/test_todos_storage.py`:

```python
from kc_supervisor.todos.storage import TodoStorage


@pytest.fixture
def todo_store(db_path):
    s = Storage(db_path)
    s.init()
    # Insert a conversation row so FK references succeed
    with s.connect() as c:
        c.execute("INSERT INTO conversations (id, agent, channel, started_at) VALUES (?, ?, ?, ?)",
                  (40, "Kona-AI", "dashboard", 1.0))
    return TodoStorage(s)


def test_add_creates_conversation_scoped_item(todo_store):
    item = todo_store.add(agent="Kona-AI", conversation_id=40, title="Book hotel", notes="cheap one", persist=False)
    assert item["id"] > 0
    assert item["title"] == "Book hotel"
    assert item["notes"] == "cheap one"
    assert item["status"] == "open"
    assert item["scope"] == "conversation"
    assert item["conversation_id"] == 40


def test_add_with_persist_makes_agent_scoped(todo_store):
    item = todo_store.add(agent="Kona-AI", conversation_id=40, title="Renew passport", persist=True)
    assert item["scope"] == "agent"
    assert item["conversation_id"] is None


def test_add_missing_title_raises(todo_store):
    with pytest.raises(ValueError, match="title"):
        todo_store.add(agent="Kona-AI", conversation_id=40, title="   ", persist=False)


def test_list_all_scope_returns_conv_plus_agent(todo_store):
    todo_store.add(agent="Kona-AI", conversation_id=40, title="conv item", persist=False)
    todo_store.add(agent="Kona-AI", conversation_id=40, title="persistent", persist=True)
    items = todo_store.list(agent="Kona-AI", conversation_id=40, status="open", scope="all")
    titles = {i["title"] for i in items}
    assert titles == {"conv item", "persistent"}


def test_list_conversation_scope_only(todo_store):
    todo_store.add(agent="Kona-AI", conversation_id=40, title="conv item", persist=False)
    todo_store.add(agent="Kona-AI", conversation_id=40, title="persistent", persist=True)
    items = todo_store.list(agent="Kona-AI", conversation_id=40, status="open", scope="conversation")
    titles = {i["title"] for i in items}
    assert titles == {"conv item"}


def test_list_agent_scope_only(todo_store):
    todo_store.add(agent="Kona-AI", conversation_id=40, title="conv item", persist=False)
    todo_store.add(agent="Kona-AI", conversation_id=40, title="persistent", persist=True)
    items = todo_store.list(agent="Kona-AI", conversation_id=40, status="open", scope="agent")
    titles = {i["title"] for i in items}
    assert titles == {"persistent"}


def test_list_status_filter(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    todo_store.add(agent="Kona-AI", conversation_id=40, title="B", persist=False)
    todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])
    open_items = todo_store.list(agent="Kona-AI", conversation_id=40, status="open", scope="all")
    done_items = todo_store.list(agent="Kona-AI", conversation_id=40, status="done", scope="all")
    all_items  = todo_store.list(agent="Kona-AI", conversation_id=40, status="all",  scope="all")
    assert {i["title"] for i in open_items} == {"B"}
    assert {i["title"] for i in done_items} == {"A"}
    assert {i["title"] for i in all_items}  == {"A", "B"}


def test_complete_idempotent(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    r1 = todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])
    r2 = todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])  # already done
    assert r1["status"] == "done"
    assert r2["status"] == "done"


def test_complete_not_found(todo_store):
    with pytest.raises(LookupError):
        todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=99999)


def test_complete_wrong_agent(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    with pytest.raises(PermissionError):
        todo_store.complete(agent="Other-Agent", conversation_id=40, todo_id=a["id"])


def test_update_title_and_notes(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", notes="n1", persist=False)
    r = todo_store.update(agent="Kona-AI", conversation_id=40, todo_id=a["id"],
                          title="A renamed", notes="n2")
    assert r["title"] == "A renamed"
    assert r["notes"] == "n2"


def test_update_requires_at_least_one_field(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    with pytest.raises(ValueError, match="missing_fields"):
        todo_store.update(agent="Kona-AI", conversation_id=40, todo_id=a["id"],
                          title=None, notes=None)


def test_delete_removes_row(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    todo_store.delete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])
    with pytest.raises(LookupError):
        todo_store.delete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])


def test_clear_done_all_scope(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    b = todo_store.add(agent="Kona-AI", conversation_id=40, title="B", persist=True)
    c = todo_store.add(agent="Kona-AI", conversation_id=40, title="C", persist=False)
    todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=a["id"])
    todo_store.complete(agent="Kona-AI", conversation_id=40, todo_id=b["id"])
    n = todo_store.clear_done(agent="Kona-AI", conversation_id=40, scope="all")
    assert n == 2
    remaining = todo_store.list(agent="Kona-AI", conversation_id=40, status="all", scope="all")
    assert {i["title"] for i in remaining} == {"C"}


def test_wrong_conversation_for_conv_scoped(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=False)
    with pytest.raises(PermissionError):
        todo_store.complete(agent="Kona-AI", conversation_id=999, todo_id=a["id"])


def test_wrong_conversation_does_not_apply_to_agent_scoped(todo_store):
    a = todo_store.add(agent="Kona-AI", conversation_id=40, title="A", persist=True)
    # Any conversation under the same agent can touch agent-scoped items.
    r = todo_store.complete(agent="Kona-AI", conversation_id=999, todo_id=a["id"])
    assert r["status"] == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_todos_storage.py -v -k "add or list or complete or update or delete or clear or wrong"`
Expected: ImportError on `kc_supervisor.todos.storage` (module doesn't exist).

- [ ] **Step 3: Create the subpackage**

Write `kc-supervisor/src/kc_supervisor/todos/__init__.py`:

```python
"""KonaClaw todo tools (Phase C).

Conversation-internal task list. Items are scoped to a conversation by
default; passing persist=True at creation lifts an item to agent-scope.
Backed by the supervisor's SQLite. Wired into Kona via the assembly.
"""
```

Write `kc-supervisor/src/kc_supervisor/todos/storage.py`:

```python
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
```

- [ ] **Step 4: Run all tests in the file to verify**

Run: `pytest kc-supervisor/tests/test_todos_storage.py -v`
Expected: all ~16 PASS (4 schema + ~12 CRUD).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/todos/ kc-supervisor/tests/test_todos_storage.py
git commit -m "feat(kc-supervisor): TodoStorage CRUD with conversation/agent scope (Phase C)"
```

---

## Task 3: Todo agent tools (six ops)

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/todos/tools.py`
- Test: `kc-supervisor/tests/test_todos_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_todos_tools.py`:

```python
import json
import pytest

from kc_supervisor.storage import Storage
from kc_supervisor.todos.storage import TodoStorage
from kc_supervisor.todos.tools import build_todo_tools


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (id, agent, channel, started_at) VALUES (?, ?, ?, ?)",
                  (40, "Kona-AI", "dashboard", 1.0))
    return TodoStorage(s)


@pytest.fixture
def tools(store):
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    return {t.name: t for t in build_todo_tools(
        storage=store,
        current_context=lambda: ctx,
        broadcast=lambda event: None,  # tested separately in Task 9
    )}


def test_builder_returns_six_tools(tools):
    assert set(tools.keys()) == {
        "todo.add", "todo.list", "todo.complete",
        "todo.update", "todo.delete", "todo.clear_done",
    }


def test_add_happy_path(tools):
    out = json.loads(tools["todo.add"].impl(title="Book hotel"))
    assert out["title"] == "Book hotel"
    assert out["scope"] == "conversation"
    assert out["status"] == "open"


def test_add_missing_title(tools):
    out = json.loads(tools["todo.add"].impl(title="   "))
    assert out == {"error": "missing_title"}


def test_add_persist(tools):
    out = json.loads(tools["todo.add"].impl(title="Renew passport", persist=True))
    assert out["scope"] == "agent"


def test_list_default_status_open(tools):
    tools["todo.add"].impl(title="A")
    tools["todo.add"].impl(title="B")
    out = json.loads(tools["todo.list"].impl())
    assert out["count"] == 2
    assert {i["title"] for i in out["items"]} == {"A", "B"}


def test_list_invalid_status(tools):
    out = json.loads(tools["todo.list"].impl(status="garbage"))
    assert out == {"error": "invalid_status", "value": "garbage"}


def test_list_invalid_scope(tools):
    out = json.loads(tools["todo.list"].impl(scope="garbage"))
    assert out == {"error": "invalid_scope", "value": "garbage"}


def test_complete_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    r = json.loads(tools["todo.complete"].impl(id=a["id"]))
    assert r["status"] == "done"


def test_complete_missing_id(tools):
    out = json.loads(tools["todo.complete"].impl())
    assert out == {"error": "missing_id"}


def test_complete_not_found(tools):
    out = json.loads(tools["todo.complete"].impl(id=99999))
    assert out == {"error": "not_found", "id": 99999}


def test_update_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.update"].impl(id=a["id"], title="A renamed", notes="n"))
    assert out["title"] == "A renamed"
    assert out["notes"] == "n"


def test_update_missing_fields(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.update"].impl(id=a["id"]))
    assert out == {"error": "missing_fields"}


def test_delete_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.delete"].impl(id=a["id"]))
    assert out == {"id": a["id"], "deleted": True}


def test_clear_done_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    b = json.loads(tools["todo.add"].impl(title="B"))
    tools["todo.complete"].impl(id=a["id"])
    tools["todo.complete"].impl(id=b["id"])
    out = json.loads(tools["todo.clear_done"].impl())
    assert out == {"deleted_count": 2}


def test_broadcast_called_on_mutation(store):
    events = []
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    tools = {t.name: t for t in build_todo_tools(
        storage=store,
        current_context=lambda: ctx,
        broadcast=lambda event: events.append(event),
    )}
    tools["todo.add"].impl(title="A")
    # We expect one event after add. Shape verified in Task 9.
    assert len(events) == 1
    assert events[0]["action"] == "added"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_todos_tools.py -v`
Expected: ImportError on `kc_supervisor.todos.tools`.

- [ ] **Step 3: Implement `kc-supervisor/src/kc_supervisor/todos/tools.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest kc-supervisor/tests/test_todos_tools.py -v`
Expected: ~15 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/todos/tools.py kc-supervisor/tests/test_todos_tools.py
git commit -m "feat(kc-supervisor): six todo.* agent tools (Phase C)"
```

---

## Task 4: ClarifyBroker

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/clarify/__init__.py`
- Create: `kc-supervisor/src/kc_supervisor/clarify/broker.py`
- Test: `kc-supervisor/tests/test_clarify_broker.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_clarify_broker.py`:

```python
import asyncio
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker


@pytest.mark.asyncio
async def test_request_allocates_id_and_publishes():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda frame: seen.append(frame))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice="A")

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out["choice"] == "A"
    assert out["choice_index"] == 0
    assert "elapsed_ms" in out
    assert len(seen) == 1
    assert seen[0]["type"] == "clarify_request"
    assert seen[0]["question"] == "Q?"
    assert seen[0]["choices"] == ["A", "B"]


@pytest.mark.asyncio
async def test_skip_returns_skipped_payload():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda frame: seen.append(frame))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice=None, reason="skipped")

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out == {"choice": None, "reason": "skipped"}


@pytest.mark.asyncio
async def test_timeout_returns_timeout_payload():
    broker = ClarifyBroker()
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=0.1,
    )
    assert out["choice"] is None
    assert out["reason"] == "timeout"
    assert out["elapsed_ms"] >= 80
    assert out["elapsed_ms"] < 1000


def test_resolve_unknown_id_is_noop():
    broker = ClarifyBroker()
    # Should not raise.
    broker.resolve("does-not-exist", choice="A")


@pytest.mark.asyncio
async def test_resolve_already_resolved_is_noop():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice="A")
        broker.resolve(rid, choice="B")  # second resolve, must not raise

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out["choice"] == "A"


@pytest.mark.asyncio
async def test_pending_for_conversation():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        # Start two in conv 40, one in conv 41.
        t40a = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="A?", choices=["x", "y"], timeout_seconds=10,
        ))
        t40b = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="B?", choices=["x", "y"], timeout_seconds=10,
        ))
        t41  = asyncio.create_task(broker.request_clarification(
            conversation_id=41, agent="Kona-AI",
            question="C?", choices=["x", "y"], timeout_seconds=10,
        ))
        await asyncio.sleep(0.05)
        pending40 = broker.pending_for_conversation(40)
        pending41 = broker.pending_for_conversation(41)
        assert len(pending40) == 2
        assert len(pending41) == 1
        assert {p["question"] for p in pending40} == {"A?", "B?"}
        assert pending41[0]["question"] == "C?"
        # Resolve all so the tasks finish.
        for f in seen:
            broker.resolve(f["request_id"], choice="x")
        await asyncio.gather(t40a, t40b, t41)

    await driver()


@pytest.mark.asyncio
async def test_concurrent_requests_unique_ids():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        tasks = [
            asyncio.create_task(broker.request_clarification(
                conversation_id=40, agent="Kona-AI",
                question=f"Q{i}", choices=["x", "y"], timeout_seconds=10,
            )) for i in range(5)
        ]
        await asyncio.sleep(0.05)
        rids = {f["request_id"] for f in seen}
        assert len(rids) == 5
        for f in seen:
            broker.resolve(f["request_id"], choice="x")
        await asyncio.gather(*tasks)

    await driver()


@pytest.mark.asyncio
async def test_subscriber_exception_swallowed():
    broker = ClarifyBroker()
    broker.subscribe(lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    captured = []
    broker.subscribe(lambda f: captured.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["x", "y"], timeout_seconds=10,
        ))
        await asyncio.sleep(0.05)
        # The good subscriber still got the frame despite the bad one raising.
        assert len(captured) == 1
        broker.resolve(captured[0]["request_id"], choice="x")
        await task

    await driver()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_clarify_broker.py -v`
Expected: ImportError.

- [ ] **Step 3: Create the subpackage + broker**

Write `kc-supervisor/src/kc_supervisor/clarify/__init__.py`:

```python
"""KonaClaw clarify tool (Phase C).

Blocking multiple-choice question — Kona pauses, the dashboard renders a card,
the user clicks, Kona continues with the answer. Mirrors ApprovalBroker for
the async-future pattern; mirrors ApprovalCard for the UI shape.
"""
```

Write `kc-supervisor/src/kc_supervisor/clarify/broker.py`:

```python
from __future__ import annotations
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class _PendingClarify:
    request_id:      str
    conversation_id: int
    agent:           str
    question:        str
    choices:         list[str]
    started_at:      float
    timeout_seconds: int
    future:          asyncio.Future = field(repr=False)
    loop:            asyncio.AbstractEventLoop = field(repr=False)


class ClarifyBroker:
    """In-memory broker for pending clarification requests.

    Mirrors ApprovalBroker — request_clarification() allocates a future, calls
    subscribers synchronously with a clarify_request frame, then awaits the
    future via asyncio.wait_for(timeout=...). The WS handler calls resolve()
    when the user clicks a choice or Skip.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingClarify] = {}
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(fn)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(fn)
            except ValueError:
                pass

        return unsubscribe

    async def request_clarification(
        self,
        *,
        conversation_id: int,
        agent: str,
        question: str,
        choices: list[str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        started = time.time()
        pending = _PendingClarify(
            request_id=request_id,
            conversation_id=conversation_id,
            agent=agent,
            question=question,
            choices=list(choices),
            started_at=started,
            timeout_seconds=timeout_seconds,
            future=fut,
            loop=loop,
        )
        self._pending[request_id] = pending

        frame = {
            "type":             "clarify_request",
            "request_id":       request_id,
            "conversation_id":  conversation_id,
            "agent":            agent,
            "question":         question,
            "choices":          list(choices),
            "timeout_seconds":  timeout_seconds,
            "started_at":       started,
        }
        for sub in list(self._subscribers):
            try:
                sub(frame)
            except Exception:
                logger.exception("clarify subscriber raised; ignoring")

        try:
            return await asyncio.wait_for(fut, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            return {"choice": None, "reason": "timeout", "elapsed_ms": elapsed_ms}
        finally:
            self._pending.pop(request_id, None)

    def resolve(
        self,
        request_id: str,
        *,
        choice: Optional[str],
        reason: str = "answered",
    ) -> None:
        """Fulfill an outstanding clarify request. Unknown ids and
        already-resolved ids are silently ignored."""
        pending = self._pending.get(request_id)
        if pending is None:
            return
        fut, loop = pending.future, pending.loop
        if fut.done():
            return

        if choice is None:
            payload: dict[str, Any] = {"choice": None, "reason": reason}
        else:
            try:
                idx = pending.choices.index(choice)
            except ValueError:
                idx = -1
            elapsed_ms = int((time.time() - pending.started_at) * 1000)
            payload = {"choice": choice, "choice_index": idx, "elapsed_ms": elapsed_ms}

        def _set() -> None:
            if not fut.done():
                fut.set_result(payload)

        try:
            loop.call_soon_threadsafe(_set)
        except RuntimeError:
            # Loop closed — drop silently.
            return

    def pending_for_conversation(self, conversation_id: int) -> list[dict[str, Any]]:
        """Snapshot of currently-outstanding requests for one conversation
        (for /ws/chat/{N} reconnect handlers)."""
        out = []
        for p in self._pending.values():
            if p.conversation_id != conversation_id:
                continue
            out.append({
                "type":             "clarify_request",
                "request_id":       p.request_id,
                "conversation_id":  p.conversation_id,
                "agent":            p.agent,
                "question":         p.question,
                "choices":          list(p.choices),
                "timeout_seconds":  p.timeout_seconds,
                "started_at":       p.started_at,
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest kc-supervisor/tests/test_clarify_broker.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/clarify/ kc-supervisor/tests/test_clarify_broker.py
git commit -m "feat(kc-supervisor): ClarifyBroker — async multiple-choice questions (Phase C)"
```

---

## Task 5: Clarify agent tool

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/clarify/tools.py`
- Test: `kc-supervisor/tests/test_clarify_tool.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_clarify_tool.py`:

```python
import asyncio
import json
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker
from kc_supervisor.clarify.tools import build_clarify_tool


@pytest.fixture
def broker():
    return ClarifyBroker()


@pytest.fixture
def tool(broker):
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    return build_clarify_tool(broker=broker, current_context=lambda: ctx)


def test_tool_metadata(tool):
    assert tool.name == "clarify"
    assert "required" in tool.parameters
    assert set(tool.parameters["required"]) == {"question", "choices"}


@pytest.mark.asyncio
async def test_happy_path(tool, broker):
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.02)
        broker.resolve(seen[0]["request_id"], choice="Tuesday")

    asyncio.create_task(resolver())
    out = json.loads(await tool.impl(
        question="Which day?", choices=["Monday", "Tuesday"], timeout_seconds=2,
    ))
    assert out["choice"] == "Tuesday"
    assert out["choice_index"] == 1


@pytest.mark.asyncio
async def test_missing_question(tool):
    out = json.loads(await tool.impl(question="   ", choices=["a", "b"]))
    assert out == {"error": "missing_question"}


@pytest.mark.asyncio
async def test_missing_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=None))
    assert out == {"error": "missing_choices"}


@pytest.mark.asyncio
async def test_too_few_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=["only"]))
    assert out == {"error": "too_few_choices", "count": 1, "minimum": 2}


@pytest.mark.asyncio
async def test_too_many_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=[str(i) for i in range(9)]))
    assert out == {"error": "too_many_choices", "count": 9, "maximum": 8}


@pytest.mark.asyncio
async def test_duplicate_choices(tool):
    out = json.loads(await tool.impl(
        question="Q?", choices=["A", "B", "A", "C", "B"],
    ))
    assert out["error"] == "duplicate_choices"
    assert set(out["values"]) == {"A", "B"}


@pytest.mark.asyncio
async def test_timeout_clamped_low(tool, broker):
    # Pass 1 → clamped to 10 (minimum). We won't wait the full 10s; we'll
    # resolve quickly and just check it didn't time out at 1s.
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.05)
        broker.resolve(seen[0]["request_id"], choice="A")

    asyncio.create_task(resolver())
    out = json.loads(await tool.impl(
        question="Q?", choices=["A", "B"], timeout_seconds=1,
    ))
    # Frame should reflect the clamped value.
    assert seen[0]["timeout_seconds"] == 10
    assert out["choice"] == "A"


@pytest.mark.asyncio
async def test_timeout_clamped_high(tool, broker):
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.05)
        broker.resolve(seen[0]["request_id"], choice="A")

    asyncio.create_task(resolver())
    await tool.impl(question="Q?", choices=["A", "B"], timeout_seconds=99999)
    assert seen[0]["timeout_seconds"] == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_clarify_tool.py -v`
Expected: ImportError on `kc_supervisor.clarify.tools`.

- [ ] **Step 3: Implement `kc-supervisor/src/kc_supervisor/clarify/tools.py`**

```python
from __future__ import annotations
import json
from typing import Any, Callable

from kc_core.tools import Tool

from kc_supervisor.clarify.broker import ClarifyBroker


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


_DESCRIPTION = (
    "Ask the user a multiple-choice question and pause until they click an "
    "answer. The dashboard renders a card with one button per choice plus a "
    "Skip button. Returns the user's selection (or {choice: null, reason: "
    "'skipped'|'timeout'} if they decline or take too long). Best for "
    "narrow questions like picking a day, picking from a short list, or "
    "asking 'should I do X or Y?'. Don't use this for open-ended questions — "
    "the user can just type a regular reply faster."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "REQUIRED. The question text."},
        "choices":  {"type": "array", "items": {"type": "string"},
                     "description": "REQUIRED. 2-8 distinct option strings."},
        "timeout_seconds": {"type": "integer",
                            "description": "Optional. Default 300, clamped to [10, 600]."},
    },
    "required": ["question", "choices"],
}


def build_clarify_tool(
    broker: ClarifyBroker,
    current_context: Callable[[], dict],
) -> Tool:
    async def impl(
        question: str = "",
        choices: Any = None,
        timeout_seconds: int = 300,
    ) -> str:
        # Validate.
        if not isinstance(question, str) or not question.strip():
            return _json({"error": "missing_question"})
        if not isinstance(choices, list):
            return _json({"error": "missing_choices"})
        if not all(isinstance(c, str) for c in choices):
            return _json({"error": "missing_choices"})
        if len(choices) < 2:
            return _json({"error": "too_few_choices", "count": len(choices), "minimum": 2})
        if len(choices) > 8:
            return _json({"error": "too_many_choices", "count": len(choices), "maximum": 8})
        seen: set[str] = set()
        dupes: list[str] = []
        for c in choices:
            if c in seen and c not in dupes:
                dupes.append(c)
            seen.add(c)
        if dupes:
            return _json({"error": "duplicate_choices", "values": dupes})

        # Clamp.
        try:
            t = int(timeout_seconds)
        except (TypeError, ValueError):
            t = 300
        t = max(10, min(600, t))

        ctx = current_context()
        result = await broker.request_clarification(
            conversation_id=ctx["conversation_id"],
            agent=ctx["agent"],
            question=question.strip(),
            choices=list(choices),
            timeout_seconds=t,
        )
        return _json(result)

    return Tool(
        name="clarify",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        impl=impl,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest kc-supervisor/tests/test_clarify_tool.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/clarify/tools.py kc-supervisor/tests/test_clarify_tool.py
git commit -m "feat(kc-supervisor): clarify tool with validation + timeout clamp (Phase C)"
```

---

## Task 6: Wire into Deps, AgentRegistry, assemble_agent

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/service.py` — add fields to `Deps`
- Modify: `kc-supervisor/src/kc_supervisor/main.py` — construct and pass through
- Modify: `kc-supervisor/src/kc_supervisor/agents.py` — accept + thread through
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py` — accept kwargs, register tools
- Test: `kc-supervisor/tests/test_assembly.py` — append 4 cases

- [ ] **Step 1: Write the failing assembly tests**

Append to `kc-supervisor/tests/test_assembly.py` after the existing terminal section:

```python
# ------------------------------------------------------------------ Phase C
# (todo + clarify integration)


def test_todo_tools_registered_on_kona(home, tmp_path):
    """When todo_storage is supplied AND agent is named 'kona' or 'Kona-AI',
    the six todo.* tools register at Tier.SAFE."""
    from kc_supervisor.todos.storage import TodoStorage
    from kc_supervisor.storage import Storage
    db = Storage(tmp_path / "kc.db"); db.init()
    todo_storage = TodoStorage(db)

    # Need an agent named kona/Kona-AI for registration. The `home` fixture
    # creates "alice.yaml" — write a "kona.yaml" alongside it.
    (home / "agents" / "kona.yaml").write_text(
        "name: kona\nmodel: qwen2.5:7b\nsystem_prompt: I am kona.\n"
    )

    kwargs = _basic_assemble_kwargs(home)
    kwargs["cfg"] = AgentConfig(name="kona", model="qwen2.5:7b", system_prompt="I am kona.")
    a = assemble_agent(**kwargs, todo_storage=todo_storage)
    names = a.registry.names()
    for n in ("todo.add", "todo.list", "todo.complete", "todo.update", "todo.delete", "todo.clear_done"):
        assert n in names
        assert a.engine.tier_map[n] == Tier.SAFE


def test_todo_tools_absent_on_research_agent(home, tmp_path):
    from kc_supervisor.todos.storage import TodoStorage
    from kc_supervisor.storage import Storage
    db = Storage(tmp_path / "kc.db"); db.init()
    todo_storage = TodoStorage(db)
    # The 'alice' fixture is not kona — should not get todo tools.
    a = assemble_agent(**_basic_assemble_kwargs(home), todo_storage=todo_storage)
    names = a.registry.names()
    assert "todo.add" not in names


def test_clarify_tool_registered_on_kona(home, tmp_path):
    from kc_supervisor.clarify.broker import ClarifyBroker
    broker = ClarifyBroker()
    (home / "agents" / "kona.yaml").write_text(
        "name: kona\nmodel: qwen2.5:7b\nsystem_prompt: I am kona.\n"
    )
    kwargs = _basic_assemble_kwargs(home)
    kwargs["cfg"] = AgentConfig(name="kona", model="qwen2.5:7b", system_prompt="I am kona.")
    a = assemble_agent(**kwargs, clarify_broker=broker)
    assert "clarify" in a.registry.names()
    assert a.engine.tier_map["clarify"] == Tier.SAFE


def test_clarify_tool_absent_on_research_agent(home):
    from kc_supervisor.clarify.broker import ClarifyBroker
    broker = ClarifyBroker()
    a = assemble_agent(**_basic_assemble_kwargs(home), clarify_broker=broker)
    assert "clarify" not in a.registry.names()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_assembly.py -v -k "todo or clarify"`
Expected: 4 failures — `todo_storage` / `clarify_broker` not accepted by `assemble_agent`.

- [ ] **Step 3: Add fields to `Deps` in `service.py`**

In `kc-supervisor/src/kc_supervisor/service.py`, find the `Deps` dataclass (the existing field list ends around line 80 with `google_scopes: tuple[str, ...] = ()`). Add directly after `google_scopes`:

```python
    # Phase C — todo + clarify singletons. Constructed in main.py; threaded
    # to assemble_agent via AgentRegistry. None when the package isn't
    # importable, which keeps the supervisor bootable without Phase C.
    todo_storage:    Optional[Any] = None
    clarify_broker:  Optional[Any] = None
```

- [ ] **Step 4: Build them in `main.py` and thread through**

In `kc-supervisor/src/kc_supervisor/main.py`, find the news block (around line 38-50). Add this directly after the news block (or after the web block — anywhere before Deps construction):

```python
    # Phase C — todo + clarify singletons. Built unconditionally; both tools
    # only register on Kona inside assemble_agent.
    todo_storage = None
    clarify_broker = None
    try:
        from kc_supervisor.todos.storage import TodoStorage
        from kc_supervisor.clarify.broker import ClarifyBroker
        todo_storage = TodoStorage(storage)
        clarify_broker = ClarifyBroker()
    except ImportError:
        pass
```

Then find the `Deps(...)` constructor (around line 280). Add the two new kwargs to it:

```python
        # ... existing kwargs ...
        google_scopes=DEFAULT_GOOGLE_SCOPES,
        todo_storage=todo_storage,
        clarify_broker=clarify_broker,
    )
```

Find the AgentRegistry construction (search for `AgentRegistry(`). Pass the new singletons through:

```python
    # If your AgentRegistry constructor accepts kwargs like news_client=,
    # gmail_service=, etc., add:
    #     todo_storage=todo_storage,
    #     clarify_broker=clarify_broker,
    # alongside them.
```

(Look at the existing `AgentRegistry(...)` call in main.py and pattern-match — the constructor takes named singletons. Add `todo_storage=todo_storage, clarify_broker=clarify_broker,` to the kwargs.)

- [ ] **Step 5: Add fields to `AgentRegistry` and thread to `assemble_agent`**

In `kc-supervisor/src/kc_supervisor/agents.py`, find the `AgentRegistry.__init__` parameter list (around line 60-80, alongside `news_client`, `gmail_service`, `web_config`, etc). Add:

```python
        todo_storage:    "Optional[Any]" = None,
        clarify_broker:  "Optional[Any]" = None,
```

In the body of `__init__` where other kwargs are stored on `self`, add:

```python
        self.todo_storage    = todo_storage
        self.clarify_broker  = clarify_broker
```

In `AgentRegistry.load_all()`, find the call to `assemble_agent(...)` and add the new kwargs:

```python
                assembled = assemble_agent(
                    # ... existing kwargs ...
                    web_config=self.web_config,
                    todo_storage=self.todo_storage,
                    clarify_broker=self.clarify_broker,
                )
```

- [ ] **Step 6: Accept kwargs in `assemble_agent` and register tools**

In `kc-supervisor/src/kc_supervisor/assembly.py`, find the `assemble_agent` signature (search for `def assemble_agent`). Add the two kwargs to the signature, alongside `web_config`:

```python
    web_config: Optional[Any] = None,
    todo_storage:   Optional[Any] = None,
    clarify_broker: Optional[Any] = None,
) -> AssembledAgent:
```

Find the existing Kona-only scheduling block (around line 286, `if cfg.name == "kona" and schedule_service is not None:`). After that block, add the Phase C registrations:

```python
    # Phase C — todo + clarify tools. Registered ONLY on Kona (the assistant),
    # not on Research-Agent (the deep-dive worker). Both subpackages reuse the
    # scheduling context contextvar set by ws_routes/inbound before invoking
    # the agent.
    if cfg.name in ("kona", "Kona-AI") and todo_storage is not None:
        from kc_supervisor.todos.tools import build_todo_tools
        from kc_supervisor.scheduling.context import get_current_context

        def _broadcast_todo(event: dict) -> None:
            # Wired in Task 9 to push todo_event WS frames. For now, no-op
            # so the tool impls work end-to-end before the broadcaster lands.
            pass

        for t in build_todo_tools(
            storage=todo_storage,
            current_context=get_current_context,
            broadcast=_broadcast_todo,
        ):
            registry.register(t)
            tier_map[t.name] = Tier.SAFE

    if cfg.name in ("kona", "Kona-AI") and clarify_broker is not None:
        from kc_supervisor.clarify.tools import build_clarify_tool
        from kc_supervisor.scheduling.context import get_current_context

        clarify_tool = build_clarify_tool(
            broker=clarify_broker,
            current_context=get_current_context,
        )
        registry.register(clarify_tool)
        tier_map[clarify_tool.name] = Tier.SAFE
```

- [ ] **Step 7: Run the 4 new assembly tests**

Run: `pytest kc-supervisor/tests/test_assembly.py -v -k "todo or clarify"`
Expected: 4 PASS.

- [ ] **Step 8: Run the full supervisor test suite**

Run: `pytest kc-supervisor/tests/ -q 2>&1 | tail -5`
Expected: ~395+ passing (375 baseline + 16 todo storage + ~6 task-2 leftover + 8 broker + 9 tool + 4 assembly). No regressions.

- [ ] **Step 9: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/{service.py,main.py,agents.py,assembly.py} \
        kc-supervisor/tests/test_assembly.py
git commit -m "feat(kc-supervisor): wire todo + clarify singletons through Deps -> assembly (Phase C)"
```

---

## Task 7: HTTP /todos routes

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py` — add routes
- Test: `kc-supervisor/tests/test_http_todos.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_http_todos.py`:

```python
import pytest
from fastapi.testclient import TestClient

from kc_supervisor.storage import Storage
from kc_supervisor.todos.storage import TodoStorage


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up a minimal FastAPI app with /todos routes mounted against a
    real TodoStorage backed by a tmp SQLite. Doesn't need the full
    assemble_agent stack — http_routes.install just needs `deps`."""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from kc_supervisor.http_routes import install as install_http
    from kc_supervisor.approvals import ApprovalBroker

    s = Storage(tmp_path / "kc.db"); s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (id, agent, channel, started_at) VALUES (?,?,?,?)",
                  (40, "Kona-AI", "dashboard", 1.0))
    todo_storage = TodoStorage(s)

    app = FastAPI()
    app.state.deps = SimpleNamespace(
        storage=s, todo_storage=todo_storage, approvals=ApprovalBroker(),
        # any other attrs http_routes may touch — add as the tests reveal them
    )
    install_http(app, app.state.deps)
    return TestClient(app)


def test_get_todos_empty(client):
    r = client.get("/todos?conversation_id=40&agent=Kona-AI")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "count": 0}


def test_post_creates_todo(client):
    r = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "A"
    assert body["scope"] == "conversation"


def test_post_missing_title_422(client):
    r = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": ""})
    assert r.status_code == 422


def test_post_persist_true(client):
    r = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI",
                                     "title": "P", "persist": True})
    body = r.json()
    assert body["scope"] == "agent"


def test_get_after_add_returns_item(client):
    client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"})
    r = client.get("/todos?conversation_id=40&agent=Kona-AI")
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["title"] == "A"


def test_patch_updates_title(client):
    a = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "agent": "Kona-AI", "title": "renamed"})
    assert r.status_code == 200
    assert r.json()["title"] == "renamed"


def test_patch_status_done(client):
    a = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "agent": "Kona-AI", "status": "done"})
    assert r.json()["status"] == "done"


def test_patch_invalid_status_422(client):
    a = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "agent": "Kona-AI", "status": "garbage"})
    assert r.status_code == 422


def test_delete_removes(client):
    a = client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": "A"}).json()
    r = client.delete(f"/todos/{a['id']}?conversation_id=40&agent=Kona-AI")
    assert r.status_code == 204
    r2 = client.delete(f"/todos/{a['id']}?conversation_id=40&agent=Kona-AI")
    assert r2.status_code == 404


def test_bulk_delete_clear_done(client):
    for t in ("A", "B", "C"):
        client.post("/todos", json={"conversation_id": 40, "agent": "Kona-AI", "title": t})
    items = client.get("/todos?conversation_id=40&agent=Kona-AI").json()["items"]
    client.patch(f"/todos/{items[0]['id']}",
                 json={"conversation_id": 40, "agent": "Kona-AI", "status": "done"})
    client.patch(f"/todos/{items[1]['id']}",
                 json={"conversation_id": 40, "agent": "Kona-AI", "status": "done"})
    r = client.delete("/todos?conversation_id=40&agent=Kona-AI&scope=all&status=done")
    assert r.status_code == 200
    assert r.json() == {"deleted_count": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest kc-supervisor/tests/test_http_todos.py -v`
Expected: 404 / `AttributeError` — routes don't exist yet.

- [ ] **Step 3: Add the routes**

In `kc-supervisor/src/kc_supervisor/http_routes.py`, inside `install(app, deps)`, append:

```python
    # ----------- Phase C — todos -----------

    from pydantic import BaseModel, Field
    from typing import Optional as Opt

    class _TodoCreate(BaseModel):
        conversation_id: int
        agent:           str
        title:           str = Field(min_length=1)
        notes:           str = ""
        persist:         bool = False

    class _TodoPatch(BaseModel):
        conversation_id: int
        agent:           str
        title:           Opt[str] = None
        notes:           Opt[str] = None
        status:          Opt[str] = None
        # Pydantic-v2 status validation is enforced in the route below via
        # explicit `if req.status not in ("open", "done"): raise 422`. We
        # don't use a Pydantic validator here because the supervisor's
        # FastAPI version pins Pydantic v2 and the older __get_validators__
        # pattern silently no-ops.

    @app.get("/todos")
    def list_todos(conversation_id: int, agent: str,
                   status: str = "open", scope: str = "all"):
        from fastapi import HTTPException
        ts = app.state.deps.todo_storage
        if ts is None:
            raise HTTPException(503, detail="todo_storage not configured")
        try:
            items = ts.list(agent=agent, conversation_id=conversation_id,
                            status=status, scope=scope)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        return {"items": items, "count": len(items)}

    @app.post("/todos", status_code=201)
    def create_todo(req: _TodoCreate):
        from fastapi import HTTPException
        ts = app.state.deps.todo_storage
        if ts is None:
            raise HTTPException(503, detail="todo_storage not configured")
        if not req.title.strip():
            raise HTTPException(422, detail="title must be non-empty")
        return ts.add(agent=req.agent, conversation_id=req.conversation_id,
                      title=req.title, notes=req.notes, persist=req.persist)

    @app.patch("/todos/{todo_id}")
    def patch_todo(todo_id: int, req: _TodoPatch):
        from fastapi import HTTPException
        ts = app.state.deps.todo_storage
        if ts is None:
            raise HTTPException(503, detail="todo_storage not configured")
        if req.status is not None and req.status not in ("open", "done"):
            raise HTTPException(422, detail="status must be 'open' or 'done'")
        # If only status was given, route to complete or "reopen" (which we
        # do by patching the row directly through TodoStorage's update — but
        # update can't change status, so we go through complete OR a small
        # admin SQL path. Simplest: when status='done', call complete.
        # When status='open', flip via SQL.
        try:
            if req.title is not None or req.notes is not None:
                item = ts.update(agent=req.agent, conversation_id=req.conversation_id,
                                 todo_id=todo_id, title=req.title, notes=req.notes)
                if req.status == "done":
                    item = ts.complete(agent=req.agent, conversation_id=req.conversation_id,
                                       todo_id=todo_id)
                # No need to handle status='open' here (item is already open
                # unless previously completed; reopen flow is dashboard-only).
                return item
            else:
                if req.status == "done":
                    return ts.complete(agent=req.agent, conversation_id=req.conversation_id,
                                       todo_id=todo_id)
                if req.status == "open":
                    # Dashboard reopen: direct SQL flip on the storage layer.
                    # TodoStorage doesn't expose this op for agents on purpose
                    # (see spec: no todo.reopen for v1). Dashboard manipulation
                    # is allowed though.
                    return _reopen(ts, agent=req.agent,
                                   conversation_id=req.conversation_id, todo_id=todo_id)
                raise HTTPException(422, detail="nothing to update")
        except LookupError:
            raise HTTPException(404, detail="not_found")
        except PermissionError as e:
            raise HTTPException(403, detail=str(e))
        except ValueError as e:
            raise HTTPException(422, detail=str(e))

    def _reopen(ts, *, agent, conversation_id, todo_id):
        """Dashboard-only reopen path. Bypasses the agent tool surface
        deliberately — see spec's 'no todo.reopen for v1' decision."""
        import time
        with ts._storage.connect() as c:
            ts._load_and_authz(c, agent=agent, conversation_id=conversation_id, todo_id=todo_id)
            c.execute("UPDATE todos SET status='open', updated_at=? WHERE id=?",
                      (time.time(), todo_id))
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        from kc_supervisor.todos.storage import _row_to_dict
        return _row_to_dict(row)

    @app.delete("/todos/{todo_id}", status_code=204)
    def delete_todo(todo_id: int, conversation_id: int, agent: str):
        from fastapi import HTTPException, Response
        ts = app.state.deps.todo_storage
        if ts is None:
            raise HTTPException(503, detail="todo_storage not configured")
        try:
            ts.delete(agent=agent, conversation_id=conversation_id, todo_id=todo_id)
        except LookupError:
            raise HTTPException(404, detail="not_found")
        except PermissionError as e:
            raise HTTPException(403, detail=str(e))
        return Response(status_code=204)

    @app.delete("/todos")
    def bulk_delete_todos(conversation_id: int, agent: str,
                          scope: str = "all", status: str = "done"):
        from fastapi import HTTPException
        if status != "done":
            raise HTTPException(422, detail="bulk delete supports status=done only")
        ts = app.state.deps.todo_storage
        if ts is None:
            raise HTTPException(503, detail="todo_storage not configured")
        try:
            n = ts.clear_done(agent=agent, conversation_id=conversation_id, scope=scope)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        return {"deleted_count": n}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest kc-supervisor/tests/test_http_todos.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Run the full supervisor suite for regressions**

Run: `pytest kc-supervisor/tests/ -q 2>&1 | tail -5`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http_todos.py
git commit -m "feat(kc-supervisor): /todos HTTP routes (Phase C)"
```

---

## Task 8: WebSocket clarify frames in ws_routes.py

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py` — handle inbound clarify_response; re-emit pending clarifies on connect
- Test: `kc-supervisor/tests/test_ws_clarify.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_ws_clarify.py`:

```python
import asyncio
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker


@pytest.mark.asyncio
async def test_broker_resolve_via_response_handler():
    """Simulate ws_routes handling a clarify_response: route it to broker.resolve.
    This is a logic test, not a WS-protocol test — see SMOKE for the live path."""
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["A", "B"], timeout_seconds=2,
        ))
        await asyncio.sleep(0.02)
        rid = seen[0]["request_id"]
        # Simulate the WS handler routing an inbound frame:
        broker.resolve(rid, choice="B")
        out = await task
        assert out["choice"] == "B"
        assert out["choice_index"] == 1

    await driver()


@pytest.mark.asyncio
async def test_pending_for_conversation_after_reconnect_snapshot():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["A", "B"], timeout_seconds=5,
        ))
        await asyncio.sleep(0.02)
        # On WS reconnect for conv 40, the handler calls pending_for_conversation
        # and re-sends each frame. Snapshot here:
        snapshot = broker.pending_for_conversation(40)
        assert len(snapshot) == 1
        assert snapshot[0]["question"] == "Q?"
        assert snapshot[0]["type"] == "clarify_request"
        # Resolve so the task finishes:
        broker.resolve(snapshot[0]["request_id"], choice="A")
        await task

    await driver()
```

- [ ] **Step 2: Run tests to verify they pass already**

Run: `pytest kc-supervisor/tests/test_ws_clarify.py -v`
Expected: 2 PASS — these are broker-level tests that exercise the contract the WS handler will use. The handler itself is wiring; we'll add it in step 3 and rely on the SMOKE gate to validate the live path.

- [ ] **Step 3: Add the WS handlers in `ws_routes.py`**

In `kc-supervisor/src/kc_supervisor/ws_routes.py`, find the existing WS chat handler (`@router.websocket("/ws/chat/{conversation_id}")` or similar). Two changes:

**A. Subscribe to the clarify broker so new `clarify_request` frames go to the connected client:**

Near where the WS connection accepts and starts its message loop, add (after `await ws.accept()`):

```python
        # Phase C: subscribe to clarify_request frames for this conversation.
        clarify_broker = app.state.deps.clarify_broker
        clarify_unsubscribe = None
        if clarify_broker is not None:
            def _forward_clarify(frame: dict) -> None:
                if frame.get("conversation_id") != conversation_id:
                    return
                # ws.send_json is async; schedule onto the running loop.
                try:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json(frame),
                        asyncio.get_event_loop(),
                    )
                except Exception:
                    pass
            clarify_unsubscribe = clarify_broker.subscribe(_forward_clarify)
            # Re-emit any in-flight clarifies for this conversation (reconnect).
            for frame in clarify_broker.pending_for_conversation(conversation_id):
                await ws.send_json(frame)
```

Near the existing cleanup path (where the WS connection ends), add:

```python
        finally:
            if clarify_unsubscribe is not None:
                clarify_unsubscribe()
```

**B. Handle inbound `clarify_response` frames:**

Find the existing inbound-frame dispatcher (likely a `while True: msg = await ws.receive_json()` loop or similar). Add a new branch:

```python
            if msg.get("type") == "clarify_response":
                if clarify_broker is None:
                    continue  # silently drop — phase C not wired
                rid = msg.get("request_id")
                choice = msg.get("choice")  # may be None for skip
                reason = msg.get("reason", "answered" if choice is not None else "skipped")
                if isinstance(rid, str):
                    clarify_broker.resolve(rid, choice=choice, reason=reason)
                continue
```

- [ ] **Step 4: Run all supervisor tests for regressions**

Run: `pytest kc-supervisor/tests/ -q 2>&1 | tail -5`
Expected: no regressions; 2 new ws_clarify tests pass.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/ws_routes.py kc-supervisor/tests/test_ws_clarify.py
git commit -m "feat(kc-supervisor): WS clarify_request/response frames + reconnect re-emit (Phase C)"
```

---

## Task 9: WS todo_event broadcast on storage mutations

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py` — wire the broadcast callback
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py` — subscribe to todo events
- Modify: `kc-supervisor/src/kc_supervisor/service.py` — small `todo_broadcaster` shared object

- [ ] **Step 1: Add a TodoBroadcaster to `service.py`**

In `kc-supervisor/src/kc_supervisor/service.py`, add this class definition near the top (alongside `GoogleOAuthState`):

```python
class TodoBroadcaster:
    """Pub-sub for todo_event frames. Built once in main.py; subscribed to
    by each WS chat connection. The agent tools invoke .publish() on every
    mutation."""

    def __init__(self) -> None:
        self._subscribers: list = []

    def subscribe(self, fn):
        self._subscribers.append(fn)
        def unsubscribe():
            try:
                self._subscribers.remove(fn)
            except ValueError:
                pass
        return unsubscribe

    def publish(self, event: dict) -> None:
        for sub in list(self._subscribers):
            try:
                sub(event)
            except Exception:
                pass
```

In the `Deps` dataclass, add the field:

```python
    todo_broadcaster: Optional[Any] = None
```

- [ ] **Step 2: Construct it in main.py and pass through**

In `kc-supervisor/src/kc_supervisor/main.py`, near the todo_storage construction (from Task 6):

```python
    todo_broadcaster = None
    try:
        from kc_supervisor.service import TodoBroadcaster
        todo_broadcaster = TodoBroadcaster()
    except ImportError:
        pass
```

Pass it through to `Deps(...)` and to `AgentRegistry(...)` (same pattern as todo_storage in Task 6).

In `agents.py`, add the kwarg + `self.todo_broadcaster` assignment + thread through to `assemble_agent`.

- [ ] **Step 3: Wire the broadcaster into the todo tool registration in `assembly.py`**

In `assembly.py`, find the Task 6 todo-registration block. Replace the `_broadcast_todo` no-op with a real one:

```python
    if cfg.name in ("kona", "Kona-AI") and todo_storage is not None:
        from kc_supervisor.todos.tools import build_todo_tools
        from kc_supervisor.scheduling.context import get_current_context

        def _broadcast_todo(event: dict) -> None:
            if todo_broadcaster is None:
                return
            todo_broadcaster.publish({"type": "todo_event", **event})

        for t in build_todo_tools(
            storage=todo_storage,
            current_context=get_current_context,
            broadcast=_broadcast_todo,
        ):
            registry.register(t)
            tier_map[t.name] = Tier.SAFE
```

Add `todo_broadcaster` to the assemble_agent signature alongside the others.

- [ ] **Step 4: Subscribe per-WS-connection in `ws_routes.py`**

Near where the clarify subscription was added (Task 8), add a parallel subscription for todo_event:

```python
        # Phase C: subscribe to todo_event frames for this conversation.
        todo_broadcaster = app.state.deps.todo_broadcaster
        todo_unsubscribe = None
        if todo_broadcaster is not None:
            def _forward_todo(event: dict) -> None:
                if event.get("conversation_id") != conversation_id:
                    # Agent-scoped events have a conversation_id field too
                    # (the one Kona was in when she added/changed the
                    # persistent item). For now, only deliver to that
                    # conversation. Cross-conversation propagation of
                    # persistent items can be added later if needed.
                    return
                try:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json(event),
                        asyncio.get_event_loop(),
                    )
                except Exception:
                    pass
            todo_unsubscribe = todo_broadcaster.subscribe(_forward_todo)
```

Add `if todo_unsubscribe is not None: todo_unsubscribe()` to the cleanup path.

- [ ] **Step 5: Add a small integration test**

Append to `kc-supervisor/tests/test_todos_tools.py`:

```python
def test_broadcast_receives_event_via_real_broadcaster(store, monkeypatch):
    """End-to-end: a tool call -> _broadcast_todo -> TodoBroadcaster.publish ->
    subscriber sees the {type:todo_event, action:added, item:..., ...} dict."""
    from kc_supervisor.service import TodoBroadcaster
    bc = TodoBroadcaster()
    captured = []
    bc.subscribe(lambda e: captured.append(e))

    def emit(event):
        bc.publish({"type": "todo_event", **event})

    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    tools = {t.name: t for t in build_todo_tools(
        storage=store, current_context=lambda: ctx, broadcast=emit,
    )}
    tools["todo.add"].impl(title="A")
    assert len(captured) == 1
    e = captured[0]
    assert e["type"] == "todo_event"
    assert e["action"] == "added"
    assert e["item"]["title"] == "A"
    assert e["conversation_id"] == 40
    assert e["agent"] == "Kona-AI"
```

- [ ] **Step 6: Run tests**

Run: `pytest kc-supervisor/tests/test_todos_tools.py -v -k broadcast`
Expected: 2 PASS (the existing `test_broadcast_called_on_mutation` + the new end-to-end one).

- [ ] **Step 7: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/{service.py,main.py,agents.py,assembly.py,ws_routes.py} \
        kc-supervisor/tests/test_todos_tools.py
git commit -m "feat(kc-supervisor): TodoBroadcaster wires todo_event WS frames (Phase C)"
```

---

## Task 10: Dashboard `api/todos.ts` typed wrapper

**Files:**
- Create: `kc-dashboard/src/api/todos.ts`
- Test: `kc-dashboard/tests/api/todos.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/api/todos.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { listTodos, createTodo, patchTodo, deleteTodo, bulkDeleteTodos } from "../../src/api/todos";

describe("todos api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listTodos sends correct query and parses response", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ id: 1, title: "A" }], count: 1 }),
    } as any);
    const res = await listTodos({ conversationId: 40, agent: "Kona-AI" });
    expect(res.count).toBe(1);
    expect(res.items[0].title).toBe("A");
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos?");
    expect(fetchSpy.mock.calls[0][0]).toContain("conversation_id=40");
    expect(fetchSpy.mock.calls[0][0]).toContain("agent=Kona-AI");
  });

  it("createTodo POSTs JSON body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1, title: "A", scope: "conversation" }),
    } as any);
    await createTodo({ conversationId: 40, agent: "Kona-AI", title: "A" });
    const args = fetchSpy.mock.calls[0];
    expect(args[1].method).toBe("POST");
    expect(JSON.parse(args[1].body)).toEqual({
      conversation_id: 40, agent: "Kona-AI", title: "A", notes: "", persist: false,
    });
  });

  it("patchTodo PATCHes /todos/:id", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ id: 1, status: "done" }),
    } as any);
    await patchTodo({ id: 1, conversationId: 40, agent: "Kona-AI", status: "done" });
    const args = fetchSpy.mock.calls[0];
    expect(args[0]).toContain("/todos/1");
    expect(args[1].method).toBe("PATCH");
  });

  it("deleteTodo sends DELETE", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, status: 204, text: async () => "",
    } as any);
    await deleteTodo({ id: 5, conversationId: 40, agent: "Kona-AI" });
    expect(fetchSpy.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos/5");
  });

  it("bulkDeleteTodos sends DELETE with query params", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ deleted_count: 3 }),
    } as any);
    const res = await bulkDeleteTodos({
      conversationId: 40, agent: "Kona-AI", scope: "all", status: "done",
    });
    expect(res.deleted_count).toBe(3);
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos?");
    expect(fetchSpy.mock.calls[0][0]).toContain("status=done");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/api/todos.test.ts`
Expected: import errors / "module not found."

- [ ] **Step 3: Implement `kc-dashboard/src/api/todos.ts`**

Create the file:

```typescript
const BASE = "http://127.0.0.1:8765";

export type Todo = {
  id: number;
  agent: string;
  conversation_id: number | null;
  title: string;
  notes: string;
  status: "open" | "done";
  scope: "conversation" | "agent";
  created_at: number;
  updated_at: number;
};

export async function listTodos(args: {
  conversationId: number;
  agent: string;
  status?: "open" | "done" | "all";
  scope?: "all" | "conversation" | "agent";
}): Promise<{ items: Todo[]; count: number }> {
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
    agent: args.agent,
    status: args.status ?? "open",
    scope: args.scope ?? "all",
  });
  const r = await fetch(`${BASE}/todos?${params.toString()}`);
  if (!r.ok) throw new Error(`listTodos failed: ${r.status}`);
  return r.json();
}

export async function createTodo(args: {
  conversationId: number;
  agent: string;
  title: string;
  notes?: string;
  persist?: boolean;
}): Promise<Todo> {
  const r = await fetch(`${BASE}/todos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: args.conversationId,
      agent: args.agent,
      title: args.title,
      notes: args.notes ?? "",
      persist: args.persist ?? false,
    }),
  });
  if (!r.ok) throw new Error(`createTodo failed: ${r.status}`);
  return r.json();
}

export async function patchTodo(args: {
  id: number;
  conversationId: number;
  agent: string;
  title?: string;
  notes?: string;
  status?: "open" | "done";
}): Promise<Todo> {
  const body: Record<string, any> = {
    conversation_id: args.conversationId,
    agent: args.agent,
  };
  if (args.title !== undefined) body.title = args.title;
  if (args.notes !== undefined) body.notes = args.notes;
  if (args.status !== undefined) body.status = args.status;
  const r = await fetch(`${BASE}/todos/${args.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patchTodo failed: ${r.status}`);
  return r.json();
}

export async function deleteTodo(args: {
  id: number;
  conversationId: number;
  agent: string;
}): Promise<void> {
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
    agent: args.agent,
  });
  const r = await fetch(`${BASE}/todos/${args.id}?${params.toString()}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 204) throw new Error(`deleteTodo failed: ${r.status}`);
}

export async function bulkDeleteTodos(args: {
  conversationId: number;
  agent: string;
  scope: "all" | "conversation" | "agent";
  status: "done";
}): Promise<{ deleted_count: number }> {
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
    agent: args.agent,
    scope: args.scope,
    status: args.status,
  });
  const r = await fetch(`${BASE}/todos?${params.toString()}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`bulkDeleteTodos failed: ${r.status}`);
  return r.json();
}
```

- [ ] **Step 4: Run tests**

Run: `cd kc-dashboard && npx vitest run tests/api/todos.test.ts`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/api/todos.ts kc-dashboard/tests/api/todos.test.ts
git commit -m "feat(kc-dashboard): /todos typed API wrapper (Phase C)"
```

---

## Task 11: ClarifyCard component

**Files:**
- Create: `kc-dashboard/src/components/ClarifyCard.tsx`
- Test: `kc-dashboard/tests/components/ClarifyCard.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/components/ClarifyCard.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { ClarifyCard } from "../../src/components/ClarifyCard";

describe("ClarifyCard", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("renders question and choice buttons", () => {
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Which day?"
                   choices={["Mon", "Tue", "Wed"]} timeout_seconds={300}
                   started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    expect(getByText("Which day?")).toBeTruthy();
    expect(getByText("Mon")).toBeTruthy();
    expect(getByText("Tue")).toBeTruthy();
    expect(getByText("Wed")).toBeTruthy();
    expect(getByText(/Skip/i)).toBeTruthy();
  });

  it("calls onChoose with the picked choice", () => {
    const onChoose = vi.fn();
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={300} started_at={Date.now() / 1000}
                   onChoose={onChoose} onSkip={vi.fn()} />
    );
    fireEvent.click(getByText("B"));
    expect(onChoose).toHaveBeenCalledWith("r1", "B");
  });

  it("calls onSkip on the Skip button", () => {
    const onSkip = vi.fn();
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={300} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={onSkip} />
    );
    fireEvent.click(getByText(/Skip/i));
    expect(onSkip).toHaveBeenCalledWith("r1");
  });

  it("countdown decrements once per second", () => {
    const { container } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={10} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    expect(container.textContent).toContain("0:10");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(container.textContent).toContain("0:09");
  });

  it("disables buttons after timeout reaches 0 and shows 'Timed out'", () => {
    const { container, getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={2} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    act(() => { vi.advanceTimersByTime(2500); });
    expect(container.textContent).toMatch(/Timed out/i);
    const btnA = getByText("A").closest("button");
    expect(btnA?.disabled).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/components/ClarifyCard.test.tsx`
Expected: module not found / 5 failures.

- [ ] **Step 3: Implement `kc-dashboard/src/components/ClarifyCard.tsx`**

```typescript
import { useEffect, useState } from "react";

export type ClarifyCardProps = {
  request_id: string;
  question: string;
  choices: string[];
  timeout_seconds: number;
  started_at: number;          // seconds since epoch (server-supplied)
  onChoose: (request_id: string, choice: string) => void;
  onSkip: (request_id: string) => void;
};

function fmt(secs: number): string {
  if (secs < 0) secs = 0;
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ClarifyCard(props: ClarifyCardProps) {
  const deadline = props.started_at * 1000 + props.timeout_seconds * 1000;
  const [remaining, setRemaining] = useState(() => Math.max(0, deadline - Date.now()) / 1000);

  useEffect(() => {
    const id = setInterval(() => {
      setRemaining(Math.max(0, deadline - Date.now()) / 1000);
    }, 1000);
    return () => clearInterval(id);
  }, [deadline]);

  const timedOut = remaining <= 0;

  return (
    <div style={{
      border: "1px solid #d49a3a",
      background: "rgba(212, 154, 58, 0.08)",
      borderRadius: 6,
      padding: 12,
      margin: "12px 0",
    }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        ❓ {props.question}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {props.choices.map((c) => (
          <button
            key={c}
            disabled={timedOut}
            onClick={() => props.onChoose(props.request_id, c)}
            style={{
              padding: "6px 12px",
              border: "1px solid #555",
              borderRadius: 4,
              background: timedOut ? "#222" : "#2a2a2a",
              color: timedOut ? "#666" : "#eee",
              cursor: timedOut ? "default" : "pointer",
            }}
          >
            {c}
          </button>
        ))}
        <button
          disabled={timedOut}
          onClick={() => props.onSkip(props.request_id)}
          style={{
            marginLeft: "auto",
            padding: "6px 12px",
            border: "1px solid #555",
            borderRadius: 4,
            background: "transparent",
            color: "#888",
            cursor: timedOut ? "default" : "pointer",
          }}
        >
          Skip
        </button>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: "#888" }}>
        {timedOut
          ? "⏱ Timed out — Kona moved on"
          : `⏱ Kona is waiting · ${fmt(remaining)} remaining`}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd kc-dashboard && npx vitest run tests/components/ClarifyCard.test.tsx`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/components/ClarifyCard.tsx kc-dashboard/tests/components/ClarifyCard.test.tsx
git commit -m "feat(kc-dashboard): ClarifyCard component (Phase C)"
```

---

## Task 12: TodoItem component

**Files:**
- Create: `kc-dashboard/src/components/TodoItem.tsx`
- Test: `kc-dashboard/tests/components/TodoItem.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/components/TodoItem.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { TodoItem } from "../../src/components/TodoItem";

const sample = {
  id: 1, agent: "Kona-AI", conversation_id: 40, title: "Pack",
  notes: "warm clothes", status: "open" as const, scope: "conversation" as const,
  created_at: 1, updated_at: 1,
};

describe("TodoItem", () => {
  it("renders title and notes", () => {
    const { getByText } = render(
      <TodoItem todo={sample} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    expect(getByText("Pack")).toBeTruthy();
    expect(getByText(/warm clothes/)).toBeTruthy();
  });

  it("shows pin icon for agent-scoped items", () => {
    const persistent = { ...sample, scope: "agent" as const, conversation_id: null };
    const { container } = render(
      <TodoItem todo={persistent} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    expect(container.textContent).toContain("📌");
  });

  it("checkbox click fires onToggle with new status", () => {
    const onToggle = vi.fn();
    const { container } = render(
      <TodoItem todo={sample} onToggle={onToggle} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    const cb = container.querySelector("input[type=checkbox]") as HTMLInputElement;
    fireEvent.click(cb);
    expect(onToggle).toHaveBeenCalledWith(1, "done");
  });

  it("delete button fires onDelete", () => {
    const onDelete = vi.fn();
    const { getByLabelText } = render(
      <TodoItem todo={sample} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={onDelete} />
    );
    fireEvent.click(getByLabelText(/delete/i));
    expect(onDelete).toHaveBeenCalledWith(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/components/TodoItem.test.tsx`
Expected: module not found.

- [ ] **Step 3: Implement `kc-dashboard/src/components/TodoItem.tsx`**

```typescript
import type { Todo } from "../api/todos";

export type TodoItemProps = {
  todo: Todo;
  onToggle: (id: number, newStatus: "open" | "done") => void;
  onEdit:   (id: number) => void;
  onDelete: (id: number) => void;
};

export function TodoItem({ todo, onToggle, onEdit, onDelete }: TodoItemProps) {
  const isDone = todo.status === "done";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: "4px 6px",
        borderRadius: 3,
        opacity: isDone ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={isDone}
        onChange={() => onToggle(todo.id, isDone ? "open" : "done")}
        aria-label={`toggle ${todo.title}`}
      />
      <div
        style={{ flex: 1, cursor: "pointer" }}
        onClick={() => onEdit(todo.id)}
      >
        <div style={{
          fontSize: 12,
          fontWeight: 500,
          textDecoration: isDone ? "line-through" : "none",
        }}>
          {todo.scope === "agent" && <span title="persistent" style={{ marginRight: 4 }}>📌</span>}
          {todo.title}
        </div>
        {todo.notes && (
          <div style={{ fontSize: 10, color: "#888", marginTop: 2 }}>
            {todo.notes.split("\n")[0].slice(0, 80)}
          </div>
        )}
      </div>
      <button
        aria-label={`delete ${todo.title}`}
        onClick={() => onDelete(todo.id)}
        style={{
          background: "transparent",
          border: "none",
          color: "#888",
          cursor: "pointer",
          padding: 0,
        }}
        title="Delete"
      >
        ×
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd kc-dashboard && npx vitest run tests/components/TodoItem.test.tsx`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/components/TodoItem.tsx kc-dashboard/tests/components/TodoItem.test.tsx
git commit -m "feat(kc-dashboard): TodoItem component (Phase C)"
```

---

## Task 13: TodoWidget component

**Files:**
- Create: `kc-dashboard/src/components/TodoWidget.tsx`
- Test: `kc-dashboard/tests/components/TodoWidget.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/components/TodoWidget.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, fireEvent } from "@testing-library/react";
import { TodoWidget } from "../../src/components/TodoWidget";

beforeEach(() => {
  vi.restoreAllMocks();
});

function mockFetchOnce(payload: any, status = 200) {
  vi.spyOn(globalThis, "fetch" as any).mockResolvedValueOnce({
    ok: status < 400,
    status,
    json: async () => payload,
  } as any);
}

describe("TodoWidget", () => {
  it("renders empty state when no items", async () => {
    mockFetchOnce({ items: [], count: 0 });
    const { findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    expect(await findByText(/no todos yet/i)).toBeTruthy();
  });

  it("renders items returned from /todos", async () => {
    mockFetchOnce({
      items: [
        { id: 1, agent: "Kona-AI", conversation_id: 40, title: "Pack",
          notes: "", status: "open", scope: "conversation",
          created_at: 1, updated_at: 1 },
        { id: 2, agent: "Kona-AI", conversation_id: null, title: "Renew passport",
          notes: "", status: "open", scope: "agent",
          created_at: 2, updated_at: 2 },
      ],
      count: 2,
    });
    const { findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    expect(await findByText("Pack")).toBeTruthy();
    expect(await findByText("Renew passport")).toBeTruthy();
  });

  it("groups agent-scoped items under a 'Persistent' header", async () => {
    mockFetchOnce({
      items: [
        { id: 1, conversation_id: 40, title: "Pack", notes: "", status: "open",
          scope: "conversation", agent: "Kona-AI", created_at: 1, updated_at: 1 },
        { id: 2, conversation_id: null, title: "Renew", notes: "", status: "open",
          scope: "agent", agent: "Kona-AI", created_at: 2, updated_at: 2 },
      ],
      count: 2,
    });
    const { findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    expect(await findByText(/Persistent/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/components/TodoWidget.test.tsx`
Expected: module not found.

- [ ] **Step 3: Implement `kc-dashboard/src/components/TodoWidget.tsx`**

```typescript
import { useEffect, useState } from "react";
import { listTodos, patchTodo, deleteTodo, type Todo } from "../api/todos";
import { TodoItem } from "./TodoItem";

export type TodoWidgetProps = {
  conversationId: number;
  agent: string;
};

export function TodoWidget({ conversationId, agent }: TodoWidgetProps) {
  const [items, setItems] = useState<Todo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refetch = async () => {
    try {
      const res = await listTodos({ conversationId, agent });
      setItems(res.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => { void refetch(); }, [conversationId, agent]);

  if (error) return <div style={{ padding: 8, color: "#e57373" }}>todos: {error}</div>;
  if (items === null) return <div style={{ padding: 8, color: "#888" }}>Loading…</div>;
  if (items.length === 0) {
    return (
      <div style={{ padding: 8, color: "#888", fontStyle: "italic", fontSize: 11 }}>
        No todos yet — ask Kona to start a list.
      </div>
    );
  }

  const convItems  = items.filter((t) => t.scope === "conversation");
  const agentItems = items.filter((t) => t.scope === "agent");

  const onToggle = async (id: number, status: "open" | "done") => {
    setItems((prev) => prev?.map((t) => t.id === id ? { ...t, status } : t) ?? null);
    try {
      await patchTodo({ id, conversationId, agent, status });
    } catch {
      void refetch();
    }
  };

  const onDelete = async (id: number) => {
    setItems((prev) => prev?.filter((t) => t.id !== id) ?? null);
    try {
      await deleteTodo({ id, conversationId, agent });
    } catch {
      void refetch();
    }
  };

  const onEdit = (_id: number) => {
    // Inline-edit popover is a v2 polish. For now, the dashboard offers
    // status-toggle and delete; edits flow through Kona via chat.
  };

  return (
    <div style={{ padding: 6 }}>
      <div style={{
        fontSize: 11, color: "#aaa", marginBottom: 6,
        textTransform: "uppercase", letterSpacing: 1,
      }}>Todo</div>
      {convItems.map((t) => (
        <TodoItem key={t.id} todo={t}
                  onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
      ))}
      {agentItems.length > 0 && (
        <>
          <div style={{
            fontSize: 10, color: "#4a90e2", marginTop: 8, marginBottom: 4,
            fontWeight: 600,
          }}>📌 Persistent</div>
          {agentItems.map((t) => (
            <TodoItem key={t.id} todo={t}
                      onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
          ))}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd kc-dashboard && npx vitest run tests/components/TodoWidget.test.tsx`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/components/TodoWidget.tsx kc-dashboard/tests/components/TodoWidget.test.tsx
git commit -m "feat(kc-dashboard): TodoWidget right-sidebar component (Phase C)"
```

---

## Task 14: Mount widgets and wire WS frames in Chat.tsx

**Files:**
- Modify: `kc-dashboard/src/views/Chat.tsx`

This is mostly wiring — no new tests beyond what each component already has. Read the existing `Chat.tsx` patterns for `NewsWidget` mount and ApprovalCard WS handling; mirror them.

- [ ] **Step 1: Import the new components and api**

Near the existing imports at the top of `kc-dashboard/src/views/Chat.tsx`:

```typescript
import { TodoWidget } from "../components/TodoWidget";
import { ClarifyCard } from "../components/ClarifyCard";
```

- [ ] **Step 2: Track pending clarifies via WS events**

Inside the `Chat` component, after the existing `events` accumulation (around line 103 where `useChatSocket` is destructured), add:

```typescript
  const [pendingClarifies, setPendingClarifies] = useState<Array<{
    request_id: string; question: string; choices: string[];
    timeout_seconds: number; started_at: number;
  }>>([]);

  useEffect(() => {
    if (events.length === 0) return;
    const last = events[events.length - 1] as any;
    if (last?.type === "clarify_request") {
      setPendingClarifies((prev) => {
        if (prev.find((p) => p.request_id === last.request_id)) return prev;
        return [...prev, {
          request_id:      last.request_id,
          question:        last.question,
          choices:         last.choices,
          timeout_seconds: last.timeout_seconds,
          started_at:      last.started_at,
        }];
      });
    }
  }, [events]);

  const respondToClarify = (request_id: string, choice: string | null, reason?: string) => {
    sendUserMessage({
      type: "clarify_response",
      request_id,
      choice,
      ...(reason ? { reason } : {}),
    } as any);
    setPendingClarifies((prev) => prev.filter((p) => p.request_id !== request_id));
  };
```

(`sendUserMessage` is the existing send-helper from `useChatSocket` — it should already accept an arbitrary JSON-serializable payload. If it's strictly typed to `{type: "user_message", content: string}`, broaden the type signature in `useChatSocket` to accept any `{type: string, ...}` object.)

- [ ] **Step 3: Render ClarifyCard inline among the existing approval cards**

Find the existing `{pendingForAgent.map((req) => (` ApprovalCard render block (around line 500). Add a parallel render directly after it:

```tsx
          {pendingClarifies.map((req) => (
            <ClarifyCard
              key={req.request_id}
              request_id={req.request_id}
              question={req.question}
              choices={req.choices}
              timeout_seconds={req.timeout_seconds}
              started_at={req.started_at}
              onChoose={(rid, c) => respondToClarify(rid, c)}
              onSkip={(rid) => respondToClarify(rid, null, "skipped")}
            />
          ))}
```

- [ ] **Step 4: Mount TodoWidget alongside NewsWidget in the right sidebar**

Find where `NewsWidget` is rendered in the right sidebar (search Chat.tsx for `NewsWidget`). Add directly below it, in the same sidebar column:

```tsx
{activeConv && activeAgent && (
  <TodoWidget conversationId={activeConv} agent={activeAgent.name} />
)}
```

(Use whatever variable names the surrounding code uses for the active conversation id and active agent name.)

- [ ] **Step 5: Handle `todo_event` WS frames to refresh the widget**

In Chat.tsx, near the clarify_request handler from Step 2, add a parallel:

```typescript
  const [todoEventCounter, setTodoEventCounter] = useState(0);

  useEffect(() => {
    if (events.length === 0) return;
    const last = events[events.length - 1] as any;
    if (last?.type === "todo_event") {
      setTodoEventCounter((c) => c + 1);
    }
  }, [events]);
```

Then plumb `todoEventCounter` into TodoWidget so it can refetch on bump:

In `TodoWidget.tsx`, add an optional `refreshKey?: number` prop and include it in the `useEffect` dep array:

```typescript
export type TodoWidgetProps = {
  conversationId: number;
  agent: string;
  refreshKey?: number;
};

// ...
useEffect(() => { void refetch(); }, [conversationId, agent, refreshKey]);
```

In Chat.tsx, pass it through:

```tsx
<TodoWidget conversationId={activeConv} agent={activeAgent.name} refreshKey={todoEventCounter} />
```

- [ ] **Step 6: Run the dashboard test suite for regressions**

Run: `cd kc-dashboard && npx vitest run 2>&1 | tail -10`
Expected: all passing (or the pre-existing jsdom-env Chat.test.tsx failures stay at their pre-existing count — confirm via stash diff if uncertain).

- [ ] **Step 7: Commit**

```bash
git add kc-dashboard/src/views/Chat.tsx kc-dashboard/src/components/TodoWidget.tsx
git commit -m "feat(kc-dashboard): mount TodoWidget + ClarifyCard in Chat view (Phase C)"
```

---

## Task 15: SMOKE checklist doc

**Files:**
- Create: `docs/superpowers/specs/2026-05-1X-todo-clarify-SMOKE.md` (replace `1X` with the day SMOKE actually runs)

- [ ] **Step 1: Create the doc**

Replace `1X` in the filename with today's day-of-month when you create the file. Content:

```markdown
# Todo + Clarify Tools — Manual SMOKE Checklist

**Date:** 2026-05-1X
**Phase:** Tools Rollout — Phase C
**Spec:** docs/superpowers/specs/2026-05-11-todo-clarify-design.md
**Plan:** docs/superpowers/plans/2026-05-11-todo-clarify.md

## Preconditions

- [ ] Latest `main` deployed to local supervisor.
- [ ] Supervisor restarted; logs show no errors at startup.
- [ ] Dashboard reachable at http://localhost:5173.

## Gates

### 1. Todo round-trip via chat

**Action:** In a fresh Kona conversation, type:

> "Start a todo list for my NYC trip: book hotel, find restaurants, get euros"

**Expected:**
- Kona calls `todo.add` three times.
- Right sidebar (under NewsWidget) shows all 3 items.
- Audit log has 3 rows tagged `todo.add` at `tier=SAFE`.

**Actual:**

### 2. Manual tick from sidebar

**Action:** In the sidebar, click the checkbox next to "book hotel."

**Expected:**
- Item displays with strikethrough + reduced opacity.
- Kona's next reply (if you ask "what's left?") shows it as completed via `todo.list`.

**Actual:**

### 3. Hybrid scope (persist=true)

**Action:** Type:

> "Add a persistent reminder to renew my passport — that's a long-term thing, not just this trip"

**Expected:**
- Kona calls `todo.add` with `persist=true`.
- Sidebar shows the item under a "📌 Persistent" sub-header.
- Open a different conversation with Kona → the persistent item appears there too (the trip items do not).

**Actual:**

### 4. clear_done

**Action:** Tick a couple of trip items done, then type:

> "Clear out the completed items."

**Expected:**
- Kona calls `todo.clear_done`.
- Sidebar updates: completed items disappear; passport persistent item stays.

**Actual:**

### 5. Clarify happy path

**Action:** Type:

> "Schedule dinner with mom — give me a few options"

**Expected:**
- Kona calls `clarify` with 3-4 day options.
- An amber-bordered card appears inline in the chat with one button per choice + Skip + countdown.
- Click a choice → tool result has that choice; Kona continues with it.
- Card transitions to a resolved state showing your selection.

**Actual:**

### 6. Clarify skip

**Action:** Trigger a new clarify (any natural way). Click "Skip" instead of a choice.

**Expected:**
- Tool result: `{"choice": null, "reason": "skipped"}`.
- Kona handles it gracefully (asks in free text or moves on).

**Actual:**

### 7. Clarify timeout

**Action:** Ask Kona "what's something you could clarify with me — give me 10 seconds to answer or move on." (Goal: get her to call `clarify` with a short timeout.) If she won't pick a short timeout naturally, ask explicitly: "Call clarify with timeout_seconds=10 and ask which color I prefer."

**Expected:**
- Card shows countdown decrementing.
- After 10 seconds (clamped to 10 minimum), card transitions to "⏱ Timed out — Kona moved on" and buttons disable.
- Kona's tool result is `{"choice": null, "reason": "timeout", "elapsed_ms": ~10000}`.

**Actual:**

### 8. WS reconnect mid-clarify

**Action:** Trigger a clarify with a long timeout (e.g., 120s). While the card is rendered, hard-reload the dashboard tab (Cmd+Shift+R).

**Expected:**
- After reconnect, the same clarify card reappears.
- Countdown continues from the original `started_at` (so it shows less remaining than it did before the reload).
- Clicking a choice still resolves the awaiting tool call on the supervisor side.

**Actual:**

### 9. Dashboard manipulation

**Action:** In the sidebar, click a todo's checkbox → tick it done. Then click its `×` button.

**Expected:**
- Checkbox click: item shows strikethrough; PATCH `/todos/{id}` with `{status: "done"}`.
- Delete click: item removed from sidebar; DELETE `/todos/{id}` returns 204.
- Kona's next `todo.list` reflects both changes.

**Actual:**

### 10. Audit visibility

**Action:** After running gates 1-9, query the supervisor's audit table:

```sql
SELECT tool, decision, substr(args_json, 1, 80) FROM audit
WHERE tool LIKE 'todo.%' OR tool = 'clarify'
ORDER BY id DESC LIMIT 30;
```

**Expected:**
- All `todo.*` and `clarify` calls present.
- All show `decision = "tier"` (auto-allowed, no approval prompts).

**Actual:**

## Result

- [ ] All 10 gates pass.
- [ ] Memory updated with smoke status.
- [ ] If any gate fails, file an issue and do not consider Phase C shipped.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-1X-todo-clarify-SMOKE.md
git commit -m "docs: SMOKE gates for todo + clarify tools (Phase C)"
```

---

## Final checks

- [ ] **All tests pass**

Run from project root:
```
pytest kc-supervisor/tests/ -q 2>&1 | tail -3
cd kc-dashboard && npx vitest run 2>&1 | tail -5
```
Expected: supervisor 410+ passing (375 baseline + ~35 Phase C); dashboard delta matches the new test files (about +17 new test cases), pre-existing jsdom Chat.test.tsx env issues unchanged.

- [ ] **Branch ready for review**

Run: `git log --oneline | head -20`
Expected: ~15 task commits, one per task, plus the spec commit.

- [ ] **Proceed to SMOKE only after merge**

Phase C is "shipped" only when all 10 SMOKE gates pass on Sammy's running supervisor + dashboard after restart. Until then, the tools are inert (todo.* and clarify are registered, but they're new code paths with no real-world exercise).
