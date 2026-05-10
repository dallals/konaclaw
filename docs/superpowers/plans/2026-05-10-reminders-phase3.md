# Reminders Phase 3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a dashboard Reminders view (browse + snooze + cancel + bubble linking) with realtime WS-driven updates, backed by new HTTP endpoints and a `messages.scheduled_job_id` migration.

**Architecture:** Three layers. Backend: extend `ScheduleService` with `list_all_reminders` + `snooze_reminder`, switch cancel from hard-delete to soft `status='cancelled'`, add a `RemindersBroadcaster` (mirrors `ApprovalBroker.subscribe` pattern), add `/reminders` REST routes and `/ws/reminders` WS endpoint, populate `messages.scheduled_job_id` from `ReminderRunner.fire`. Frontend: new `Reminders.tsx` view with tabs/chips/list/expand-panel/snooze-popover, new WS hook that invalidates the React Query cache on lifecycle events, add a "from reminder #N" footer to assistant bubbles in `Chat.tsx`. Data: one additive column on `messages` plus a partial index, applied via the existing idempotent `Storage.init()` pattern.

**Tech Stack:** Python 3.14, FastAPI, APScheduler (SQLAlchemyJobStore), SQLite, pytest. React 18, TypeScript, Vite, React Query, react-router-dom, Vitest + Testing Library, Tailwind.

**Spec:** `docs/superpowers/specs/2026-05-10-reminders-phase3-design.md`

**Codebase pointers (read these before starting):**
- `kc-supervisor/src/kc_supervisor/scheduling/service.py` — `ScheduleService` (existing methods to extend)
- `kc-supervisor/src/kc_supervisor/scheduling/runner.py` — `ReminderRunner.fire` (needs to populate the new column + emit events)
- `kc-supervisor/src/kc_supervisor/storage.py` — schema and `Storage.init()` migration pattern
- `kc-supervisor/src/kc_supervisor/approvals.py` — `ApprovalBroker` (the broadcaster pattern to mirror)
- `kc-supervisor/src/kc_supervisor/ws_routes.py` — existing `/ws/approvals` shape
- `kc-supervisor/src/kc_supervisor/http_routes.py` — REST registration
- `kc-supervisor/src/kc_supervisor/service.py` — `Deps` dataclass
- `kc-supervisor/src/kc_supervisor/main.py` — production wiring
- `kc-dashboard/src/views/Audit.tsx`, `Agents.tsx` — visual style + React Query patterns to match
- `kc-dashboard/src/api/client.ts`, `audit.ts` — API client style
- `kc-dashboard/src/ws/useChatSocket.ts` — WS hook style
- `kc-dashboard/src/App.tsx` — nav/router registration

---

## File Structure (created or modified)

**kc-supervisor**
- *Modify* `src/kc_supervisor/storage.py` — `SCHEMA` adds `scheduled_job_id` to `messages` CREATE; `Storage.init()` adds idempotent ALTER + partial index.
- *Modify* `src/kc_supervisor/scheduling/service.py` — add `list_all_reminders`, `snooze_reminder`; switch `_do_cancel` to soft-cancel; emit broadcaster events.
- *Modify* `src/kc_supervisor/scheduling/runner.py` — populate `messages.scheduled_job_id`; emit `reminder.fired` / `reminder.failed`.
- *Create* `src/kc_supervisor/reminders_broadcaster.py` — `RemindersBroadcaster` (the new pub/sub).
- *Modify* `src/kc_supervisor/service.py` — add `reminders_broadcaster` to `Deps`.
- *Modify* `src/kc_supervisor/main.py` — construct broadcaster, inject into `ScheduleService` + `ReminderRunner`.
- *Modify* `src/kc_supervisor/http_routes.py` — `GET /reminders`, `DELETE /reminders/{id}`, `PATCH /reminders/{id}`.
- *Modify* `src/kc_supervisor/ws_routes.py` — `/ws/reminders` endpoint.
- *Create* tests under `tests/` for each new behavior.

**kc-dashboard**
- *Modify* `src/App.tsx` — register `/reminders` route + nav tab `07 — REMINDERS`.
- *Create* `src/api/reminders.ts` — typed REST client.
- *Create* `src/ws/useReminderEvents.ts` — WS hook that invalidates the reminders cache.
- *Create* `src/views/Reminders.tsx` — the new view.
- *Modify* `src/views/Chat.tsx` — render "from reminder #N" footer when `scheduled_job_id` present.
- *Create* tests alongside each new file.

**Docs**
- *Create* `docs/superpowers/specs/2026-05-10-reminders-phase3-SMOKE.md` — manual end-to-end checklist (Phase 2 mirror).

---

## Pre-flight

- [ ] **Step 0a: Confirm we're on the right branch** — Phase 3 builds on `main` (Phase 2 is merged). Either work directly on `main` per recent landings, or create a `phase3-reminders` branch. Check the git status before starting:
  ```bash
  cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
  git status && git log --oneline -3
  ```
  Expected: clean working tree on `main` with `1ae5151 docs: design spec for reminders Phase 3` near the top.

- [ ] **Step 0b: Smoke-run the existing test suites** so any failures attributed to later tasks are honest signals:
  ```bash
  cd kc-supervisor && uv run pytest -q
  cd ../kc-dashboard && npm test -- --run
  ```
  Expected: both green.

---

## Task 1: Migration — `messages.scheduled_job_id` column + partial index

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py` (the `SCHEMA` constant near the top, and `Storage.init()` near line 116)
- Test: `kc-supervisor/tests/test_storage_scheduled_job_id_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# kc-supervisor/tests/test_storage_scheduled_job_id_migration.py
from __future__ import annotations
import sqlite3
import time
import pytest
from kc_supervisor.storage import Storage


def _new_storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "test.db")
    s.init()
    return s


def test_messages_has_scheduled_job_id_column(tmp_path):
    s = _new_storage(tmp_path)
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    assert "scheduled_job_id" in cols


def test_partial_index_exists(tmp_path):
    s = _new_storage(tmp_path)
    with s.connect() as c:
        names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
        ).fetchall()}
    assert "idx_messages_scheduled_job_id" in names


def test_init_is_idempotent(tmp_path):
    s = _new_storage(tmp_path)
    s.init()  # second call must not raise
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    assert "scheduled_job_id" in cols


def test_legacy_db_gets_migrated(tmp_path):
    # Build a "legacy" DB that has messages but no scheduled_job_id column.
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as c:
        c.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, agent TEXT, channel TEXT, started_at REAL)")
        c.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY, conversation_id INTEGER, kind TEXT, "
            "content TEXT, ts REAL)"
        )
        c.execute("INSERT INTO conversations VALUES (1, 'a', 'dashboard', ?)", (time.time(),))
        c.execute("INSERT INTO messages VALUES (1, 1, 'user', 'hi', ?)", (time.time(),))

    s = Storage(db_path)
    s.init()  # must add the column without losing the row

    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
        rows = c.execute("SELECT id, scheduled_job_id FROM messages").fetchall()
    assert "scheduled_job_id" in cols
    assert len(rows) == 1
    assert rows[0]["scheduled_job_id"] is None


def test_fk_violation_when_referencing_unknown_job(tmp_path):
    s = _new_storage(tmp_path)
    with s.connect() as c:
        c.execute("INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                  ("a", "dashboard", time.time()))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO messages (conversation_id, kind, content, ts, scheduled_job_id) "
                "VALUES (1, 'user', 'x', ?, 99999)", (time.time(),)
            )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_storage_scheduled_job_id_migration.py -v
```
Expected: FAIL — `scheduled_job_id` column not found.

- [ ] **Step 3: Update `SCHEMA` so fresh DBs include the column**

Find the `messages` `CREATE TABLE` inside `SCHEMA` in `storage.py` (around line 20). Add the new column at the end of the column list (and a partial index after the table block). The exact edit:

```python
# storage.py — within the SCHEMA string, the messages CREATE block becomes:
"""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    arguments_json TEXT,
    ts REAL NOT NULL,
    usage_json TEXT,
    scheduled_job_id INTEGER REFERENCES scheduled_jobs(id) ON DELETE SET NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_scheduled_job_id
  ON messages(scheduled_job_id) WHERE scheduled_job_id IS NOT NULL;
"""
```

(Adjust to match the actual existing column list — read the file first; only the `scheduled_job_id` line and the partial index are new. Note: `usage_json` is already there from a prior idempotent ALTER, so leaving it in the base `CREATE` here is consistent with the existing pattern of "`SCHEMA` is the union of all migrations applied so far.")

- [ ] **Step 4: Add the idempotent ALTER inside `Storage.init()`**

Inside `Storage.init()`, after the existing `messages` ALTER for `usage_json`, extend the same pattern:

```python
# storage.py — inside Storage.init(), after the usage_json ALTER block
msg_cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
if "usage_json" not in msg_cols:
    c.execute("ALTER TABLE messages ADD COLUMN usage_json TEXT")
if "scheduled_job_id" not in msg_cols:
    # SQLite requires the FK column have a NULL default when added via ALTER.
    # PRAGMA foreign_keys = ON is set per-connection (see connect()), so
    # ON DELETE SET NULL is enforced.
    c.execute(
        "ALTER TABLE messages ADD COLUMN scheduled_job_id INTEGER "
        "REFERENCES scheduled_jobs(id) ON DELETE SET NULL"
    )
c.execute(
    "CREATE INDEX IF NOT EXISTS idx_messages_scheduled_job_id "
    "ON messages(scheduled_job_id) WHERE scheduled_job_id IS NOT NULL"
)
```

(Replace the existing `msg_cols`/`usage_json` block with the version above so we only call `PRAGMA table_info` once.)

- [ ] **Step 5: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_storage_scheduled_job_id_migration.py -v
```
Expected: PASS for all five tests.

- [ ] **Step 6: Run the full supervisor suite to catch regressions**

```bash
cd kc-supervisor && uv run pytest -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_storage_scheduled_job_id_migration.py
git commit -m "feat(kc-supervisor): messages.scheduled_job_id column + partial index for reminder bubble linking"
```

---

## Task 2: Storage helper for setting `scheduled_job_id` on the most recent message

The runner creates an `AssistantMessage` via `ConversationManager.append`. Rather than threading `scheduled_job_id` through every `append` call site, give `Storage` a focused helper the runner uses right after appending: "stamp this scheduled_job_id onto the message I just inserted." This keeps `ConversationManager.append` untouched.

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Test: `kc-supervisor/tests/test_storage_stamp_scheduled_job_id.py`

- [ ] **Step 1: Write the failing test**

```python
# kc-supervisor/tests/test_storage_stamp_scheduled_job_id.py
import time
import pytest
from kc_supervisor.storage import Storage


def _seed(tmp_path):
    s = Storage(tmp_path / "t.db"); s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                  ("a", "dashboard", time.time()))
        # Mirror the columns the actual messages table uses; the prod path is
        # ConversationManager.append, but we don't need it here.
        c.execute(
            "INSERT INTO messages (conversation_id, kind, content, ts) VALUES (1, 'assistant', 'hi', ?)",
            (time.time(),),
        )
        # Insert a scheduled_jobs row to satisfy the FK
        c.execute(
            "INSERT INTO scheduled_jobs (kind, agent, conversation_id, channel, chat_id, "
            "payload, when_utc, status, created_at) "
            "VALUES ('reminder','a',1,'dashboard','c1','x',?,'pending',?)",
            (time.time(), time.time()),
        )
    return s


def test_stamp_scheduled_job_id_on_message(tmp_path):
    s = _seed(tmp_path)
    s.set_message_scheduled_job_id(message_id=1, scheduled_job_id=1)
    with s.connect() as c:
        row = c.execute("SELECT scheduled_job_id FROM messages WHERE id=1").fetchone()
    assert row["scheduled_job_id"] == 1


def test_stamp_unknown_message_is_noop(tmp_path):
    s = _seed(tmp_path)
    s.set_message_scheduled_job_id(message_id=999, scheduled_job_id=1)  # must not raise


def test_stamp_with_unknown_job_id_raises_fk(tmp_path):
    import sqlite3
    s = _seed(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        s.set_message_scheduled_job_id(message_id=1, scheduled_job_id=99999)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_storage_stamp_scheduled_job_id.py -v
```
Expected: FAIL — `set_message_scheduled_job_id` does not exist.

- [ ] **Step 3: Implement the helper in `Storage`**

Add inside `Storage` (near the other `messages`-related helpers — search for a method like `append_message` or similar):

```python
def set_message_scheduled_job_id(self, *, message_id: int, scheduled_job_id: int) -> None:
    """Stamp a scheduled_job_id onto an existing message row. No-op if the
    message row doesn't exist; raises sqlite3.IntegrityError if the
    scheduled_job_id is unknown (FK violation).
    """
    with self.connect() as c:
        c.execute(
            "UPDATE messages SET scheduled_job_id=? WHERE id=?",
            (scheduled_job_id, message_id),
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_storage_stamp_scheduled_job_id.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_storage_stamp_scheduled_job_id.py
git commit -m "feat(kc-supervisor): Storage.set_message_scheduled_job_id helper"
```

---

## Task 3: Soft-cancel — switch `_do_cancel` from delete to status update

`_do_cancel` currently calls `storage.delete_scheduled_job(id)`. Phase 3's "cancelled" filter needs the row to persist. Switch to `storage.update_scheduled_job_status(id, "cancelled")`.

This is a behavior change for the existing `cancel_reminder` chat tool path. It is intentional and additive: the user-visible flow (chat says "cancelled") is unchanged, but the row remains visible in the dashboard with `status='cancelled'`.

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py` — `_do_cancel`
- Modify: `kc-supervisor/tests/test_scheduling_service.py` — update existing assertions
- Test: `kc-supervisor/tests/test_scheduling_service.py::test_cancel_soft_deletes` (new)

- [ ] **Step 1: Read the existing test file to see what assertions to update**

```bash
grep -n "cancel\|delete_scheduled_job" kc-supervisor/tests/test_scheduling_service.py
```
Expected: identifies tests that assert post-cancel rows are gone. Note the line numbers.

- [ ] **Step 2: Write the new failing test**

Append to `kc-supervisor/tests/test_scheduling_service.py`:

```python
def test_cancel_soft_deletes_marking_status_cancelled(tmp_path):
    # Use the same fixture/setup as other tests in this file (mirror the closest existing test).
    svc, storage = _make_service(tmp_path)  # whatever helper this file already uses
    res = svc.schedule_one_shot(
        when="in 1 hour", content="ping", conversation_id=1,
        channel="dashboard", chat_id="c1", agent="kona",
    )
    job_id = res["id"]
    svc.cancel_reminder(str(job_id), conversation_id=1, scope="user")

    row = storage.get_scheduled_job(job_id)
    assert row is not None, "row must persist after cancel"
    assert row["status"] == "cancelled"
```

If `_make_service` doesn't exist, copy the smallest setup boilerplate from the top of an existing test in the file. Don't invent fixtures.

- [ ] **Step 3: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py::test_cancel_soft_deletes_marking_status_cancelled -v
```
Expected: FAIL — `get_scheduled_job` returns None because cancel currently deletes.

- [ ] **Step 4: Update `_do_cancel`**

Replace the body of `_do_cancel` in `scheduling/service.py` (line ~280-289) with:

```python
def _do_cancel(self, rows: list[dict]) -> dict:
    cancelled: list[dict] = []
    for r in rows:
        try:
            self._scheduler.remove_job(str(r["id"]))
        except Exception:
            logger.debug("APS job %s not found; updating DB row anyway", r["id"])
        self.storage.update_scheduled_job_status(r["id"], "cancelled")
        cancelled.append({"id": r["id"], "content": r["payload"]})
    return {"ambiguous": False, "candidates": [], "cancelled": cancelled}
```

(Single line change: `delete_scheduled_job` → `update_scheduled_job_status(..., "cancelled")`. Log message updated for accuracy.)

- [ ] **Step 5: Update any existing tests that assert post-cancel deletion**

Grep:

```bash
grep -n "delete_scheduled_job\|cancel.*None\|cancel.*assert" kc-supervisor/tests/test_scheduling_service.py kc-supervisor/tests/test_scheduling_tools.py
```

For each test that asserts the row is gone after cancel (e.g., `assert storage.get_scheduled_job(id) is None`), change to:

```python
row = storage.get_scheduled_job(id)
assert row is not None
assert row["status"] == "cancelled"
```

Also verify the `list_reminders(active_only=True)` semantics — those tests already filter by `status='pending'`, so they should continue to pass without changes.

- [ ] **Step 6: Run the affected suites**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py tests/test_scheduling_tools.py tests/test_scheduling_storage.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py kc-supervisor/tests/test_scheduling_tools.py
git commit -m "feat(kc-supervisor): cancel sets status='cancelled' instead of deleting (enables Phase 3 cancelled filter)"
```

---

## Task 4: `RemindersBroadcaster` — pub/sub for lifecycle events

Mirrors `ApprovalBroker.subscribe` shape exactly. Synchronous `publish` notifies subscribers with `(event_type, reminder_row_dict)`. Subscribers are expected to enqueue and return immediately; misbehaving subscribers are logged and swallowed.

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/reminders_broadcaster.py`
- Test: `kc-supervisor/tests/test_reminders_broadcaster.py`

- [ ] **Step 1: Write the failing test**

```python
# kc-supervisor/tests/test_reminders_broadcaster.py
from __future__ import annotations
from kc_supervisor.reminders_broadcaster import RemindersBroadcaster


def test_subscribe_receives_published_events():
    b = RemindersBroadcaster()
    received: list[tuple[str, dict]] = []
    sub = b.subscribe(lambda et, row: received.append((et, row)))

    row = {"id": 1, "kind": "reminder", "status": "pending"}
    b.publish("reminder.created", row)
    b.publish("reminder.cancelled", row)

    assert received == [("reminder.created", row), ("reminder.cancelled", row)]
    sub.unsubscribe()


def test_unsubscribe_stops_delivery():
    b = RemindersBroadcaster()
    received: list = []
    sub = b.subscribe(lambda et, row: received.append((et, row)))
    sub.unsubscribe()
    b.publish("reminder.created", {"id": 1})
    assert received == []


def test_misbehaving_subscriber_does_not_break_others(caplog):
    b = RemindersBroadcaster()
    received: list = []
    b.subscribe(lambda et, row: (_ for _ in ()).throw(RuntimeError("boom")))
    b.subscribe(lambda et, row: received.append(et))
    b.publish("reminder.fired", {"id": 1})
    assert received == ["reminder.fired"]


def test_publish_is_safe_with_no_subscribers():
    b = RemindersBroadcaster()
    b.publish("reminder.created", {"id": 1})  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_reminders_broadcaster.py -v
```
Expected: FAIL — module not importable.

- [ ] **Step 3: Implement the broadcaster**

```python
# kc-supervisor/src/kc_supervisor/reminders_broadcaster.py
from __future__ import annotations
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventType = str  # one of "reminder.created" | "cancelled" | "snoozed" | "fired" | "failed"
ReminderRow = dict[str, Any]
SubscriberCallback = Callable[[EventType, ReminderRow], None]


class _Subscription:
    def __init__(self, broker: "RemindersBroadcaster", callback: SubscriberCallback) -> None:
        self._broker = broker
        self.callback = callback

    def unsubscribe(self) -> None:
        self._broker._subs.discard(self)


class RemindersBroadcaster:
    """Synchronous pub/sub for reminder lifecycle events.

    Mirrors ApprovalBroker.subscribe semantics. Subscribers register a callback
    that takes (event_type, reminder_row_dict). publish() fans out to all
    current subscribers; misbehaving callbacks are logged and swallowed so a
    single bad subscriber can't block the publisher.

    Producers (ScheduleService, ReminderRunner) MUST call publish() *after*
    their DB transaction commits. Pre-commit publishing risks broadcasting a
    state that rolls back.
    """

    def __init__(self) -> None:
        self._subs: set[_Subscription] = set()

    def subscribe(self, callback: SubscriberCallback) -> _Subscription:
        sub = _Subscription(broker=self, callback=callback)
        self._subs.add(sub)
        return sub

    def publish(self, event_type: EventType, reminder_row: ReminderRow) -> None:
        for sub in list(self._subs):
            try:
                sub.callback(event_type, reminder_row)
            except Exception:
                logger.exception("reminders subscriber raised; ignoring")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_reminders_broadcaster.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/reminders_broadcaster.py kc-supervisor/tests/test_reminders_broadcaster.py
git commit -m "feat(kc-supervisor): RemindersBroadcaster pub/sub for reminder lifecycle events"
```

---

## Task 5: Wire `RemindersBroadcaster` into `Deps` and `main.py`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/service.py` — add field to `Deps`
- Modify: `kc-supervisor/src/kc_supervisor/main.py` — construct and inject

- [ ] **Step 1: Add the field to `Deps`**

In `service.py`, add to the `Deps` dataclass alongside `schedule_service`:

```python
# service.py — at top of file
from kc_supervisor.reminders_broadcaster import RemindersBroadcaster

# Inside @dataclass class Deps, near schedule_service:
reminders_broadcaster: Optional[RemindersBroadcaster] = None
```

- [ ] **Step 2: Construct in `main.py` and inject**

In `main.py`, find where `ScheduleService` is constructed (around line 343 — `deps.schedule_service = ScheduleService(...)`). Just before it:

```python
# main.py — before ScheduleService construction
from kc_supervisor.reminders_broadcaster import RemindersBroadcaster

deps.reminders_broadcaster = RemindersBroadcaster()
```

We won't pass the broadcaster into `ScheduleService` or `ReminderRunner` constructors yet — those constructor changes happen in Tasks 6, 8, 9 alongside the `publish` calls. This task just makes the broadcaster reachable via `app.state.deps.reminders_broadcaster`.

- [ ] **Step 3: Run the supervisor test suite to confirm nothing breaks**

```bash
cd kc-supervisor && uv run pytest -q
```
Expected: PASS (no behavior change yet).

- [ ] **Step 4: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/service.py kc-supervisor/src/kc_supervisor/main.py
git commit -m "chore(kc-supervisor): plumb RemindersBroadcaster into Deps"
```

---

## Task 6: `ScheduleService.list_all_reminders` — global, filtered, sorted

Adds a new method (does NOT modify existing `list_reminders`). Returns the **full** row plus a server-computed `next_fire_at` (epoch seconds).

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Test: `kc-supervisor/tests/test_scheduling_service.py::test_list_all_reminders_*`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduling_service.py`:

```python
def test_list_all_reminders_returns_full_rows_with_next_fire_at(tmp_path):
    svc, storage = _make_service(tmp_path)
    one = svc.schedule_one_shot(
        when="in 1 hour", content="A", conversation_id=1,
        channel="dashboard", chat_id="c1", agent="kona",
    )
    cron = svc.schedule_cron(
        cron="0 9 * * *", content="B", conversation_id=1,
        channel="dashboard", chat_id="c1", agent="kona",
    )
    out = svc.list_all_reminders()
    ids = [r["id"] for r in out["reminders"]]
    assert one["id"] in ids and cron["id"] in ids
    for r in out["reminders"]:
        # full row keys
        assert {"id","kind","payload","status","channel","chat_id",
                "when_utc","cron_spec","attempts","last_fired_at",
                "created_at","mode","agent","conversation_id"} <= set(r.keys())
        # server-computed fire time
        assert "next_fire_at" in r
        if r["kind"] == "reminder":
            assert r["next_fire_at"] == r["when_utc"]
        else:
            assert isinstance(r["next_fire_at"], (int, float))


def test_list_all_reminders_filters_by_status(tmp_path):
    svc, storage = _make_service(tmp_path)
    a = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                              channel="dashboard", chat_id="c1", agent="kona")
    svc.cancel_reminder(str(a["id"]), conversation_id=1, scope="user")
    pending = svc.list_all_reminders(statuses=["pending"])
    cancelled = svc.list_all_reminders(statuses=["cancelled"])
    assert all(r["status"] == "pending" for r in pending["reminders"])
    assert all(r["status"] == "cancelled" for r in cancelled["reminders"])
    assert a["id"] in [r["id"] for r in cancelled["reminders"]]


def test_list_all_reminders_filters_by_kind_and_channel(tmp_path):
    svc, _ = _make_service(tmp_path)
    one = svc.schedule_one_shot(when="in 1 hour", content="o", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    svc.schedule_cron(cron="0 9 * * *", content="c", conversation_id=1,
                      channel="dashboard", chat_id="c1", agent="kona")
    only_oneshot = svc.list_all_reminders(kinds=["reminder"])
    assert all(r["kind"] == "reminder" for r in only_oneshot["reminders"])
    only_cron = svc.list_all_reminders(kinds=["cron"])
    assert all(r["kind"] == "cron" for r in only_cron["reminders"])


def test_list_all_reminders_sort_order(tmp_path):
    svc, _ = _make_service(tmp_path)
    far = svc.schedule_one_shot(when="in 3 hours", content="far", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    near = svc.schedule_one_shot(when="in 30 minutes", content="near", conversation_id=1,
                                 channel="dashboard", chat_id="c1", agent="kona")
    rows = svc.list_all_reminders(statuses=["pending"])["reminders"]
    ids = [r["id"] for r in rows]
    assert ids.index(near["id"]) < ids.index(far["id"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py::test_list_all_reminders_returns_full_rows_with_next_fire_at -v
```
Expected: FAIL — `list_all_reminders` doesn't exist.

- [ ] **Step 3: Implement `list_all_reminders` and a `Storage.list_scheduled_jobs_filtered` if needed**

First, check what the existing `Storage.list_scheduled_jobs` accepts (around storage.py:398). It currently takes `conversation_id` and `statuses`. Add a `kinds` and `channels` filter via an additional method or by extending. Simplest: extend the existing method since it's the only caller surface for this kind of query.

In `storage.py`, replace the existing `list_scheduled_jobs` body with a version that accepts the extra filters (keep param names backwards-compatible — current callers only pass `conversation_id` and `statuses`):

```python
def list_scheduled_jobs(
    self,
    *,
    conversation_id: Optional[int] = None,
    statuses: Optional[tuple[str, ...]] = None,
    kinds: Optional[tuple[str, ...]] = None,
    channels: Optional[tuple[str, ...]] = None,
) -> list[dict]:
    clauses, params = [], []
    if conversation_id is not None:
        clauses.append("conversation_id=?"); params.append(conversation_id)
    if statuses is not None:
        clauses.append(f"status IN ({','.join('?'*len(statuses))})"); params.extend(statuses)
    if kinds is not None:
        clauses.append(f"kind IN ({','.join('?'*len(kinds))})"); params.extend(kinds)
    if channels is not None:
        clauses.append(f"channel IN ({','.join('?'*len(channels))})"); params.extend(channels)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM scheduled_jobs {where} ORDER BY id ASC"
    with self.connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
```

Then in `scheduling/service.py`, add (below the existing `list_reminders`):

```python
def list_all_reminders(
    self,
    *,
    statuses: Optional[list[str]] = None,
    kinds: Optional[list[str]] = None,
    channels: Optional[list[str]] = None,
) -> dict:
    """Global list (all conversations). Returns full rows plus next_fire_at.

    `next_fire_at` is `when_utc` for one-shots, the next APS trigger time
    (epoch seconds) for crons, or None if no future fire time can be
    computed (e.g., a misconfigured cron). Sort: next_fire_at ASC NULLS
    LAST, then created_at DESC.
    """
    rows = self.storage.list_scheduled_jobs(
        statuses=tuple(statuses) if statuses else None,
        kinds=tuple(kinds) if kinds else None,
        channels=tuple(channels) if channels else None,
    )
    enriched = [self._enrich_row(r) for r in rows]
    enriched.sort(key=lambda r: (
        r["next_fire_at"] is None,                     # NULLS LAST
        r["next_fire_at"] if r["next_fire_at"] else 0,
        -(r["created_at"] or 0),                       # created_at DESC tiebreak
    ))
    return {"reminders": enriched}


def _enrich_row(self, row: dict) -> dict:
    """Add server-computed next_fire_at to a scheduled_jobs row."""
    nfa: Optional[float]
    if row["kind"] == "reminder":
        nfa = row["when_utc"]
    elif row["kind"] == "cron" and row["cron_spec"]:
        try:
            trigger = CronTrigger.from_crontab(row["cron_spec"], timezone=self._tz)
            nxt = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
            nfa = nxt.timestamp() if nxt is not None else None
        except Exception:
            nfa = None
    else:
        nfa = None
    return {**row, "next_fire_at": nfa}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v -k list_all_reminders
```
Expected: PASS for all four tests.

- [ ] **Step 5: Run the broader suite to catch regressions**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py tests/test_scheduling_service.py tests/test_scheduling_tools.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService.list_all_reminders + kind/channel filters"
```

---

## Task 7: `ScheduleService.snooze_reminder` — atomic row update + APS modify

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Modify: `kc-supervisor/src/kc_supervisor/storage.py` — add a `update_scheduled_job_when` helper if not present
- Test: `kc-supervisor/tests/test_scheduling_service.py::test_snooze_*`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduling_service.py`:

```python
def test_snooze_reschedules_pending_oneshot(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    res = svc.schedule_one_shot(
        when="in 1 hour", content="x", conversation_id=1,
        channel="dashboard", chat_id="c1", agent="kona",
    )
    new_when = time.time() + 3600 * 3  # 3 hours from now
    out = svc.snooze_reminder(reminder_id=res["id"], when_utc=new_when)
    assert out["id"] == res["id"]
    row = storage.get_scheduled_job(res["id"])
    assert abs(row["when_utc"] - new_when) < 1.0
    # APS job rescheduled to same job id
    aps = svc._scheduler.get_job(str(res["id"]))
    assert aps is not None
    assert abs(aps.next_run_time.timestamp() - new_when) < 1.0


def test_snooze_rejects_non_pending(tmp_path):
    import time
    svc, _ = _make_service(tmp_path)
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    svc.cancel_reminder(str(res["id"]), conversation_id=1, scope="user")
    with pytest.raises(ValueError, match="already_fired|cancelled|not pending"):
        svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600)


def test_snooze_rejects_cron(tmp_path):
    import time
    svc, _ = _make_service(tmp_path)
    res = svc.schedule_cron(cron="0 9 * * *", content="x", conversation_id=1,
                            channel="dashboard", chat_id="c1", agent="kona")
    with pytest.raises(ValueError, match="cron_not_snoozable"):
        svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600)


def test_snooze_rejects_past_time(tmp_path):
    import time
    svc, _ = _make_service(tmp_path)
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    with pytest.raises(ValueError, match="past"):
        svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() - 60)


def test_snooze_unknown_id_raises(tmp_path):
    import time
    svc, _ = _make_service(tmp_path)
    with pytest.raises(LookupError):
        svc.snooze_reminder(reminder_id=999999, when_utc=time.time() + 3600)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v -k snooze
```
Expected: FAIL — `snooze_reminder` not found.

- [ ] **Step 3: Add a storage helper for the `when_utc` update**

In `storage.py`, alongside the other scheduled_job updaters:

```python
def update_scheduled_job_when(self, job_id: int, when_utc: float) -> None:
    with self.connect() as c:
        c.execute(
            "UPDATE scheduled_jobs SET when_utc=? WHERE id=?",
            (when_utc, job_id),
        )
```

- [ ] **Step 4: Implement `snooze_reminder`**

In `scheduling/service.py`, add (below `cancel_reminder`):

```python
def snooze_reminder(self, *, reminder_id: int, when_utc: float) -> dict:
    """Reschedule a pending one-shot to a new fire time. Atomically updates
    the DB row + APS trigger; on APS failure, the DB write rolls back via
    SQLite transaction (APS's SQLAlchemyJobStore writes to the same DB).

    Raises:
      LookupError — id not found
      ValueError("cron_not_snoozable") — row is a cron
      ValueError("past_when_utc") — target time is not in the future
      ValueError("not pending: <status>") — row is no longer pending
    """
    import time
    row = self.storage.get_scheduled_job(reminder_id)
    if row is None:
        raise LookupError(f"reminder {reminder_id} not found")
    if row["kind"] != "reminder":
        raise ValueError(f"cron_not_snoozable: id={reminder_id}")
    if row["status"] != "pending":
        raise ValueError(f"not pending: {row['status']}")
    if when_utc <= time.time():
        raise ValueError(f"past_when_utc: {when_utc}")

    # Update the human-readable mirror first; if APS modify_job raises we
    # need to roll back. SQLite's autocommit-on-success / rollback-on-error
    # is per-statement here, so handle manually.
    self.storage.update_scheduled_job_when(reminder_id, when_utc)
    try:
        new_run_dt = datetime.fromtimestamp(when_utc, tz=_tz_mod.utc)
        self._scheduler.reschedule_job(
            str(reminder_id),
            trigger=DateTrigger(run_date=new_run_dt),
        )
    except Exception:
        # Restore the prior when_utc to keep DB and APS consistent.
        self.storage.update_scheduled_job_when(reminder_id, row["when_utc"])
        raise

    return {"id": reminder_id, "when_utc": when_utc}
```

(SQLite's per-connection transactional behavior in `Storage.connect()` uses `isolation_level=None` — see storage.py:138 — so we can't rely on a Python-level transaction wrapper here. The compensating-write approach above is the simplest correct path and is what the spec's "atomic" guarantee actually requires.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v -k snooze
```
Expected: PASS for all five tests.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService.snooze_reminder for one-shot rescheduling"
```

---

## Task 8: Wire broadcaster publishes into `ScheduleService`

`ScheduleService` needs to emit `reminder.created`, `reminder.cancelled`, `reminder.snoozed` after each successful operation.

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py` — add `broadcaster` constructor arg + publish calls
- Modify: `kc-supervisor/src/kc_supervisor/main.py` — pass broadcaster into `ScheduleService(...)`
- Test: `kc-supervisor/tests/test_scheduling_service.py::test_publishes_*`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduling_service.py`:

```python
def _make_service_with_broadcaster(tmp_path):
    """Same as _make_service but also returns the broadcaster so tests can subscribe."""
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    svc, storage = _make_service(tmp_path, broadcaster=b)  # extend the helper to accept this
    return svc, storage, b


def test_publishes_reminder_created(tmp_path):
    svc, _, b = _make_service_with_broadcaster(tmp_path)
    events = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    assert ("reminder.created", res["id"]) in events


def test_publishes_reminder_cancelled(tmp_path):
    svc, _, b = _make_service_with_broadcaster(tmp_path)
    events = []
    b.subscribe(lambda et, row: events.append((et, row["id"], row.get("status"))))
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    svc.cancel_reminder(str(res["id"]), conversation_id=1, scope="user")
    cancelled_events = [e for e in events if e[0] == "reminder.cancelled"]
    assert cancelled_events
    assert cancelled_events[-1][2] == "cancelled"


def test_publishes_reminder_snoozed(tmp_path):
    import time
    svc, _, b = _make_service_with_broadcaster(tmp_path)
    events = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="kona")
    svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600 * 3)
    assert ("reminder.snoozed", res["id"]) in events
```

Update `_make_service` (the existing helper near the top of the test file) to accept an optional `broadcaster` kwarg and pass it through. If the helper is currently:

```python
def _make_service(tmp_path):
    storage = Storage(tmp_path / "t.db"); storage.init()
    runner = _FakeRunner()
    svc = ScheduleService(storage=storage, runner=runner, db_path=tmp_path/"t.db", timezone="UTC")
    svc.start()
    return svc, storage
```

extend it to:

```python
def _make_service(tmp_path, broadcaster=None):
    storage = Storage(tmp_path / "t.db"); storage.init()
    runner = _FakeRunner()
    svc = ScheduleService(
        storage=storage, runner=runner, db_path=tmp_path/"t.db",
        timezone="UTC", broadcaster=broadcaster,
    )
    svc.start()
    return svc, storage
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v -k publishes
```
Expected: FAIL — `ScheduleService.__init__` doesn't accept `broadcaster`.

- [ ] **Step 3: Add the constructor arg + publish calls**

In `scheduling/service.py`, modify `__init__`:

```python
def __init__(
    self,
    storage: Storage,
    runner: _RunnerLike,
    db_path: Path,
    timezone: str,
    broadcaster: Optional["RemindersBroadcaster"] = None,
) -> None:
    self.storage = storage
    self.runner = runner
    self._tz = timezone
    self._broadcaster = broadcaster  # NEW
    # ... (rest unchanged)
```

(Add `from kc_supervisor.reminders_broadcaster import RemindersBroadcaster` at the top, gated under `TYPE_CHECKING` if you want to avoid the runtime import; safe either way.)

Add a private helper for publish:

```python
def _publish(self, event_type: str, reminder_id: int) -> None:
    if self._broadcaster is None:
        return
    row = self.storage.get_scheduled_job(reminder_id)
    if row is None:
        return  # raced with deletion (shouldn't happen post-Phase-3 since we soft-cancel)
    enriched = self._enrich_row(row)
    self._broadcaster.publish(event_type, enriched)
```

Then add publish calls:
- Inside `schedule_one_shot`, after the successful `add_job` block, before `return`:
  `self._publish("reminder.created", job_id)`
- Inside `schedule_cron`, same place:
  `self._publish("reminder.created", job_id)`
- Inside `_do_cancel`, after the loop:
  ```python
  for c in cancelled:
      self._publish("reminder.cancelled", c["id"])
  ```
- Inside `snooze_reminder`, after the successful APS reschedule:
  `self._publish("reminder.snoozed", reminder_id)`

- [ ] **Step 4: Update `main.py` to pass the broadcaster**

```python
# main.py — at the ScheduleService construction site
deps.schedule_service = ScheduleService(
    storage=deps.storage,
    runner=runner,
    db_path=db_path,
    timezone=tz,
    broadcaster=deps.reminders_broadcaster,  # NEW
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v -k publishes
```
Expected: PASS for all three tests.

- [ ] **Step 6: Run the broader suite**

```bash
cd kc-supervisor && uv run pytest -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/src/kc_supervisor/main.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService publishes lifecycle events to RemindersBroadcaster"
```

---

## Task 9: `ReminderRunner` populates `messages.scheduled_job_id` + emits fired/failed

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/runner.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py` — pass broadcaster into `ReminderRunner(...)`
- Modify: `kc-supervisor/src/kc_supervisor/conversations.py` — `append` returns the new message id (read first; only modify if it doesn't already)
- Test: `kc-supervisor/tests/test_reminder_runner.py`

- [ ] **Step 1: Inspect `ConversationManager.append` to see whether it returns the message id**

```bash
grep -n "def append" kc-supervisor/src/kc_supervisor/conversations.py
```

If it returns the new message id, skip step 2. If it returns `None`, add a return value (one-line change to return the inserted row id from the underlying `Storage.append_message`).

- [ ] **Step 2: If needed, make `ConversationManager.append` return the message id**

Update the method's signature/return and any call sites (most ignore the return value already). Run the suite to confirm nothing breaks:

```bash
cd kc-supervisor && uv run pytest -q
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_reminder_runner.py`:

```python
def test_fire_stamps_scheduled_job_id_on_assistant_message(tmp_path):
    runner, storage, conv_mgr, _ = _make_runner(tmp_path)  # 4-tuple: (runner, storage, conv_mgr_mock, connector_registry_mock)
    # Schedule a fake row directly
    job_id = storage.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=1,
        channel="dashboard", chat_id="c1", payload="ping",
        when_utc=0, cron_spec=None, mode="literal",
    )
    runner.fire(job_id)
    # The most recent message should have scheduled_job_id == job_id
    with storage.connect() as c:
        row = c.execute(
            "SELECT id, scheduled_job_id FROM messages "
            "WHERE conversation_id=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["scheduled_job_id"] == job_id


def test_fire_publishes_reminder_fired(tmp_path):
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    events = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    runner, storage, _, _ = _make_runner(tmp_path, broadcaster=b)
    job_id = storage.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=1,
        channel="dashboard", chat_id="c1", payload="ping",
        when_utc=0, cron_spec=None, mode="literal",
    )
    runner.fire(job_id)
    assert ("reminder.fired", job_id) in events


def test_fire_failed_publishes_failed_event(tmp_path):
    """Force a connector failure by using a channel for which no connector is registered."""
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    events = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    runner, storage, _, _ = _make_runner(tmp_path, broadcaster=b, with_connector=False)
    job_id = storage.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=1,
        channel="telegram", chat_id="c1", payload="ping",  # telegram not registered
        when_utc=0, cron_spec=None, mode="literal",
    )
    runner.fire(job_id)
    assert ("reminder.failed", job_id) in events
```

Update the existing `_make_runner` helper to accept `broadcaster` (default None) and `with_connector` (default True), passing them through.

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -v -k "stamps_scheduled_job_id or publishes_reminder_fired or fire_failed"
```
Expected: FAIL.

- [ ] **Step 5: Implement**

In `scheduling/runner.py`:

```python
# Constructor — add broadcaster
def __init__(
    self,
    *,
    storage: Storage,
    conversations: Any,
    connector_registry: Any,
    coroutine_runner: CoroRunner,
    agent_registry: Optional[Any] = None,
    broadcaster: Optional["RemindersBroadcaster"] = None,  # NEW
) -> None:
    self.storage = storage
    self.conversations = conversations
    self.connector_registry = connector_registry
    self._run_coro = coroutine_runner
    self.agent_registry = agent_registry
    self._broadcaster = broadcaster  # NEW
```

(Add the import — under TYPE_CHECKING is fine.)

Add a small helper:

```python
def _publish(self, event_type: str, job_id: int) -> None:
    if self._broadcaster is None:
        return
    row = self.storage.get_scheduled_job(job_id)
    if row is None:
        return
    self._broadcaster.publish(event_type, dict(row))
```

In `fire()`, after each `self.conversations.append(dest_conv_id, AssistantMessage(...))` call, capture the returned message id and stamp it:

```python
msg_id = self.conversations.append(dest_conv_id, AssistantMessage(content=text))
if isinstance(msg_id, int):
    self.storage.set_message_scheduled_job_id(message_id=msg_id, scheduled_job_id=job_id)
```

(Apply this to BOTH branches — the `dashboard` branch and the connector branch.)

After `update_scheduled_job_after_fire(... new_status="done"|"pending")`, publish:

```python
self._publish("reminder.fired", job_id)
```

For the failure paths (each `update_scheduled_job_after_fire(... new_status="failed")`), publish:

```python
self._publish("reminder.failed", job_id)
return
```

- [ ] **Step 6: Update `main.py` to pass the broadcaster into `ReminderRunner`**

```python
# main.py — at ReminderRunner construction (around line 333)
runner = ReminderRunner(
    storage=deps.storage,
    conversations=deps.conversations,
    connector_registry=deps.connector_registry,
    coroutine_runner=_run_coro,
    agent_registry=deps.registry,
    broadcaster=deps.reminders_broadcaster,  # NEW
)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -v
```
Expected: PASS for all (including pre-existing tests).

- [ ] **Step 8: Run the full supervisor suite**

```bash
cd kc-supervisor && uv run pytest -q
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/src/kc_supervisor/conversations.py kc-supervisor/src/kc_supervisor/main.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): ReminderRunner stamps messages.scheduled_job_id and emits fired/failed events"
```

---

## Task 10: HTTP `GET /reminders`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Test: `kc-supervisor/tests/test_http_reminders.py` (new)

- [ ] **Step 1: Extend the shared conftest with a deps-with-scheduler fixture**

The existing `deps` fixture in `kc-supervisor/tests/conftest.py` does NOT construct a `ScheduleService` (it's `None` in the dataclass default). Add a sibling fixture that mounts one. Append to `conftest.py`:

```python
@pytest.fixture
def deps_with_scheduler(deps, tmp_path):
    """Extends `deps` with a real ScheduleService + RemindersBroadcaster + a fake
    runner. Tests that exercise /reminders endpoints request this fixture."""
    from kc_supervisor.scheduling.service import ScheduleService
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    from unittest.mock import MagicMock

    # Seed at least one conversation so schedule_one_shot's FK satisfies.
    with deps.storage.connect() as c:
        c.execute("INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                  ("alice", "dashboard", 0))

    deps.reminders_broadcaster = RemindersBroadcaster()
    deps.schedule_service = ScheduleService(
        storage=deps.storage,
        runner=MagicMock(),
        db_path=tmp_path / "kc-home" / "data" / "kc.db",
        timezone="UTC",
        broadcaster=deps.reminders_broadcaster,
    )
    deps.schedule_service.start()
    yield deps
    deps.schedule_service.shutdown()


@pytest.fixture
def app_with_scheduler(deps_with_scheduler):
    return create_app(deps_with_scheduler)
```

- [ ] **Step 2: Write the failing test**

```python
# kc-supervisor/tests/test_http_reminders.py
from __future__ import annotations
from fastapi.testclient import TestClient


def test_get_reminders_returns_pending_and_cancelled(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    a = svc.schedule_one_shot(when="in 1 hour", content="A", conversation_id=1,
                              channel="dashboard", chat_id="c1", agent="alice")
    b = svc.schedule_one_shot(when="in 2 hours", content="B", conversation_id=1,
                              channel="dashboard", chat_id="c1", agent="alice")
    svc.cancel_reminder(str(b["id"]), conversation_id=1, scope="user")

    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders")
        assert r.status_code == 200
        body = r.json()
        assert "reminders" in body
        ids = {row["id"] for row in body["reminders"]}
        assert a["id"] in ids and b["id"] in ids


def test_get_reminders_filters_by_status_and_kind(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    one = svc.schedule_one_shot(when="in 1 hour", content="o", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    cron = svc.schedule_cron(cron="0 9 * * *", content="c", conversation_id=1,
                             channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders?kind=cron")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()["reminders"]]
        assert cron["id"] in ids and one["id"] not in ids

        r2 = client.get("/reminders?status=pending&kind=reminder")
        ids2 = [row["id"] for row in r2.json()["reminders"]]
        assert one["id"] in ids2 and cron["id"] not in ids2


def test_get_reminders_invalid_status_returns_422(app_with_scheduler):
    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders?status=bogus")
        assert r.status_code == 422
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v
```
Expected: FAIL — 404 from `/reminders`.

- [ ] **Step 4: Implement the route**

In `http_routes.py`, inside `register_http_routes(app)`, add:

```python
@app.get("/reminders")
def list_reminders_endpoint(
    status: Optional[list[str]] = Query(default=None),
    kind: Optional[list[str]] = Query(default=None),
    channel: Optional[list[str]] = Query(default=None),
):
    deps = app.state.deps
    svc = deps.schedule_service
    if svc is None:
        raise HTTPException(status_code=503, detail="schedule_service unavailable")

    ALLOWED_STATUSES = {"pending", "done", "cancelled", "failed", "missed"}
    ALLOWED_KINDS = {"reminder", "cron"}
    ALLOWED_CHANNELS = {"dashboard", "telegram", "imessage"}

    if status is not None:
        bad = [s for s in status if s not in ALLOWED_STATUSES]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid status: {bad}")
    if kind is not None:
        bad = [k for k in kind if k not in ALLOWED_KINDS]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid kind: {bad}")
    if channel is not None:
        bad = [c for c in channel if c not in ALLOWED_CHANNELS]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid channel: {bad}")

    return svc.list_all_reminders(statuses=status, kinds=kind, channels=channel)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v
```
Expected: PASS for the three tests.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/conftest.py kc-supervisor/tests/test_http_reminders.py
git commit -m "feat(kc-supervisor): GET /reminders endpoint + deps_with_scheduler test fixture"
```

---

## Task 11: HTTP `DELETE /reminders/{id}`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Test: `kc-supervisor/tests/test_http_reminders.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_http_reminders.py`:

```python
def test_delete_reminder_cancels_pending(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.delete(f"/reminders/{res['id']}")
        assert r.status_code == 204
    row = deps_with_scheduler.storage.get_scheduled_job(res["id"])
    assert row["status"] == "cancelled"


def test_delete_reminder_unknown_id_returns_404(app_with_scheduler):
    with TestClient(app_with_scheduler) as client:
        r = client.delete("/reminders/999999")
        assert r.status_code == 404


def test_delete_reminder_already_cancelled_returns_409(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        client.delete(f"/reminders/{res['id']}")
        r = client.delete(f"/reminders/{res['id']}")
        assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v -k delete
```
Expected: FAIL.

- [ ] **Step 3: Implement the route**

In `http_routes.py`:

```python
@app.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder_endpoint(reminder_id: int):
    deps = app.state.deps
    svc = deps.schedule_service
    if svc is None:
        raise HTTPException(status_code=503, detail="schedule_service unavailable")

    row = deps.storage.get_scheduled_job(reminder_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"reminder {reminder_id} not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"reminder is already in terminal state: {row['status']}",
        )
    # Use the existing path through cancel_reminder so APS + DB stay in sync.
    svc.cancel_reminder(str(reminder_id), conversation_id=row["conversation_id"], scope="user")
    return None  # 204
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v -k delete
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http_reminders.py
git commit -m "feat(kc-supervisor): DELETE /reminders/{id} endpoint"
```

---

## Task 12: HTTP `PATCH /reminders/{id}` (snooze)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Test: `kc-supervisor/tests/test_http_reminders.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_patch_reminder_snoozes(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    new_when = time.time() + 3600 * 3
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": new_when})
        assert r.status_code == 200
        body = r.json()
        assert abs(body["when_utc"] - new_when) < 1.0


def test_patch_reminder_cron_returns_409(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_cron(cron="0 9 * * *", content="x", conversation_id=1,
                            channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": time.time() + 3600})
        assert r.status_code == 409
        assert r.json().get("detail", {}).get("code") == "cron_not_snoozable"


def test_patch_reminder_past_time_returns_422(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": time.time() - 60})
        assert r.status_code == 422


def test_patch_reminder_unknown_returns_404(app_with_scheduler):
    import time
    with TestClient(app_with_scheduler) as client:
        r = client.patch("/reminders/999999", json={"when_utc": time.time() + 3600})
        assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v -k patch
```
Expected: FAIL.

- [ ] **Step 3: Implement the route**

```python
class SnoozeRequest(BaseModel):
    when_utc: float


@app.patch("/reminders/{reminder_id}")
def patch_reminder_endpoint(reminder_id: int, req: SnoozeRequest):
    deps = app.state.deps
    svc = deps.schedule_service
    if svc is None:
        raise HTTPException(status_code=503, detail="schedule_service unavailable")
    try:
        result = svc.snooze_reminder(reminder_id=reminder_id, when_utc=req.when_utc)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"reminder {reminder_id} not found")
    except ValueError as e:
        msg = str(e)
        if "cron_not_snoozable" in msg:
            raise HTTPException(status_code=409, detail={"code": "cron_not_snoozable"})
        if "not pending" in msg:
            raise HTTPException(status_code=409, detail={"code": "already_fired", "message": msg})
        if "past_when_utc" in msg:
            raise HTTPException(status_code=422, detail={"code": "past_when_utc"})
        raise
    row = deps.storage.get_scheduled_job(reminder_id)
    enriched = svc._enrich_row(dict(row))
    return enriched
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd kc-supervisor && uv run pytest tests/test_http_reminders.py -v
```
Expected: PASS for all reminders endpoint tests.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http_reminders.py
git commit -m "feat(kc-supervisor): PATCH /reminders/{id} snooze endpoint"
```

---

## Task 13: WebSocket `/ws/reminders` endpoint

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py`
- Test: `kc-supervisor/tests/test_ws_reminders.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# kc-supervisor/tests/test_ws_reminders.py
from __future__ import annotations
from fastapi.testclient import TestClient


def test_ws_reminders_pushes_lifecycle_events(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    with TestClient(app_with_scheduler) as client:
        with client.websocket_connect("/ws/reminders") as ws:
            res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                        channel="dashboard", chat_id="c1", agent="alice")
            msg = ws.receive_json()
            assert msg["type"] == "reminder.created"
            assert msg["reminder"]["id"] == res["id"]
            assert "ts" in msg


def test_ws_reminders_disconnect_cleans_up(app_with_scheduler, deps_with_scheduler):
    broadcaster = deps_with_scheduler.reminders_broadcaster
    with TestClient(app_with_scheduler) as client:
        with client.websocket_connect("/ws/reminders") as ws:
            pass  # connection closes on context exit
    assert len(broadcaster._subs) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-supervisor && uv run pytest tests/test_ws_reminders.py -v
```
Expected: FAIL — `/ws/reminders` returns 403/404.

- [ ] **Step 3: Implement the WS endpoint**

In `ws_routes.py`, inside `register_ws_routes(app)`, add (mirror the `/ws/approvals` shape):

```python
@app.websocket("/ws/reminders")
async def ws_reminders(ws: WebSocket):
    await ws.accept()
    deps = app.state.deps
    broadcaster = deps.reminders_broadcaster
    if broadcaster is None:
        await ws.send_json({"type": "error", "message": "broadcaster unavailable"})
        await ws.close()
        return

    import asyncio as _asyncio
    import time as _time
    loop = _asyncio.get_running_loop()

    async def _send(event_type: str, reminder_row: dict) -> None:
        try:
            await ws.send_json({
                "type": event_type,
                "reminder": reminder_row,
                "ts": int(_time.time()),
            })
        except Exception:
            logger.warning("ws_reminders failed to send %s", event_type, exc_info=True)

    sub = broadcaster.subscribe(
        lambda et, row: loop.call_soon_threadsafe(_asyncio.create_task, _send(et, row))
    )

    try:
        # Keep the connection open. We don't expect inbound messages, but a
        # blocking receive lets us notice client disconnects.
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        sub.unsubscribe()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-supervisor && uv run pytest tests/test_ws_reminders.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/ws_routes.py kc-supervisor/tests/test_ws_reminders.py
git commit -m "feat(kc-supervisor): /ws/reminders endpoint streams RemindersBroadcaster events"
```

---

## Task 14: Frontend API client `reminders.ts`

**Files:**
- Create: `kc-dashboard/src/api/reminders.ts`
- Test: `kc-dashboard/src/api/reminders.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// kc-dashboard/src/api/reminders.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { listReminders, cancelReminder, snoozeReminder } from "./reminders";

describe("reminders api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("listReminders builds query params", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ reminders: [] }), { status: 200 }) as Response,
    );
    await listReminders({ statuses: ["pending"], kinds: ["reminder"], channels: ["dashboard"] });
    const url = (fetchMock.mock.calls[0][0] as string);
    expect(url).toContain("/reminders?");
    expect(url).toContain("status=pending");
    expect(url).toContain("kind=reminder");
    expect(url).toContain("channel=dashboard");
  });

  it("cancelReminder sends DELETE", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response(null, { status: 204 }) as Response);
    await cancelReminder(42);
    expect(fetchMock.mock.calls[0][0]).toContain("/reminders/42");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });

  it("snoozeReminder sends PATCH with when_utc", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 42, when_utc: 1234 }), { status: 200 }) as Response,
    );
    await snoozeReminder(42, 1234);
    expect(fetchMock.mock.calls[0][0]).toContain("/reminders/42");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(JSON.stringify({ when_utc: 1234 }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/api/reminders.test.ts
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the client**

```typescript
// kc-dashboard/src/api/reminders.ts
import { apiGet, apiPatch, apiDelete } from "./client";

export type ReminderStatus = "pending" | "done" | "cancelled" | "failed" | "missed";
export type ReminderKind = "reminder" | "cron";
export type ReminderChannel = "dashboard" | "telegram" | "imessage";

export type Reminder = {
  id: number;
  kind: ReminderKind;
  agent: string;
  conversation_id: number;
  channel: ReminderChannel;
  chat_id: string;
  payload: string;
  when_utc: number | null;
  cron_spec: string | null;
  status: ReminderStatus;
  attempts: number;
  last_fired_at: number | null;
  created_at: number;
  mode: "literal" | "agent_phrased";
  next_fire_at: number | null;
};

export type ReminderFilters = {
  statuses?: ReminderStatus[];
  kinds?: ReminderKind[];
  channels?: ReminderChannel[];
};

function buildQuery(f: ReminderFilters): string {
  const p = new URLSearchParams();
  f.statuses?.forEach(s => p.append("status", s));
  f.kinds?.forEach(k => p.append("kind", k));
  f.channels?.forEach(c => p.append("channel", c));
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

export const listReminders = (filters: ReminderFilters = {}) =>
  apiGet<{ reminders: Reminder[] }>(`/reminders${buildQuery(filters)}`);

export const cancelReminder = (id: number) => apiDelete(`/reminders/${id}`);

export const snoozeReminder = (id: number, when_utc: number) =>
  apiPatch<Reminder>(`/reminders/${id}`, { when_utc });
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/api/reminders.test.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/api/reminders.ts kc-dashboard/src/api/reminders.test.ts
git commit -m "feat(kc-dashboard): reminders API client"
```

---

## Task 15: Frontend WS hook `useReminderEvents.ts`

The hook subscribes to `/ws/reminders` and invalidates the `["reminders", ...]` React Query cache on each event.

**Files:**
- Create: `kc-dashboard/src/ws/useReminderEvents.ts`
- Test: `kc-dashboard/src/ws/useReminderEvents.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// kc-dashboard/src/ws/useReminderEvents.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useReminderEvents } from "./useReminderEvents";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  close() { this.onclose?.(); }
  send() {}
}

describe("useReminderEvents", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket as any;
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it("invalidates reminders queries on a reminder.created event", async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    renderHook(() => useReminderEvents(), { wrapper });
    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({ type: "reminder.created", reminder: { id: 1 }, ts: 1 }),
      }));
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["reminders"] });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/ws/useReminderEvents.test.tsx
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the hook**

```typescript
// kc-dashboard/src/ws/useReminderEvents.ts
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getBaseUrl } from "../api/client";

const REMINDER_EVENT_TYPES = new Set([
  "reminder.created",
  "reminder.cancelled",
  "reminder.snoozed",
  "reminder.fired",
  "reminder.failed",
]);

export function useReminderEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const url = getBaseUrl().replace(/^http/, "ws") + "/ws/reminders";
    const ws = new WebSocket(url);
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (typeof ev?.type === "string" && REMINDER_EVENT_TYPES.has(ev.type)) {
          qc.invalidateQueries({ queryKey: ["reminders"] });
        }
      } catch {
        // ignore non-JSON or malformed payloads
      }
    };
    return () => ws.close();
  }, [qc]);
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/ws/useReminderEvents.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/ws/useReminderEvents.ts kc-dashboard/src/ws/useReminderEvents.test.tsx
git commit -m "feat(kc-dashboard): useReminderEvents WS hook invalidates reminders cache"
```

---

## Task 16: Register `/reminders` route + nav tab in `App.tsx`

**Files:**
- Modify: `kc-dashboard/src/App.tsx`
- Modify: `kc-dashboard/src/main.tsx` — wire the route

- [ ] **Step 1: Read the current router config**

```bash
grep -n "createBrowserRouter\|RouterProvider\|<Route" kc-dashboard/src/main.tsx kc-dashboard/src/App.tsx
```

- [ ] **Step 2: Add the tab entry to `App.tsx`**

In the `tabs` array (App.tsx:5):

```tsx
const tabs = [
  { to: "/chat",        label: "Chat",        num: "01" },
  { to: "/agents",      label: "Agents",      num: "02" },
  { to: "/connectors",  label: "Connectors",  num: "03" },
  { to: "/permissions", label: "Permissions", num: "04" },
  { to: "/audit",       label: "Audit",       num: "05" },
  { to: "/monitor",     label: "Monitor",     num: "06" },
  { to: "/reminders",   label: "Reminders",   num: "07" },  // NEW
];
```

- [ ] **Step 3: Add the route definition**

In `main.tsx` (or wherever routes are defined — read first), add:

```tsx
import Reminders from "./views/Reminders";

// inside the route children list:
{ path: "reminders", element: <Reminders /> },
```

- [ ] **Step 4: Add a placeholder Reminders view so the route resolves**

```tsx
// kc-dashboard/src/views/Reminders.tsx (placeholder; replaced in Task 17)
export default function Reminders() {
  return <div className="p-5">Reminders (under construction)</div>;
}
```

- [ ] **Step 5: Run the dashboard build to confirm no TypeScript errors**

```bash
cd kc-dashboard && npx tsc --noEmit
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add kc-dashboard/src/App.tsx kc-dashboard/src/main.tsx kc-dashboard/src/views/Reminders.tsx
git commit -m "feat(kc-dashboard): register /reminders route + nav tab (placeholder view)"
```

---

## Task 17: `Reminders.tsx` — tabs + filter chips + URL state + list rendering

This task replaces the placeholder with the real view. Subsequent tasks (18–20) layer in expand/cancel/snooze.

**Files:**
- Modify: `kc-dashboard/src/views/Reminders.tsx`
- Test: `kc-dashboard/src/views/Reminders.test.tsx`

- [ ] **Step 1: Write the failing test (skeleton)**

```tsx
// kc-dashboard/src/views/Reminders.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Reminders from "./Reminders";

vi.mock("../api/reminders", () => ({
  listReminders: vi.fn().mockResolvedValue({
    reminders: [
      { id: 1, kind: "reminder", payload: "stretch", channel: "telegram",
        status: "pending", agent: "kona", conversation_id: 1, chat_id: "c",
        when_utc: Date.now()/1000 + 600, cron_spec: null, attempts: 0,
        last_fired_at: null, created_at: Date.now()/1000, mode: "literal",
        next_fire_at: Date.now()/1000 + 600 },
      { id: 2, kind: "cron", payload: "standup", channel: "dashboard",
        status: "pending", agent: "kona", conversation_id: 1, chat_id: "c",
        when_utc: null, cron_spec: "0 9 * * *", attempts: 0,
        last_fired_at: null, created_at: Date.now()/1000, mode: "literal",
        next_fire_at: Date.now()/1000 + 9000 },
    ],
  }),
  cancelReminder: vi.fn(),
  snoozeReminder: vi.fn(),
}));
vi.mock("../ws/useReminderEvents", () => ({ useReminderEvents: () => {} }));

function renderView(initial = "/reminders") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Reminders />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Reminders view", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders rows from the API", async () => {
    renderView();
    await waitFor(() => expect(screen.getByText("stretch")).toBeInTheDocument());
    expect(screen.getByText("standup")).toBeInTheDocument();
  });

  it("filters to one-shots when the One-shot tab is clicked", async () => {
    renderView();
    await waitFor(() => screen.getByText("stretch"));
    fireEvent.click(screen.getByRole("tab", { name: /one-shot/i }));
    // The mocked listReminders is called again with kinds: ["reminder"]
    const { listReminders } = await import("../api/reminders");
    await waitFor(() => {
      const lastCall = (listReminders as any).mock.calls.at(-1)[0];
      expect(lastCall).toEqual(expect.objectContaining({ kinds: ["reminder"] }));
    });
  });

  it("clicking a status chip toggles it in the URL params", async () => {
    renderView();
    await waitFor(() => screen.getByText("stretch"));
    fireEvent.click(screen.getByRole("button", { name: /^pending$/i }));
    const { listReminders } = await import("../api/reminders");
    await waitFor(() => {
      const lastCall = (listReminders as any).mock.calls.at(-1)[0];
      expect(lastCall.statuses).toEqual(["pending"]);
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: FAIL — placeholder doesn't render rows or tabs.

- [ ] **Step 3: Replace `Reminders.tsx` with the real view**

```tsx
// kc-dashboard/src/views/Reminders.tsx
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  listReminders,
  type Reminder,
  type ReminderKind,
  type ReminderStatus,
  type ReminderChannel,
} from "../api/reminders";
import { useReminderEvents } from "../ws/useReminderEvents";

const ALL_STATUSES: ReminderStatus[] = ["pending", "done", "cancelled", "failed", "missed"];
const ALL_CHANNELS: ReminderChannel[] = ["dashboard", "telegram", "imessage"];
const CHANNEL_LABEL: Record<ReminderChannel, string> = {
  dashboard: "DASH", telegram: "TG", imessage: "IMSG",
};

type KindTab = "all" | "reminder" | "cron";

function parseKindTab(s: string | null): KindTab {
  return s === "reminder" || s === "cron" ? s : "all";
}

function formatNextFire(r: Reminder): string {
  if (r.next_fire_at == null) return "—";
  if (r.kind === "cron" && r.cron_spec) {
    // crude; the audit panel will show a friendly version
    return r.cron_spec;
  }
  const delta = r.next_fire_at - Date.now() / 1000;
  if (delta < 0) return "overdue";
  if (delta < 60) return `in ${Math.round(delta)}s`;
  if (delta < 3600) return `in ${Math.round(delta / 60)}m`;
  if (delta < 86400) return `in ${Math.round(delta / 3600)}h`;
  return `in ${Math.round(delta / 86400)}d`;
}

export default function Reminders() {
  useReminderEvents();
  const [params, setParams] = useSearchParams();
  const tab = parseKindTab(params.get("tab"));
  const statuses = (params.getAll("status") as ReminderStatus[]).filter(s => ALL_STATUSES.includes(s));
  const channels = (params.getAll("channel") as ReminderChannel[]).filter(c => ALL_CHANNELS.includes(c));

  const filters = {
    statuses: statuses.length ? statuses : undefined,
    channels: channels.length ? channels : undefined,
    kinds: tab === "all" ? undefined : ([tab] as ReminderKind[]),
  };

  const q = useQuery({
    queryKey: ["reminders", filters],
    queryFn: () => listReminders(filters),
    refetchInterval: 30_000,
  });

  const setTab = (next: KindTab) => {
    if (next === "all") params.delete("tab"); else params.set("tab", next);
    setParams(params, { replace: true });
  };
  const toggleStatus = (s: ReminderStatus) => {
    const cur = params.getAll("status");
    params.delete("status");
    const next = cur.includes(s) ? cur.filter(x => x !== s) : [...cur, s];
    next.forEach(v => params.append("status", v));
    setParams(params, { replace: true });
  };
  const toggleChannel = (c: ReminderChannel) => {
    const cur = params.getAll("channel");
    params.delete("channel");
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    next.forEach(v => params.append("channel", v));
    setParams(params, { replace: true });
  };

  const reminders = q.data?.reminders ?? [];

  return (
    <div className="p-5">
      <div role="tablist" className="flex border-b border-line mb-3">
        {(["all", "reminder", "cron"] as KindTab[]).map(t => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={"px-4 py-2 text-xs uppercase tracking-[0.12em] font-mono border-r border-line "
              + (tab === t ? "text-textStrong border-b-2 border-b-accent" : "text-muted hover:text-text")}
          >
            {t === "all" ? "All" : t === "reminder" ? "One-shot" : "Recurring"}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        <span className="text-xs uppercase tracking-[0.12em] text-muted2 self-center mr-1">Status</span>
        {ALL_STATUSES.map(s => (
          <button
            key={s}
            onClick={() => toggleStatus(s)}
            className={"px-3 py-1 text-xs border "
              + (statuses.includes(s) ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{s}</button>
        ))}
        <span className="text-xs uppercase tracking-[0.12em] text-muted2 self-center mx-2">Channel</span>
        {ALL_CHANNELS.map(c => (
          <button
            key={c}
            onClick={() => toggleChannel(c)}
            className={"px-3 py-1 text-xs border font-mono "
              + (channels.includes(c) ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{CHANNEL_LABEL[c]}</button>
        ))}
      </div>

      {q.isLoading && <div className="text-muted text-sm">Loading…</div>}
      {q.isError && <div className="text-bad text-sm">Failed to load reminders.</div>}
      {!q.isLoading && reminders.length === 0 && (
        <div className="text-muted text-sm py-8 text-center">No reminders match these filters.</div>
      )}

      <ul className="divide-y divide-line">
        {reminders.map(r => (
          <li key={r.id} className="flex items-center gap-3 py-2 font-mono text-xs">
            <span className="w-32 text-muted">{formatNextFire(r)}</span>
            <span className={"px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border "
              + (r.kind === "cron" ? "border-accent text-accent" : "border-line text-muted")}>
              {r.kind === "cron" ? "CRON" : "ONE-SHOT"}
            </span>
            <span className="flex-1 text-text truncate" title={r.payload}>{r.payload}</span>
            <span className="text-muted2">{CHANNEL_LABEL[r.channel]}</span>
            {r.status !== "pending" && (
              <span className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-line text-muted">
                {r.status}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: PASS for the three skeleton tests.

- [ ] **Step 5: Smoke-check the build**

```bash
cd kc-dashboard && npx tsc --noEmit && npm test -- --run
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add kc-dashboard/src/views/Reminders.tsx kc-dashboard/src/views/Reminders.test.tsx
git commit -m "feat(kc-dashboard): Reminders view with tabs, filter chips, and list"
```

---

## Task 18: Expand-on-click audit panel

**Files:**
- Modify: `kc-dashboard/src/views/Reminders.tsx`
- Modify: `kc-dashboard/src/views/Reminders.test.tsx`

- [ ] **Step 1: Add a failing test**

Append:

```tsx
it("clicking a row toggles the audit panel", async () => {
  renderView();
  await waitFor(() => screen.getByText("stretch"));
  expect(screen.queryByText(/Created at/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("stretch"));
  expect(screen.getByText(/Created at/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("stretch"));
  expect(screen.queryByText(/Created at/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx -t "audit panel"
```
Expected: FAIL.

- [ ] **Step 3: Add expand state + panel render**

In `Reminders.tsx`:

```tsx
// Inside Reminders():
const [expandedId, setExpandedId] = useState<number | null>(null);
```

Update the row rendering to wrap each `<li>` in a fragment-friendly block and append the expand panel when `expandedId === r.id`:

```tsx
{reminders.map(r => (
  <li key={r.id} className="font-mono text-xs">
    <div className="flex items-center gap-3 py-2 cursor-pointer"
         onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}>
      <span className="w-32 text-muted">{formatNextFire(r)}</span>
      <span className={"px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border "
        + (r.kind === "cron" ? "border-accent text-accent" : "border-line text-muted")}>
        {r.kind === "cron" ? "CRON" : "ONE-SHOT"}
      </span>
      <span className="flex-1 text-text truncate" title={r.payload}>{r.payload}</span>
      <span className="text-muted2">{CHANNEL_LABEL[r.channel]}</span>
      {r.status !== "pending" && (
        <span className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-line text-muted">
          {r.status}
        </span>
      )}
    </div>
    {expandedId === r.id && (
      <div className="bg-panel2 border-l-2 border-accent px-4 py-3 mb-2 text-muted text-[11px] space-y-1">
        <div>Created at <span className="text-text">{new Date(r.created_at * 1000).toLocaleString()}</span> by <span className="text-text">{r.agent}</span> via <span className="text-text">{r.channel}</span></div>
        {r.kind === "cron" && r.cron_spec && (
          <div>Cron <span className="text-text">{r.cron_spec}</span></div>
        )}
        <div>Attempts <span className="text-text">{r.attempts}</span>{r.last_fired_at && <> · last fired <span className="text-text">{new Date(r.last_fired_at * 1000).toLocaleString()}</span></>}</div>
      </div>
    )}
  </li>
))}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Reminders.tsx kc-dashboard/src/views/Reminders.test.tsx
git commit -m "feat(kc-dashboard): expand-on-click audit panel for reminder rows"
```

---

## Task 19: Inline cancel button + confirm

**Files:**
- Modify: `kc-dashboard/src/views/Reminders.tsx`
- Modify: `kc-dashboard/src/views/Reminders.test.tsx`

- [ ] **Step 1: Add a failing test**

Append:

```tsx
it("clicking cancel + confirm calls cancelReminder", async () => {
  const { cancelReminder } = await import("../api/reminders");
  renderView();
  await waitFor(() => screen.getByText("stretch"));
  fireEvent.click(screen.getAllByRole("button", { name: /cancel reminder/i })[0]);
  fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
  await waitFor(() => expect(cancelReminder).toHaveBeenCalledWith(1));
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx -t "cancelReminder"
```
Expected: FAIL.

- [ ] **Step 3: Add cancel UI + mutation**

Add at the top of `Reminders.tsx`:

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelReminder as cancelReminderApi } from "../api/reminders";
```

Inside the component:

```tsx
const qc = useQueryClient();
const [confirmingCancelId, setConfirmingCancelId] = useState<number | null>(null);
const cancelMut = useMutation({
  mutationFn: (id: number) => cancelReminderApi(id),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["reminders"] }),
});
```

Inside the row's flex div (right side, before the closing `</div>`), but keep the click on the parent for expand-toggle — stop propagation on the action buttons:

```tsx
{r.status === "pending" && confirmingCancelId !== r.id && (
  <button
    aria-label="cancel reminder"
    onClick={(e) => { e.stopPropagation(); setConfirmingCancelId(r.id); }}
    className="text-muted hover:text-bad px-2"
  >×</button>
)}
{confirmingCancelId === r.id && (
  <span className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
    <span className="text-muted">Cancel?</span>
    <button
      onClick={() => { cancelMut.mutate(r.id); setConfirmingCancelId(null); }}
      className="px-2 py-0.5 text-[10px] uppercase tracking-[0.1em] border border-bad text-bad"
    >Confirm</button>
    <button
      onClick={() => setConfirmingCancelId(null)}
      className="px-2 py-0.5 text-[10px] uppercase tracking-[0.1em] border border-line text-muted"
    >Keep</button>
  </span>
)}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Reminders.tsx kc-dashboard/src/views/Reminders.test.tsx
git commit -m "feat(kc-dashboard): inline cancel-with-confirm on reminder rows"
```

---

## Task 20: Snooze popover + PATCH

**Files:**
- Modify: `kc-dashboard/src/views/Reminders.tsx`
- Modify: `kc-dashboard/src/views/Reminders.test.tsx`

- [ ] **Step 1: Add a failing test**

Append:

```tsx
it("snooze +15m chip calls snoozeReminder with a future when_utc", async () => {
  const { snoozeReminder } = await import("../api/reminders");
  renderView();
  await waitFor(() => screen.getByText("stretch"));
  // Snooze button is only visible on the one-shot row (id=1)
  fireEvent.click(screen.getByRole("button", { name: /snooze reminder/i }));
  fireEvent.click(screen.getByRole("button", { name: /\+15m/i }));
  await waitFor(() => {
    expect(snoozeReminder).toHaveBeenCalled();
    const [id, when] = (snoozeReminder as any).mock.calls.at(-1);
    expect(id).toBe(1);
    expect(when).toBeGreaterThan(Date.now() / 1000);
  });
});

it("snooze button is hidden on cron rows", async () => {
  renderView();
  await waitFor(() => screen.getByText("standup"));
  // Only one snooze button should exist (for the one-shot, not the cron)
  expect(screen.getAllByRole("button", { name: /snooze reminder/i })).toHaveLength(1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx -t snooze
```
Expected: FAIL.

- [ ] **Step 3: Add snooze popover**

```tsx
import { snoozeReminder as snoozeReminderApi } from "../api/reminders";

// Inside component:
const [snoozeOpenId, setSnoozeOpenId] = useState<number | null>(null);
const snoozeMut = useMutation({
  mutationFn: (vars: { id: number; when_utc: number }) =>
    snoozeReminderApi(vars.id, vars.when_utc),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["reminders"] }),
});

const QUICK_OFFSETS_SEC: Array<[string, number]> = [
  ["+15m", 15 * 60],
  ["+1h", 60 * 60],
  ["+1d", 24 * 60 * 60],
];
```

In the row, before the cancel button, add:

```tsx
{r.kind === "reminder" && r.status === "pending" && (
  <button
    aria-label="snooze reminder"
    onClick={(e) => { e.stopPropagation(); setSnoozeOpenId(snoozeOpenId === r.id ? null : r.id); }}
    className="text-muted hover:text-text px-2"
  >⏱</button>
)}
```

Below the row's expand panel, render the snooze popover when open:

```tsx
{snoozeOpenId === r.id && (
  <div onClick={(e) => e.stopPropagation()}
       className="bg-panel2 border border-line p-3 mb-2 flex flex-wrap gap-2 items-center">
    <span className="text-muted text-[11px] uppercase tracking-[0.1em] mr-1">Snooze</span>
    {QUICK_OFFSETS_SEC.map(([label, secs]) => (
      <button
        key={label}
        onClick={() => {
          snoozeMut.mutate({ id: r.id, when_utc: Date.now() / 1000 + secs });
          setSnoozeOpenId(null);
        }}
        className="px-2 py-0.5 text-[10px] border border-line hover:border-accent"
      >{label}</button>
    ))}
    <input
      type="datetime-local"
      onChange={(e) => {
        const ts = new Date(e.target.value).getTime() / 1000;
        if (!isNaN(ts)) {
          snoozeMut.mutate({ id: r.id, when_utc: ts });
          setSnoozeOpenId(null);
        }
      }}
      className="bg-bg border border-line px-2 py-0.5 text-[11px]"
    />
  </div>
)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Reminders.tsx kc-dashboard/src/views/Reminders.test.tsx
git commit -m "feat(kc-dashboard): snooze popover with quick chips + custom time"
```

---

## Task 21: Chat bubble badge "from reminder #N"

**Files:**
- Modify: `kc-dashboard/src/api/conversations.ts` — extend the message type if not already exposing `scheduled_job_id`
- Modify: `kc-dashboard/src/views/Chat.tsx`
- Test: `kc-dashboard/src/views/Chat.test.tsx` (add this if it doesn't exist; otherwise extend)

- [ ] **Step 1: Read the existing message types in conversations.ts and Chat.tsx**

```bash
cat kc-dashboard/src/api/conversations.ts
grep -n "scheduled_job_id\|AssistantMessage\|message" kc-dashboard/src/views/Chat.tsx | head -20
```

- [ ] **Step 2: Surface `scheduled_job_id` on the assistant message type**

In `conversations.ts`, find the message type/union and add `scheduled_job_id?: number | null` to whichever variant represents an `AssistantMessage` (or to the shared base, if simpler).

Then, on the supervisor side, ensure `GET /conversations/{cid}/messages` returns this field. Read `http_routes.py:195` (the endpoint) and `_message_to_dict` (around line 33). Since `_message_to_dict` uses `asdict(m)` on a kc-core dataclass that has no `scheduled_job_id` field, we instead need to merge it in from the storage row at the route level. Patch the endpoint to include `scheduled_job_id` from the row:

```python
# http_routes.py — inside the GET /conversations/{cid}/messages handler
# Replace whatever shape the handler currently returns with one that includes
# scheduled_job_id from each row dict before serializing.
@app.get("/conversations/{cid}/messages")
def get_messages(cid: int):
    deps = app.state.deps
    rows = deps.storage.list_messages_with_rows(cid)  # see below
    out = []
    for msg, row in rows:
        d = _message_to_dict(msg, usage=row.get("usage_json_decoded"))
        if row.get("scheduled_job_id") is not None:
            d["scheduled_job_id"] = row["scheduled_job_id"]
        out.append(d)
    return {"messages": out}
```

If `Storage.list_messages_with_rows` doesn't exist, add a minimal helper that returns `[(MessageDataclass, raw_row_dict), ...]` — pair each rehydrated message with its raw row so the route can pull `scheduled_job_id` without changing the kc-core dataclass.

(Optional smaller change: if `ConversationManager.list_messages` already returns rows with the column, just pass it through. The above is the conservative path.)

Add a backend integration test confirming the column flows through:

```python
# kc-supervisor/tests/test_http_messages_scheduled_job_id.py
from fastapi.testclient import TestClient


def test_messages_endpoint_includes_scheduled_job_id(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 second", content="hi", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    # Manually fire via the runner to simulate APS firing the trigger.
    # The conftest fixture wires a MagicMock runner; for this test we instead
    # construct a real ReminderRunner against deps and register it on the
    # module-level fire_reminder.
    from kc_supervisor.scheduling.runner import (
        ReminderRunner, set_active_runner, clear_active_runner, fire_reminder,
    )
    import asyncio
    runner = ReminderRunner(
        storage=deps_with_scheduler.storage,
        conversations=deps_with_scheduler.conversations,
        connector_registry=None,  # dashboard channel doesn't need a connector
        coroutine_runner=lambda c: asyncio.run(c),
        broadcaster=deps_with_scheduler.reminders_broadcaster,
    )
    set_active_runner(runner)
    try:
        fire_reminder(res["id"])
        with TestClient(app_with_scheduler) as client:
            r = client.get("/conversations/1/messages")
            body = r.json()["messages"]
        stamped = [m for m in body if m.get("scheduled_job_id") == res["id"]]
        assert len(stamped) == 1
    finally:
        clear_active_runner()
```

(If `parse_when("now+0")` doesn't accept that form, use a tiny offset like `"in 1 second"` and `time.sleep(1.5)` before firing — or use the existing helper pattern from `test_reminder_runner.py`.)

- [ ] **Step 3: Run backend test to verify pipeline works**

```bash
cd kc-supervisor && uv run pytest tests/test_http_messages_scheduled_job_id.py -v
```
Expected: PASS.

- [ ] **Step 4: Frontend test for the bubble badge**

```tsx
// kc-dashboard/src/views/Chat.test.tsx (add to existing file or create)
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// Intentionally test the bubble component / Chat view in isolation.
// If Chat.tsx is monolithic, extract the bubble into a tiny component for testability.
import { AssistantBubble } from "./Chat";  // export this from Chat.tsx as part of this task

describe("AssistantBubble badge", () => {
  it("renders 'from reminder #42' when scheduled_job_id is set", () => {
    render(
      <MemoryRouter>
        <AssistantBubble content="hi" scheduled_job_id={42} />
      </MemoryRouter>
    );
    expect(screen.getByText(/from reminder #42/i)).toBeInTheDocument();
  });

  it("renders no footer when scheduled_job_id is null/undefined", () => {
    render(
      <MemoryRouter>
        <AssistantBubble content="hi" scheduled_job_id={null} />
      </MemoryRouter>
    );
    expect(screen.queryByText(/from reminder/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run frontend test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/views/Chat.test.tsx
```
Expected: FAIL — `AssistantBubble` not exported, badge not rendered.

- [ ] **Step 6: Implement in Chat.tsx**

Inside `Chat.tsx`, extract or add a tiny `AssistantBubble` component that accepts `content` and `scheduled_job_id`. Render the existing bubble UI. After the bubble's content, append:

```tsx
import { Link } from "react-router-dom";

export function AssistantBubble({
  content, scheduled_job_id,
}: { content: string; scheduled_job_id?: number | null }) {
  return (
    <div className="…existing-bubble-classes…">
      <div className="…content-classes…">{content}</div>
      {scheduled_job_id != null && (
        <Link
          to={`/reminders?highlight=${scheduled_job_id}`}
          className="block mt-1 text-[10px] uppercase tracking-[0.12em] text-muted2 hover:text-accent"
        >
          ↻ from reminder #{scheduled_job_id}
        </Link>
      )}
    </div>
  );
}
```

Wire `AssistantBubble` into the existing message-rendering path in `Chat.tsx`. Pass `scheduled_job_id` through from the message payload. The exact integration depends on the current Chat.tsx structure — don't refactor more than necessary.

- [ ] **Step 7: Run frontend test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/views/Chat.test.tsx
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_http_messages_scheduled_job_id.py kc-dashboard/src/api/conversations.ts kc-dashboard/src/views/Chat.tsx kc-dashboard/src/views/Chat.test.tsx
git commit -m "feat: 'from reminder #N' badge on assistant bubbles when scheduled_job_id is set"
```

---

## Task 22: Highlight + scroll-to-row when navigating from a bubble

**Files:**
- Modify: `kc-dashboard/src/views/Reminders.tsx`
- Modify: `kc-dashboard/src/views/Reminders.test.tsx`

- [ ] **Step 1: Add a failing test**

```tsx
it("scrolls and highlights the row matching ?highlight= param", async () => {
  const scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy;
  renderView("/reminders?highlight=1");
  await waitFor(() => screen.getByText("stretch"));
  await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
  expect(screen.getByText("stretch").closest("li")).toHaveClass(/pulse|highlight/);
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx -t "highlight"
```
Expected: FAIL.

- [ ] **Step 3: Implement highlight logic**

In `Reminders.tsx`:

```tsx
import { useEffect, useRef } from "react";

const highlightId = Number(params.get("highlight")) || null;
const rowRefs = useRef<Map<number, HTMLLIElement>>(new Map());
const [pulseId, setPulseId] = useState<number | null>(null);

useEffect(() => {
  if (highlightId == null) return;
  const el = rowRefs.current.get(highlightId);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setPulseId(highlightId);
    const t = setTimeout(() => setPulseId(null), 2000);
    return () => clearTimeout(t);
  }
}, [highlightId, reminders.length]);
```

In the `<li>` element, add `ref` and conditional class:

```tsx
<li
  key={r.id}
  ref={(el) => { if (el) rowRefs.current.set(r.id, el); else rowRefs.current.delete(r.id); }}
  className={"font-mono text-xs " + (pulseId === r.id ? "highlight-pulse" : "")}
>
```

Add the keyframe to `index.css`:

```css
.highlight-pulse {
  animation: kc-highlight 2s ease-out;
}
@keyframes kc-highlight {
  0%   { background-color: rgb(var(--accent) / 0.30); }
  100% { background-color: transparent; }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd kc-dashboard && npx vitest run src/views/Reminders.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Reminders.tsx kc-dashboard/src/views/Reminders.test.tsx kc-dashboard/src/index.css
git commit -m "feat(kc-dashboard): scroll-to + 2s pulse highlight when ?highlight=N is set"
```

---

## Task 23: Smoke gates document

**Files:**
- Create: `docs/superpowers/specs/2026-05-10-reminders-phase3-SMOKE.md`

- [ ] **Step 1: Write the smoke gate doc**

```markdown
# Reminders Phase 3 — manual smoke gates

Run after merging the implementation. Each gate is a fresh dev environment:
both supervisor and dashboard running, Telegram + iMessage configured.

## SG-1 — One-shot appears in the Reminders tab
Schedule a one-shot ~1 minute out from chat. Open the Reminders tab.
- [ ] Row appears within 30s with status `pending`.
- [ ] `next_fire` shows a sensible countdown (e.g., "in 53s").
- [ ] Channel pill matches the conversation channel.

## SG-2 — Snooze pushes the fire time
- [ ] Click the ⏱ icon, then `+15m`.
- [ ] Row's `next_fire` updates immediately (WS-driven, no manual refresh).
- [ ] Row remains `pending`.

## SG-3 — Cancel from dashboard
- [ ] Click ×, then Confirm.
- [ ] Row stays in the list with status `cancelled` (filter chip "cancelled" reveals it).
- [ ] Verify in chat: `list_reminders` no longer shows this row in active list.

## SG-4 — Bubble linking
Schedule a one-shot ~10s out and let it fire.
- [ ] Bubble appears in Chat with footer `↻ from reminder #N`.
- [ ] Click the footer → Reminders tab opens, row highlighted with a 2s pulse.
- [ ] Row status is `done`.

## SG-5 — Cross-channel realtime
Open two browser tabs to the dashboard.
- [ ] Snooze in tab A.
- [ ] Tab B updates the same row without manual refresh.

## SG-6 — Failed path surfaces correctly
Force a runner failure (e.g., disable the destination connector temporarily, schedule a reminder targeting it, let it fire).
- [ ] Row appears in `failed` filter chip with status `failed`.
- [ ] No retry button is offered (out of scope).
- [ ] If a bubble was persisted before failure, the link still resolves.

## SG-7 — Cron round-trip
- [ ] Schedule a `0 9 * * *` cron from chat.
- [ ] Recurring tab shows it; expand panel shows the cron spec.
- [ ] Snooze button is hidden on cron rows.
- [ ] Cancel inline → row goes `cancelled`; verify cron stops firing (wait one cycle or check APS jobs).

Mark each gate ✅ before merging Phase 3 to main.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-10-reminders-phase3-SMOKE.md
git commit -m "docs: SMOKE gates for reminders Phase 3"
```

---

## Final verification

- [ ] **Run full backend suite**
  ```bash
  cd kc-supervisor && uv run pytest -q
  ```
  Expected: PASS.

- [ ] **Run full frontend suite**
  ```bash
  cd kc-dashboard && npm test -- --run
  ```
  Expected: PASS.

- [ ] **Type-check the dashboard**
  ```bash
  cd kc-dashboard && npx tsc --noEmit
  ```
  Expected: clean.

- [ ] **Boot the supervisor + dashboard locally and walk through SG-1 through SG-7**, marking each gate complete in the SMOKE doc.

- [ ] **Final commit (if SMOKE checklist edits)**
  ```bash
  git add docs/superpowers/specs/2026-05-10-reminders-phase3-SMOKE.md
  git commit -m "docs: mark Phase 3 smoke gates green"
  ```

---

## Notes for the executor

- **Don't refactor `Chat.tsx` more than necessary.** Task 21 asks you to extract `AssistantBubble`. If the existing structure already has a similar component, reuse it — only export it if it isn't already.
- **Existing test fixtures** live in `kc-supervisor/tests/conftest.py` (the `deps` and `app` fixtures). Task 10 adds `deps_with_scheduler` and `app_with_scheduler` siblings; subsequent HTTP/WS tests reuse those.
- **`_make_runner` returns a 4-tuple** `(ReminderRunner, Storage, conv_mgr_mock, connector_registry_mock)`. Don't unpack it as a 3-tuple.
- **APS modify_job vs reschedule_job** — Task 7 uses `reschedule_job` (the modern API). If the codebase elsewhere uses `modify_job`, both work; pick whichever is consistent.
- **Single-user app** — no `user_id` field anywhere. The spec mentions user-scoping; in practice, "all reminders" == "everything in the DB."
- **Spec divergences (intentional, recorded here):**
  - Cancel switched from hard-delete to soft-cancel (`status='cancelled'`). Required for the Phase 3 cancelled filter to be meaningful. Existing chat `cancel_reminder` UX is unchanged.
  - WS uses a dedicated `/ws/reminders` endpoint with the `RemindersBroadcaster.subscribe` pattern, mirroring `/ws/approvals` (no global "WS hub" exists today).
  - HTTP API uses DB-native `kind=reminder|cron` rather than the spec's `oneshot|cron`. UI labels remain "One-shot / Recurring."
