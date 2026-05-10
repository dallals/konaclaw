# Reminders Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-channel scheduling and agent-phrased mode to the Phase 1 reminders system, so the agent can schedule a reminder that fires in a different channel than where it was scheduled, and optionally compose the message text via a fresh agent turn at fire time.

**Architecture:** Two orthogonal features sharing one new `mode` column. Cross-channel adds a `channel_routing` allowlist table and a `target_channel` tool arg; the runner resolves the destination conversation at fire time via the existing `connector_conv_map`. Agent-phrased adds a `mode` tool arg; the runner calls a new `_compose_agent_phrased` helper that swaps tools, runs an agent turn, and returns the final assistant text. Both list/cancel tools default to a wider `scope="user"`.

**Tech Stack:** Python 3.14, SQLite (with PRAGMA-driven idempotent migrations), APScheduler, asyncio, kc-core's `CoreAgent.send_stream`, pytest.

**Spec:** `docs/superpowers/specs/2026-05-09-reminders-phase2-design.md`

---

## File Structure

| File | Role | Change |
|---|---|---|
| `kc-supervisor/src/kc_supervisor/storage.py` | SQLite schema + DAO | Add `mode` column ALTER, `channel_routing` table, three storage methods, update `add_scheduled_job` signature |
| `kc-supervisor/src/kc_supervisor/conversations.py` | Conversation manager | Add `get_or_create(channel, chat_id, agent)` helper |
| `kc-supervisor/src/kc_supervisor/scheduling/service.py` | High-level scheduler API | Add `target_channel` + `mode` to schedule methods, `scope` to list/cancel, return `channel`/`mode` in views |
| `kc-supervisor/src/kc_supervisor/scheduling/tools.py` | Agent-facing tool definitions | Add new args to all four tools, update descriptions |
| `kc-supervisor/src/kc_supervisor/scheduling/runner.py` | APScheduler fire callback | Resolve `dest_conv_id`, branch on `mode`, add `_compose_agent_phrased` |
| `kc-supervisor/src/kc_supervisor/main.py` | Boot wiring | Always construct `ConnectorRegistry`; pass `agent_registry` to `ReminderRunner`; seed `channel_routing` for telegram |
| `kc-supervisor/src/kc_supervisor/cli.py` (new) or extend existing entrypoint | CLI helper | `kc-supervisor channel-routing add/list/disable` subcommand |
| `kc-supervisor/tests/test_scheduling_storage.py` | Storage tests | Add ~12 tests for `mode` and `channel_routing` |
| `kc-supervisor/tests/test_conversations.py` | Conversation tests | Add ~3 tests for `get_or_create` |
| `kc-supervisor/tests/test_scheduling_service.py` | Service tests | Add ~15 tests for cross-channel + mode + scope |
| `kc-supervisor/tests/test_scheduling_tools.py` | Tools tests | Add ~10 tests for new args + schemas |
| `kc-supervisor/tests/test_reminder_runner.py` | Runner tests | Add ~12 tests for `dest_conv_id` resolution + agent-phrased |
| `kc-supervisor/tests/test_phase2_integration.py` (new) | E2E test | One end-to-end test scheduling cross-channel and verifying dispatch |
| `docs/SMOKE.md` (or wherever Phase 1 SMOKE lives) | Manual smoke gates | Add 4 new gates for Phase 2 |

---

## Part 1 — Storage: schema + DAO additions

### Task 1.1: Add `mode` column to `scheduled_jobs` (idempotent ALTER)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py:109-124` (the `init()` method's PRAGMA block)
- Test: `kc-supervisor/tests/test_scheduling_storage.py`

- [ ] **Step 1: Write the failing test**

Append to `test_scheduling_storage.py`:

```python
def test_init_adds_mode_column_idempotently(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.init()  # second call must not fail
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)").fetchall()}
    assert "mode" in cols


def test_existing_rows_get_default_literal_mode(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    # Insert via raw SQL to simulate a Phase 1 row that pre-dates the mode column.
    with s.connect() as c:
        c.execute(
            "INSERT INTO scheduled_jobs "
            "(kind, agent, conversation_id, channel, chat_id, payload, "
            " when_utc, cron_spec, status, attempts, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("reminder", "kona", cid, "telegram", "C1", "x",
             1.0, None, "pending", 0, 1.0),
        )
    rows = s.list_scheduled_jobs()
    assert rows[0]["mode"] == "literal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py::test_init_adds_mode_column_idempotently tests/test_scheduling_storage.py::test_existing_rows_get_default_literal_mode -v`
Expected: FAIL — column does not exist.

- [ ] **Step 3: Add the column to schema + idempotent migration**

In `storage.py`:

1. Update the `SCHEMA` constant `scheduled_jobs` block (around line 76-91) to include the `mode` column:

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    agent TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    when_utc REAL,
    cron_spec TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_fired_at REAL,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'literal',
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
```

2. In `init()`, after the existing `msg_cols` block (line 122-124), append:

```python
            job_cols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)").fetchall()}
            if "mode" not in job_cols:
                c.execute("ALTER TABLE scheduled_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'literal'")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py::test_init_adds_mode_column_idempotently tests/test_scheduling_storage.py::test_existing_rows_get_default_literal_mode -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_scheduling_storage.py
git commit -m "feat(kc-supervisor): add mode column to scheduled_jobs (default literal)"
```

---

### Task 1.2: Add `channel_routing` table + storage methods

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py` (add to SCHEMA + add three methods)
- Test: `kc-supervisor/tests/test_scheduling_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_storage.py`:

```python
def test_channel_routing_get_returns_none_when_missing(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    assert s.get_channel_routing("telegram") is None


def test_channel_routing_upsert_then_get(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    routing = s.get_channel_routing("telegram")
    assert routing == {"default_chat_id": "8627206839", "enabled": 1}


def test_channel_routing_upsert_overwrites(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "old", enabled=1)
    s.upsert_channel_routing("telegram", "new", enabled=0)
    routing = s.get_channel_routing("telegram")
    assert routing == {"default_chat_id": "new", "enabled": 0}


def test_channel_routing_list(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    s.upsert_channel_routing("imessage", "I", enabled=0)
    rows = s.list_channel_routing()
    by_channel = {r["channel"]: r for r in rows}
    assert by_channel["telegram"]["default_chat_id"] == "T"
    assert by_channel["telegram"]["enabled"] == 1
    assert by_channel["imessage"]["enabled"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py -k channel_routing -v`
Expected: FAIL — `get_channel_routing` does not exist.

- [ ] **Step 3: Add table + methods**

In `storage.py`, append to the `SCHEMA` string (after the `scheduled_jobs` block, before the closing `"""`):

```sql

CREATE TABLE IF NOT EXISTS channel_routing (
    channel          TEXT PRIMARY KEY,
    default_chat_id  TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1
);
```

After `delete_scheduled_job` (around line 427), add:

```python
    # ----- channel routing (cross-channel allowlist) -----

    def get_channel_routing(self, channel: str) -> Optional[dict]:
        with self.connect() as c:
            row = c.execute(
                "SELECT default_chat_id, enabled FROM channel_routing WHERE channel=?",
                (channel,),
            ).fetchone()
        return {"default_chat_id": row["default_chat_id"], "enabled": row["enabled"]} if row else None

    def upsert_channel_routing(self, channel: str, default_chat_id: str, enabled: int) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO channel_routing (channel, default_chat_id, enabled) "
                "VALUES (?,?,?) "
                "ON CONFLICT(channel) DO UPDATE SET "
                "default_chat_id=excluded.default_chat_id, enabled=excluded.enabled",
                (channel, default_chat_id, enabled),
            )

    def list_channel_routing(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT channel, default_chat_id, enabled FROM channel_routing ORDER BY channel ASC"
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py -k channel_routing -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_scheduling_storage.py
git commit -m "feat(kc-supervisor): add channel_routing table for cross-channel allowlist"
```

---

### Task 1.3: Add `mode` arg to `add_scheduled_job`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py:357-378` (`add_scheduled_job`)
- Test: `kc-supervisor/tests/test_scheduling_storage.py`

- [ ] **Step 1: Write the failing test**

Append to `test_scheduling_storage.py`:

```python
def test_add_scheduled_job_persists_mode(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )
    row = s.get_scheduled_job(job_id)
    assert row["mode"] == "agent_phrased"


def test_add_scheduled_job_default_mode_is_literal(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=1.0, cron_spec=None,
    )
    row = s.get_scheduled_job(job_id)
    assert row["mode"] == "literal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py::test_add_scheduled_job_persists_mode tests/test_scheduling_storage.py::test_add_scheduled_job_default_mode_is_literal -v`
Expected: FAIL — `add_scheduled_job` does not accept `mode`.

- [ ] **Step 3: Update `add_scheduled_job`**

Replace the method (lines ~357-378) with:

```python
    def add_scheduled_job(
        self,
        *,
        kind: str,
        agent: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        payload: str,
        when_utc: Optional[float],
        cron_spec: Optional[str],
        mode: str = "literal",
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO scheduled_jobs "
                "(kind, agent, conversation_id, channel, chat_id, payload, "
                " when_utc, cron_spec, status, attempts, created_at, mode) "
                "VALUES (?,?,?,?,?,?,?,?, 'pending', 0, ?, ?)",
                (kind, agent, conversation_id, channel, chat_id, payload,
                 when_utc, cron_spec, time.time(), mode),
            )
            return int(cur.lastrowid)
```

- [ ] **Step 4: Run all storage tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_storage.py -v`
Expected: PASS (all existing + new tests).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_scheduling_storage.py
git commit -m "feat(kc-supervisor): add mode arg to add_scheduled_job"
```

---

## Part 2 — ConversationManager: `get_or_create`

### Task 2.1: Add `get_or_create(channel, chat_id, agent)` helper

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/conversations.py:25-32`
- Test: `kc-supervisor/tests/test_conversations.py`

This consolidates the existing pattern from `inbound.py:66-70` (lookup + start + put_conv_for_chat + set_title) into one method on `ConversationManager` so the runner can resolve destination conversations the same way inbound does.

- [ ] **Step 1: Write the failing tests**

Append to `test_conversations.py`:

```python
def test_get_or_create_returns_existing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = ConversationManager(s)
    cid = cm.start(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    assert cm.get_or_create(channel="telegram", chat_id="C1", agent="kona") == cid


def test_get_or_create_creates_when_missing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = ConversationManager(s)
    new_cid = cm.get_or_create(channel="telegram", chat_id="C1", agent="kona")
    assert new_cid is not None
    # Mapping is now persisted.
    assert s.get_conv_for_chat("telegram", "C1", "kona") == new_cid
    # Title was set.
    conv = s.get_conversation(new_cid)
    assert conv["title"] == "telegram:C1"


def test_get_or_create_creates_when_mapping_points_to_deleted_conversation(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = ConversationManager(s)
    stale_cid = cm.start(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", stale_cid)
    s.delete_conversation(stale_cid)
    new_cid = cm.get_or_create(channel="telegram", chat_id="C1", agent="kona")
    assert new_cid != stale_cid
    assert s.get_conv_for_chat("telegram", "C1", "kona") == new_cid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_conversations.py -k get_or_create -v`
Expected: FAIL — `get_or_create` does not exist.

- [ ] **Step 3: Add `get_or_create` method**

In `conversations.py`, after the `start` method (line ~26), add:

```python
    def get_or_create(self, *, channel: str, chat_id: str, agent: str) -> int:
        """Resolve the conversation id for a (channel, chat_id, agent) triple.

        If a mapping exists and the conversation still exists, returns it.
        Otherwise creates a new conversation, sets its title to ``channel:chat_id``,
        and writes the mapping. Mirrors the pattern used by InboundRouter.
        """
        existing = self.s.get_conv_for_chat(channel, chat_id, agent)
        if existing is not None and self.s.get_conversation(existing) is not None:
            return existing
        new_cid = self.s.create_conversation(agent=agent, channel=channel)
        self.s.set_conversation_title(new_cid, f"{channel}:{chat_id}")
        self.s.put_conv_for_chat(channel, chat_id, agent, new_cid)
        return new_cid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_conversations.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor `inbound.py` to use the helper (optional but cleaner)**

In `inbound.py` (around line 65-70), replace:

```python
        storage = self.conversations.s
        cid = storage.get_conv_for_chat(env.channel, env.chat_id, agent_name)
        if cid is None or storage.get_conversation(cid) is None:
            cid = self.conversations.start(agent=agent_name, channel=env.channel)
            storage.set_conversation_title(cid, f"{env.channel}:{env.chat_id}")
            storage.put_conv_for_chat(env.channel, env.chat_id, agent_name, cid)
```

with:

```python
        cid = self.conversations.get_or_create(
            channel=env.channel, chat_id=env.chat_id, agent=agent_name,
        )
```

Run inbound tests to confirm no regression: `cd kc-supervisor && uv run pytest tests/test_inbound.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/conversations.py kc-supervisor/src/kc_supervisor/inbound.py kc-supervisor/tests/test_conversations.py
git commit -m "feat(kc-supervisor): add ConversationManager.get_or_create helper"
```

---

## Part 3 — ScheduleService: cross-channel + mode + scope

### Task 3.1: Add `target_channel` + `mode` to `schedule_one_shot`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py:91-131` (`schedule_one_shot`)
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_service.py` (use the existing fixtures — check the file for `_make_service` or similar; if not present, look at how Phase 1 tests construct `ScheduleService` and follow that pattern):

```python
def test_schedule_one_shot_target_channel_uses_routing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    runner = MagicMock()
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="dinner",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "telegram"
    assert row["chat_id"] == "8627206839"
    assert row["mode"] == "literal"


def test_schedule_one_shot_target_channel_current_keeps_ctx(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="x",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="current",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "dashboard"
    assert row["chat_id"] == "ws-1"


def test_schedule_one_shot_target_channel_unknown_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="not configured"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="telegram",
        )


def test_schedule_one_shot_target_channel_disabled_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "X", enabled=0)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="disabled"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="telegram",
        )


def test_schedule_one_shot_invalid_target_channel_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown channel"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="bogus",
        )


def test_schedule_one_shot_invalid_mode_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown mode"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            mode="bogus",
        )


def test_schedule_one_shot_mode_agent_phrased_persists(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="dinner trigger",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        mode="agent_phrased",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["mode"] == "agent_phrased"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -k "target_channel or mode" -v`
Expected: FAIL — service does not accept `target_channel` / `mode`.

- [ ] **Step 3: Update `schedule_one_shot`**

Replace the method (lines ~91-131) with:

```python
    _ALLOWED_TARGET_CHANNELS = {"current", "telegram", "dashboard", "imessage"}
    _ALLOWED_MODES = {"literal", "agent_phrased"}

    def schedule_one_shot(
        self,
        *,
        when: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
        target_channel: str = "current",
        mode: str = "literal",
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if mode not in self._ALLOWED_MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if target_channel not in self._ALLOWED_TARGET_CHANNELS:
            raise ValueError(f"unknown channel {target_channel!r}")

        if target_channel == "current":
            use_channel, use_chat_id = channel, chat_id
        else:
            routing = self.storage.get_channel_routing(target_channel)
            if routing is None:
                raise ValueError(f"channel {target_channel!r} not configured (no routing entry)")
            if not routing["enabled"]:
                raise ValueError(f"channel {target_channel!r} is disabled")
            use_channel, use_chat_id = target_channel, routing["default_chat_id"]

        target = parse_when(when, self._tz)
        if is_past(target):
            raise ValueError(f"'when' resolves to the past: {when!r}")
        target_utc = target.astimezone(_tz_mod.utc)

        job_id = self.storage.add_scheduled_job(
            kind="reminder", agent=agent, conversation_id=conversation_id,
            channel=use_channel, chat_id=use_chat_id, payload=content,
            when_utc=target_utc.timestamp(), cron_spec=None,
            mode=mode,
        )
        try:
            self._scheduler.add_job(
                fire_reminder, trigger=DateTrigger(run_date=target),
                kwargs={"job_id": job_id}, id=str(job_id),
                misfire_grace_time=86400,
                replace_existing=True,
            )
        except Exception:
            self.storage.delete_scheduled_job(job_id)
            raise

        return {
            "id": job_id,
            "fires_at": target.isoformat(),
            "fires_at_human": humanize(target),
            "kind": "reminder",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v`
Expected: PASS (all existing + new).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): add target_channel + mode to schedule_one_shot"
```

---

### Task 3.2: Add `target_channel` + `mode` to `schedule_cron`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py:135-185` (`schedule_cron`)
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_service.py`:

```python
def test_schedule_cron_target_channel_uses_routing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_cron(
        cron="0 9 * * *", content="standup",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "telegram"
    assert row["chat_id"] == "8627206839"
    assert row["mode"] == "agent_phrased"


def test_schedule_cron_invalid_mode_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown mode"):
        svc.schedule_cron(
            cron="0 9 * * *", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            mode="bogus",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py::test_schedule_cron_target_channel_uses_routing tests/test_scheduling_service.py::test_schedule_cron_invalid_mode_raises -v`
Expected: FAIL.

- [ ] **Step 3: Update `schedule_cron`**

Replace the method (lines ~135-185) with:

```python
    def schedule_cron(
        self,
        *,
        cron: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
        target_channel: str = "current",
        mode: str = "literal",
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if mode not in self._ALLOWED_MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if target_channel not in self._ALLOWED_TARGET_CHANNELS:
            raise ValueError(f"unknown channel {target_channel!r}")
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron: {cron!r}")

        if target_channel == "current":
            use_channel, use_chat_id = channel, chat_id
        else:
            routing = self.storage.get_channel_routing(target_channel)
            if routing is None:
                raise ValueError(f"channel {target_channel!r} not configured (no routing entry)")
            if not routing["enabled"]:
                raise ValueError(f"channel {target_channel!r} is disabled")
            use_channel, use_chat_id = target_channel, routing["default_chat_id"]

        try:
            trigger = CronTrigger.from_crontab(cron, timezone=self._tz)
        except ValueError as e:
            raise ValueError(f"invalid cron: {cron!r} ({e})")

        next_fire = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
        try:
            human_summary = get_description(cron)
        except Exception:
            human_summary = cron

        job_id = self.storage.add_scheduled_job(
            kind="cron", agent=agent, conversation_id=conversation_id,
            channel=use_channel, chat_id=use_chat_id, payload=content,
            when_utc=None, cron_spec=cron,
            mode=mode,
        )
        try:
            self._scheduler.add_job(
                fire_reminder, trigger=trigger,
                kwargs={"job_id": job_id}, id=str(job_id),
                coalesce=True,
                replace_existing=True,
            )
        except Exception:
            self.storage.delete_scheduled_job(job_id)
            raise

        return {
            "id": job_id,
            "next_fire": next_fire.isoformat() if next_fire else None,
            "next_fire_human": humanize(next_fire) if next_fire else None,
            "human_summary": human_summary,
            "kind": "cron",
        }
```

- [ ] **Step 4: Run all service tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): add target_channel + mode to schedule_cron"
```

---

### Task 3.3: Add `scope` to `list_reminders`; include channel + mode in views

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py:189-196` (`list_reminders`) and `_row_to_view` (~240-270)
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_service.py`:

```python
def test_list_reminders_default_scope_user_returns_all(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="A",
        conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.schedule_one_shot(
        when="in 1 hour", content="B",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    out = svc.list_reminders(conversation_id=cid_a)  # default scope="user"
    contents = [r["content"] for r in out["reminders"]]
    assert "A" in contents and "B" in contents


def test_list_reminders_scope_conversation_filters(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="A",
        conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.schedule_one_shot(
        when="in 1 hour", content="B",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    out = svc.list_reminders(conversation_id=cid_a, scope="conversation")
    contents = [r["content"] for r in out["reminders"]]
    assert contents == ["A"]


def test_list_reminders_view_includes_channel_and_mode(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="X",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    out = svc.list_reminders(conversation_id=cid)
    r = out["reminders"][0]
    assert r["channel"] == "telegram"
    assert r["mode"] == "agent_phrased"


def test_list_reminders_invalid_scope_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown scope"):
        svc.list_reminders(conversation_id=cid, scope="bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -k "list_reminders and (scope or includes)" -v`
Expected: FAIL.

- [ ] **Step 3: Update `list_reminders` and `_row_to_view`**

In `service.py`, add the `_ALLOWED_SCOPES` constant near `_ALLOWED_MODES`:

```python
    _ALLOWED_SCOPES = {"user", "conversation"}
```

Replace `list_reminders` (lines ~189-196) with:

```python
    def list_reminders(
        self, *, conversation_id: int, active_only: bool = True,
        scope: str = "user",
    ) -> dict:
        if scope not in self._ALLOWED_SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        statuses = ("pending",) if active_only else None
        if scope == "conversation":
            rows = self.storage.list_scheduled_jobs(
                conversation_id=conversation_id, statuses=statuses,
            )
        else:
            rows = self.storage.list_scheduled_jobs(statuses=statuses)
        return {"reminders": [self._row_to_view(r) for r in rows]}
```

In `_row_to_view` (lines ~240-270), update the return dict to include `channel` and `mode`:

```python
        return {
            "id": row["id"],
            "kind": kind,
            "fires_at_human": fires_at_human,
            "next_fire_human": next_fire_human,
            "content": row["payload"],
            "status": row["status"],
            "human_summary": human_summary,
            "channel": row["channel"],
            "mode": row["mode"],
        }
```

- [ ] **Step 4: Run all service tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): list_reminders defaults to user scope; views include channel+mode"
```

---

### Task 3.4: Add `scope` to `cancel_reminder`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py:198-227` (`cancel_reminder`)
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_service.py`:

```python
def test_cancel_reminder_default_scope_user_finds_other_conversation(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    # Cancel from conversation A — must find conversation B's reminder by description.
    result = svc.cancel_reminder("dinner", conversation_id=cid_a)
    assert result["ambiguous"] is False
    assert len(result["cancelled"]) == 1


def test_cancel_reminder_scope_conversation_only_sees_own(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    with pytest.raises(ValueError, match="no reminder matched"):
        svc.cancel_reminder("dinner", conversation_id=cid_a, scope="conversation")


def test_cancel_reminder_invalid_scope_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown scope"):
        svc.cancel_reminder("x", conversation_id=cid, scope="bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -k "cancel_reminder and scope" -v`
Expected: FAIL.

- [ ] **Step 3: Update `cancel_reminder`**

Replace the method (lines ~198-227) with:

```python
    def cancel_reminder(
        self, id_or_description: str, *, conversation_id: int,
        scope: str = "user",
    ) -> dict:
        if not id_or_description:
            raise ValueError("id_or_description must not be empty")
        if scope not in self._ALLOWED_SCOPES:
            raise ValueError(f"unknown scope {scope!r}")

        if scope == "conversation":
            candidates = self.storage.list_scheduled_jobs(
                conversation_id=conversation_id, statuses=("pending",),
            )
        else:
            candidates = self.storage.list_scheduled_jobs(statuses=("pending",))

        if id_or_description.strip().isdigit():
            target_id = int(id_or_description)
            matches = [r for r in candidates if r["id"] == target_id]
            if not matches:
                raise ValueError(f"no reminder with id {target_id}")
            return self._do_cancel(matches)

        needle = id_or_description.lower()
        matches = [r for r in candidates if needle in (r["payload"] or "").lower()]
        if not matches:
            raise ValueError(f"no reminder matched {id_or_description!r}")
        if len(matches) > 1:
            return {
                "ambiguous": True,
                "candidates": [
                    {"id": r["id"], "content": r["payload"]} for r in matches
                ],
                "cancelled": [],
            }
        return self._do_cancel(matches)
```

- [ ] **Step 4: Run all service tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): cancel_reminder defaults to user scope"
```

---

## Part 4 — Tool surface

### Task 4.1: Add `target_channel` + `mode` to `schedule_reminder` / `schedule_cron` tools

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/tools.py` (whole file)
- Test: `kc-supervisor/tests/test_scheduling_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_tools.py`:

```python
def test_schedule_reminder_tool_accepts_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    schema = sched.parameters
    assert "target_channel" in schema["properties"]
    assert "mode" in schema["properties"]
    assert "target_channel" not in schema["required"]
    assert "mode" not in schema["required"]


def test_schedule_reminder_tool_forwards_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    sched.impl(when="5pm", content="x", target_channel="telegram", mode="agent_phrased")
    kwargs = svc.schedule_one_shot.call_args.kwargs
    assert kwargs["target_channel"] == "telegram"
    assert kwargs["mode"] == "agent_phrased"


def test_schedule_reminder_tool_defaults_when_args_omitted():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    sched.impl(when="5pm", content="x")
    kwargs = svc.schedule_one_shot.call_args.kwargs
    assert kwargs["target_channel"] == "current"
    assert kwargs["mode"] == "literal"


def test_schedule_cron_tool_accepts_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_cron.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    cron_tool = next(t for t in tools if t.name == "schedule_cron")
    cron_tool.impl(cron="0 9 * * *", content="x", target_channel="telegram", mode="agent_phrased")
    kwargs = svc.schedule_cron.call_args.kwargs
    assert kwargs["target_channel"] == "telegram"
    assert kwargs["mode"] == "agent_phrased"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_tools.py -k "target_channel or schedule_cron_tool_accepts" -v`
Expected: FAIL.

- [ ] **Step 3: Update `tools.py`**

Replace `_schedule_reminder` and `_schedule_cron` and their `Tool` definitions in `build_scheduling_tools`:

```python
    def _schedule_reminder(
        when: str, content: str,
        target_channel: str = "current", mode: str = "literal",
    ) -> dict:
        ctx = current_context()
        return service.schedule_one_shot(
            when=when, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
            target_channel=target_channel, mode=mode,
        )

    def _schedule_cron(
        cron: str, content: str,
        target_channel: str = "current", mode: str = "literal",
    ) -> dict:
        ctx = current_context()
        return service.schedule_cron(
            cron=cron, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
            target_channel=target_channel, mode=mode,
        )
```

In the `Tool(name="schedule_reminder", ...)` definition, replace `parameters` with:

```python
            parameters={
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "natural-language time"},
                    "content": {"type": "string", "description": "reminder text (1-4000 chars). When mode='agent_phrased', interpreted as an internal trigger description for you, not the literal text the user sees."},
                    "target_channel": {
                        "type": "string",
                        "enum": ["current", "telegram", "dashboard", "imessage"],
                        "description": "Use only when the user explicitly asks to be reminded somewhere other than this conversation. Channels not in the configured allowlist will raise. Default 'current'.",
                        "default": "current",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["literal", "agent_phrased"],
                        "description": "If 'agent_phrased', you will be re-invoked at fire time to compose the actual message. The 'content' arg is then a trigger description for you, not user-facing text. Default 'literal'.",
                        "default": "literal",
                    },
                },
                "required": ["when", "content"],
            },
```

In the `Tool(name="schedule_cron", ...)` definition, replace `parameters` with the analogous schema (cron + content + target_channel + mode, required = ["cron", "content"]).

- [ ] **Step 4: Run all tools tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/tools.py kc-supervisor/tests/test_scheduling_tools.py
git commit -m "feat(kc-supervisor): schedule_reminder/schedule_cron tools accept target_channel + mode"
```

---

### Task 4.2: Add `scope` to `list_reminders` / `cancel_reminder` tools

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/tools.py` (the two tool definitions)
- Test: `kc-supervisor/tests/test_scheduling_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scheduling_tools.py`:

```python
def test_list_reminders_tool_accepts_scope():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.list_reminders.return_value = {"reminders": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    lst = next(t for t in tools if t.name == "list_reminders")
    assert "scope" in lst.parameters["properties"]
    lst.impl(scope="conversation")
    assert svc.list_reminders.call_args.kwargs["scope"] == "conversation"


def test_list_reminders_tool_default_scope_user():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.list_reminders.return_value = {"reminders": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    lst = next(t for t in tools if t.name == "list_reminders")
    lst.impl()
    assert svc.list_reminders.call_args.kwargs["scope"] == "user"


def test_cancel_reminder_tool_accepts_scope():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.cancel_reminder.return_value = {"ambiguous": False, "candidates": [], "cancelled": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    can = next(t for t in tools if t.name == "cancel_reminder")
    assert "scope" in can.parameters["properties"]
    can.impl(id_or_description="5", scope="conversation")
    assert svc.cancel_reminder.call_args.kwargs["scope"] == "conversation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_tools.py -k scope -v`
Expected: FAIL.

- [ ] **Step 3: Update the two tool impls + schemas**

Replace `_list_reminders` and `_cancel_reminder` impls with:

```python
    def _list_reminders(active_only: bool = True, scope: str = "user") -> dict:
        ctx = current_context()
        return service.list_reminders(
            conversation_id=ctx["conversation_id"], active_only=active_only, scope=scope,
        )

    def _cancel_reminder(id_or_description: str, scope: str = "user") -> dict:
        ctx = current_context()
        return service.cancel_reminder(
            id_or_description, conversation_id=ctx["conversation_id"], scope=scope,
        )
```

In the `Tool(name="list_reminders", ...)` definition, add `scope` to parameters:

```python
            parameters={
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": "boolean",
                        "description": "if True, only pending reminders",
                        "default": True,
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "conversation"],
                        "description": "'user' (default) lists all your reminders across channels. 'conversation' restricts to reminders scheduled in this conversation.",
                        "default": "user",
                    },
                },
                "required": [],
            },
```

In the `Tool(name="cancel_reminder", ...)` definition, add `scope` to parameters:

```python
            parameters={
                "type": "object",
                "properties": {
                    "id_or_description": {
                        "type": "string",
                        "description": "integer ID or description fragment",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "conversation"],
                        "description": "'user' (default) searches all your reminders across channels. 'conversation' restricts to reminders scheduled in this conversation.",
                        "default": "user",
                    },
                },
                "required": ["id_or_description"],
            },
```

- [ ] **Step 4: Run all tools tests**

Run: `cd kc-supervisor && uv run pytest tests/test_scheduling_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/tools.py kc-supervisor/tests/test_scheduling_tools.py
git commit -m "feat(kc-supervisor): list_reminders/cancel_reminder tools accept scope (default user)"
```

---

## Part 5 — Runner: destination resolution + literal cross-channel

### Task 5.1: Resolve `dest_conv_id` via `get_or_create` for all fires

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/runner.py:40-92` (`fire`)
- Test: `kc-supervisor/tests/test_reminder_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_reminder_runner.py`:

```python
def test_fire_persists_to_destination_conversation_for_cross_channel(tmp_path):
    """When a row's channel differs from where it was scheduled, persist to the
    destination conversation (resolved via get_or_create), not the originating one."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    # Scheduling conversation: dashboard. Destination: telegram.
    sched_cid = s.create_conversation(agent="kona", channel="dashboard")
    dest_cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", dest_cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=sched_cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=1.0, cron_spec=None,
    )
    cm = MagicMock()
    cm.get_or_create.return_value = dest_cid
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    runner.fire(job_id)
    cm.get_or_create.assert_called_once_with(channel="telegram", chat_id="C1", agent="kona")
    cm.append.assert_called_once()
    persisted_cid = cm.append.call_args.args[0]
    assert persisted_cid == dest_cid


def test_fire_dashboard_destination_takes_dashboard_branch(tmp_path):
    """A row with channel=dashboard never invokes the connector, even when
    scheduled from telegram. Persists directly to the dashboard conversation."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    sched_cid = s.create_conversation(agent="kona", channel="telegram")
    dest_cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", dest_cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=sched_cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None,
    )
    cm = MagicMock()
    cm.get_or_create.return_value = dest_cid
    connector_registry = MagicMock()
    connector_registry.get.side_effect = AssertionError("dashboard branch should not call connector")
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    runner.fire(job_id)
    cm.append.assert_called_once()
    persisted_cid = cm.append.call_args.args[0]
    assert persisted_cid == dest_cid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -k "destination or dashboard_destination" -v`
Expected: FAIL — runner currently uses `row["conversation_id"]` directly.

- [ ] **Step 3: Update `_make_runner` test helper to wire `get_or_create`**

In the existing `_make_runner` (lines 10-22), the `cm = MagicMock()` line needs to also stub `get_or_create` to return the row's `conversation_id` so existing same-channel tests stay green:

Replace `_make_runner`:

```python
def _make_runner(tmp_path) -> tuple[ReminderRunner, Storage, MagicMock, MagicMock]:
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = MagicMock()
    # Default: get_or_create returns whatever conversation_id was passed via row.
    # Tests that need a different destination override this on `cm.get_or_create`.
    cm.get_or_create.side_effect = lambda channel, chat_id, agent: (
        s.get_conv_for_chat(channel, chat_id, agent)
        or s.create_conversation(agent=agent, channel=channel)
    )
    connector_registry = MagicMock()
    connector = MagicMock()
    connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    return runner, s, cm, connector_registry
```

In `_seed`, also seed the connector_conv_map so same-channel tests resolve to the same conv_id:

```python
def _seed(s: Storage, cm: MagicMock, *, kind: str = "reminder") -> int:
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    return s.add_scheduled_job(
        kind=kind, agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=time.time() + 60 if kind == "reminder" else None,
        cron_spec=None if kind == "reminder" else "0 9 * * *",
    )
```

- [ ] **Step 4: Update `runner.py` to resolve `dest_conv_id`**

Replace `ReminderRunner.fire` (lines 40-92) with:

```python
    def fire(self, job_id: int) -> None:
        row = self.storage.get_scheduled_job(job_id)
        if row is None:
            logger.warning("ReminderRunner.fire: job %s not found; skipping", job_id)
            return

        # Resolve destination conversation. For same-channel rows this returns
        # row["conversation_id"]; for cross-channel rows, the conversation
        # belonging to the destination chat (created on demand).
        try:
            dest_conv_id = self.conversations.get_or_create(
                channel=row["channel"], chat_id=row["chat_id"], agent=row["agent"],
            )
        except Exception:
            logger.exception(
                "ReminderRunner.fire: get_or_create destination failed for job %s", job_id,
            )
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status="failed",
            )
            return

        text = PREFIX + (row["payload"] or "")  # mode branch added in Task 6.2

        if row["channel"] == "dashboard":
            try:
                self.conversations.append(
                    dest_conv_id, AssistantMessage(content=text),
                )
            except Exception:
                logger.exception(
                    "ReminderRunner.fire: dashboard persist failed for job %s", job_id,
                )
                self.storage.update_scheduled_job_after_fire(
                    job_id, fired_at=time.time(), new_status="failed",
                )
                return
            new_status = "done" if row["kind"] == "reminder" else "pending"
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status=new_status,
            )
            return

        try:
            connector = self.connector_registry.get(row["channel"])
            self._run_coro(connector.send(row["chat_id"], text))
        except Exception:
            logger.exception(
                "ReminderRunner.fire: connector send failed for job %s", job_id,
            )
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status="failed",
            )
            return
        try:
            self.conversations.append(
                dest_conv_id, AssistantMessage(content=text),
            )
        except Exception:
            logger.exception(
                "ReminderRunner.fire: persist failed for job %s; user already received message",
                job_id,
            )
        new_status = "done" if row["kind"] == "reminder" else "pending"
        self.storage.update_scheduled_job_after_fire(
            job_id, fired_at=time.time(), new_status=new_status,
        )
```

- [ ] **Step 5: Run all runner tests**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -v`
Expected: PASS (existing + new).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): runner resolves destination conversation via get_or_create"
```

---

## Part 6 — Runner: agent-phrased mode

### Task 6.1: Add `agent_registry` to `ReminderRunner` constructor

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/runner.py:27-38` (`__init__`)
- Modify: `kc-supervisor/src/kc_supervisor/main.py` (wherever `ReminderRunner(...)` is constructed)
- Test: `kc-supervisor/tests/test_reminder_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `test_reminder_runner.py`:

```python
def test_runner_accepts_agent_registry(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = ReminderRunner(
        storage=s, conversations=MagicMock(), connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=MagicMock(),
    )
    assert runner.agent_registry is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py::test_runner_accepts_agent_registry -v`
Expected: FAIL — `__init__` does not accept `agent_registry`.

- [ ] **Step 3: Update `__init__`**

In `runner.py`, replace `__init__` (lines 27-38) with:

```python
    def __init__(
        self,
        *,
        storage: Storage,
        conversations: Any,        # ConversationManager
        connector_registry: Any,   # ConnectorRegistry
        coroutine_runner: CoroRunner,
        agent_registry: Optional[Any] = None,  # AgentRegistry; required for mode='agent_phrased'
    ) -> None:
        self.storage = storage
        self.conversations = conversations
        self.connector_registry = connector_registry
        self._run_coro = coroutine_runner
        self.agent_registry = agent_registry
```

Also update the test fixture `_make_runner` to optionally pass `agent_registry`:

```python
def _make_runner(tmp_path, *, agent_registry=None) -> tuple[ReminderRunner, Storage, MagicMock, MagicMock]:
    # ... existing body ...
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=agent_registry,
    )
    return runner, s, cm, connector_registry
```

- [ ] **Step 4: Update `main.py` construction site**

Find the `ReminderRunner(...)` instantiation in `main.py` (grep for `ReminderRunner(`). Add `agent_registry=agent_registry` (the supervisor already has an `agent_registry` variable in scope; if not, pass the `AgentRegistry` instance — confirm by reading main.py lines 320-360).

- [ ] **Step 5: Run all runner tests**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/src/kc_supervisor/main.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): ReminderRunner accepts optional agent_registry"
```

---

### Task 6.2: Implement `_compose_agent_phrased`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/runner.py` (add helper)
- Test: `kc-supervisor/tests/test_reminder_runner.py`

This is the most involved task. The helper:
1. Looks up the AgentRuntime by `row["agent"]`.
2. Loads conversation history for `dest_conv_id` via `ConversationManager.list_messages`.
3. Saves the agent's current `tools`, swaps in a filtered tool list (no `schedule_reminder`/`schedule_cron`/`cancel_reminder`).
4. Saves the agent's current `system_prompt`, swaps in `base_system_prompt + addendum`.
5. Sets the scheduling context.
6. Runs `core_agent.send_stream(synthetic_trigger)`, collects the final assistant text from the `Complete` frame.
7. Restores tools + system_prompt in a `finally` block.
8. Returns the assistant text or `None` on failure.

- [ ] **Step 1: Write the failing tests**

Append to `test_reminder_runner.py`:

```python
def test_compose_agent_phrased_returns_assistant_text(tmp_path):
    """The helper invokes the agent's send_stream and returns the Complete frame's text."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="dinner trigger",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    # Build a fake AgentRegistry / AgentRuntime / AssembledAgent / CoreAgent.
    fake_core = MagicMock()
    fake_core.tools = MagicMock()
    fake_core.system_prompt = "you are kona"

    async def fake_send_stream(_content):
        # Yield a single Complete frame with an AssistantMessage.
        from kc_core.messages import AssistantMessage
        # Mirror the real frame shape used by inbound (lines 130-143 of inbound.py).
        class FakeComplete:
            reply = AssistantMessage(content="hey, dinner time!")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock()
    fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "you are kona"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []  # empty history

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    text = runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)
    assert text == "hey, dinner time!"


def test_compose_agent_phrased_strips_scheduling_tools(tmp_path):
    """During the fire-time turn, the agent's tools must NOT include scheduling tools.
    After the turn, tools are restored."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    # Fake ToolRegistry with .names() and a mechanism we can swap.
    class FakeToolRegistry:
        def __init__(self, names):
            self._names = list(names)
        def names(self):
            return list(self._names)

    original = FakeToolRegistry(["schedule_reminder", "schedule_cron", "cancel_reminder",
                                  "list_reminders", "search_files"])
    captured_tool_names_during_turn = []

    fake_core = MagicMock()
    fake_core.tools = original
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        # Capture what tools look like AT this point in time.
        captured_tool_names_during_turn.extend(fake_core.tools.names())
        class FakeComplete:
            reply = AssistantMessage(content="ok")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)

    assert "schedule_reminder" not in captured_tool_names_during_turn
    assert "schedule_cron" not in captured_tool_names_during_turn
    assert "cancel_reminder" not in captured_tool_names_during_turn
    assert "search_files" in captured_tool_names_during_turn  # unrelated tools preserved
    # After the turn, tools restored.
    assert "schedule_reminder" in fake_core.tools.names()


def test_compose_agent_phrased_returns_none_when_agent_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def __init__(self, names): self._names = list(names)
        def names(self): return list(self._names)

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry(["schedule_reminder"])
    fake_core.system_prompt = "x"

    async def boom(_content):
        raise RuntimeError("model exploded")
        yield

    fake_core.send_stream = boom
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock(); cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    text = runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)
    assert text is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -k compose_agent_phrased -v`
Expected: FAIL — `_compose_agent_phrased` does not exist.

- [ ] **Step 3: Implement `_compose_agent_phrased`**

Add to `runner.py` near the bottom of the `ReminderRunner` class:

```python
    _AGENT_PHRASED_ADDENDUM = (
        "\n\nYou are responding to a scheduled reminder you set for the user. "
        "The trigger description follows. Compose a single friendly message — "
        "do not mention this is a reminder fire unless the trigger asks you to."
    )
    _STRIPPED_TOOL_NAMES = {"schedule_reminder", "schedule_cron", "cancel_reminder"}

    def _compose_agent_phrased(self, row: dict, *, dest_conv_id: int) -> Optional[str]:
        """Run a fire-time agent turn for an agent_phrased row. Returns the agent's
        final assistant text, or None on failure (caller marks the row failed)."""
        if self.agent_registry is None:
            logger.error(
                "agent_phrased fire requires agent_registry (job %s)", row["id"],
            )
            return None
        runtime = self.agent_registry.get(row["agent"])
        if runtime is None or runtime.assembled is None:
            logger.error(
                "agent_phrased fire: agent %s not found / degraded (job %s)",
                row["agent"], row["id"],
            )
            return None

        core = runtime.assembled.core_agent
        original_tools = core.tools
        original_system_prompt = core.system_prompt
        try:
            # Rehydrate history from the destination conversation, same as inbound.
            history = self.conversations.list_messages(dest_conv_id)
            core.history = list(history)
            core.system_prompt = (
                runtime.assembled.base_system_prompt + self._AGENT_PHRASED_ADDENDUM
            )
            # Strip scheduling tools for this turn only.
            core.tools = _filter_tools(original_tools, exclude=self._STRIPPED_TOOL_NAMES)

            # Set scheduling context so any read-only scheduling tool the agent
            # might call (e.g. list_reminders) sees the destination conversation.
            from kc_supervisor.scheduling.context import set_current_context
            set_current_context({
                "conversation_id": dest_conv_id,
                "channel": row["channel"],
                "chat_id": row["chat_id"],
                "agent": row["agent"],
            })

            scheduled_iso = (
                f"{row['when_utc']}" if row["when_utc"] else (row["cron_spec"] or "?")
            )
            now_iso = f"{time.time():.0f}"
            trigger = (
                f"[Internal trigger — scheduled at {scheduled_iso}, "
                f"fired at {now_iso}] {row['payload']}"
            )

            reply_text: Optional[str] = None

            async def _run() -> Optional[str]:
                from kc_core.messages import AssistantMessage
                final_text: Optional[str] = None
                async for frame in core.send_stream(trigger):
                    reply = getattr(frame, "reply", None)
                    if isinstance(reply, AssistantMessage):
                        final_text = reply.content
                return final_text

            try:
                reply_text = self._run_coro(_run())
            except Exception:
                logger.exception(
                    "agent_phrased turn raised for job %s", row["id"],
                )
                return None

            if not reply_text or not reply_text.strip():
                logger.warning(
                    "agent_phrased turn returned empty text for job %s", row["id"],
                )
                return None
            return reply_text
        finally:
            core.tools = original_tools
            core.system_prompt = original_system_prompt
```

Also add a module-level helper near the top of the file (after `PREFIX = "⏰ "`):

```python
def _filter_tools(tools: Any, *, exclude: set[str]) -> Any:
    """Return a tool registry-like view that hides tools whose names are in `exclude`.

    The kc-core agent reads tools via `.to_openai_schema()`, `.names()`, and
    `.invoke(name, args)`. We construct a minimal proxy that delegates everything
    except those three to the underlying registry, but filters by name.
    """
    class _Filtered:
        def __init__(self, inner: Any, blocked: set[str]) -> None:
            self._inner = inner
            self._blocked = blocked
        def names(self) -> list[str]:
            return [n for n in self._inner.names() if n not in self._blocked]
        def to_openai_schema(self) -> list:
            return [
                t for t in self._inner.to_openai_schema()
                # OpenAI schema rows look like {"type":"function", "function": {"name": ..., ...}}
                if (t.get("function", {}).get("name") if isinstance(t, dict) else None) not in self._blocked
            ]
        def invoke(self, name: str, args: dict) -> Any:
            if name in self._blocked:
                raise ValueError(f"tool {name!r} is unavailable in this context")
            return self._inner.invoke(name, args)
    return _Filtered(tools, exclude)
```

Update the `Optional` import at top of `runner.py` if needed (already present).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -k compose_agent_phrased -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): _compose_agent_phrased runs fire-time agent turn with stripped tools"
```

---

### Task 6.3: Branch `fire()` on `mode`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/runner.py:fire` (the `text = PREFIX + ...` line)
- Test: `kc-supervisor/tests/test_reminder_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_reminder_runner.py`:

```python
def test_fire_literal_mode_uses_prefix_unchanged(tmp_path):
    """Existing Phase 1 behavior: literal rows fire with the ⏰ prefix."""
    runner, s, cm, registry = _make_runner(tmp_path)
    job_id = _seed(s, cm)  # mode defaults to literal
    runner.fire(job_id)
    connector = registry.get.return_value
    content = connector.send.call_args.args[1]
    assert content == "⏰ dinner"


def test_fire_agent_phrased_dispatches_composed_text(tmp_path):
    """agent_phrased rows: runner ships the agent's composed text (no prefix)."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner trigger",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def __init__(self, names): self._names = list(names)
        def names(self): return list(self._names)
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry([])
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        class FakeComplete:
            reply = AssistantMessage(content="hey, ready for dinner?")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner.fire(job_id)
    content = connector.send.call_args.args[1]
    assert content == "hey, ready for dinner?"
    # Persisted text matches.
    assert cm.append.call_args.args[1].content == "hey, ready for dinner?"


def test_fire_agent_phrased_failure_marks_row_failed_no_dispatch(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def names(self): return []
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry()
    fake_core.system_prompt = "x"

    async def boom(_content):
        raise RuntimeError("nope")
        yield

    fake_core.send_stream = boom
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock(); cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"
    connector.send.assert_not_called()
    cm.append.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail (the agent-phrased ones)**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -k "agent_phrased and fire" -v`
Expected: the literal one passes, agent_phrased ones FAIL — `fire` doesn't branch on mode yet.

- [ ] **Step 3: Update `fire()` to branch on mode**

In `runner.py`, replace the line:

```python
        text = PREFIX + (row["payload"] or "")  # mode branch added in Task 6.2
```

with:

```python
        if row["mode"] == "agent_phrased":
            text = self._compose_agent_phrased(row, dest_conv_id=dest_conv_id)
            if text is None:
                self.storage.update_scheduled_job_after_fire(
                    job_id, fired_at=time.time(), new_status="failed",
                )
                return
        else:
            text = PREFIX + (row["payload"] or "")
```

- [ ] **Step 4: Run all runner tests**

Run: `cd kc-supervisor && uv run pytest tests/test_reminder_runner.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): fire() branches on mode and dispatches composed text for agent_phrased"
```

---

## Part 7 — Always-construct ConnectorRegistry

### Task 7.1: Fix `main.py` so ConnectorRegistry is always present

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py` (the connector-registry construction site)
- Test: `kc-supervisor/tests/test_main_restart_hooks.py` or a new small test

- [ ] **Step 1: Locate the current gating**

Run: `grep -n "ConnectorRegistry\|connector_registry" kc-supervisor/src/kc_supervisor/main.py`

Read the lines. The current code likely conditionally constructs the registry only when at least one connector is configured. The fix: always construct it (with whatever connectors ARE configured, possibly none).

- [ ] **Step 2: Write the failing test**

Append to `test_main_restart_hooks.py` (or create `test_phase2_connector_always.py` if cleaner):

```python
def test_connector_registry_present_even_with_no_connectors(monkeypatch, tmp_path):
    """Phase 2 requires the connector registry to always exist so cross-channel
    scheduling can fail predictably at fire time rather than crashing at boot."""
    # This test inspects whatever startup function builds the registry.
    # Implementation: call the startup function with no connector configs and
    # assert the registry attribute exists (possibly empty).
    # The exact shape depends on how main.py exposes wiring — adapt to the
    # function that orchestrates startup (e.g., `build_supervisor(config)`).
    from kc_supervisor.main import build_supervisor  # adjust if name differs
    import asyncio
    cfg = _minimal_config_no_connectors(tmp_path)  # helper or inline construction
    sup = asyncio.run(build_supervisor(cfg))
    assert sup.connector_registry is not None
    assert sup.connector_registry.list() == [] or sup.connector_registry.names() == []
```

If `main.py` doesn't expose a clean entry point to test against, simplify: just inspect the construction site (lines around the current gating) and ensure the variable is unconditionally assigned. Add a unit test only if it's natural to do so.

- [ ] **Step 3: Update `main.py`**

Locate the current gating block (something like `if connectors_configured: connector_registry = ConnectorRegistry(...)`). Replace with unconditional construction:

```python
    # Always build the connector registry, even if zero connectors are configured.
    # Phase 2 cross-channel scheduling requires this so reminders can be scheduled
    # before connectors are wired (allowlist gates schedule-time; fire-time
    # surfaces a clean failure if the destination connector is missing).
    connector_registry = ConnectorRegistry()
    for cfg_name, cfg in connector_configs.items():
        connector_registry.register(cfg_name, build_connector(cfg))
```

(Adapt to the actual variable names in main.py.)

- [ ] **Step 4: Run tests**

Run: `cd kc-supervisor && uv run pytest tests/test_main_restart_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/main.py kc-supervisor/tests/test_main_restart_hooks.py
git commit -m "fix(kc-supervisor): always construct ConnectorRegistry at boot (Phase 2 prereq)"
```

---

## Part 8 — CLI helper for channel_routing

### Task 8.1: Add `channel-routing` CLI subcommand

**Files:**
- Modify: existing entrypoint (look for `kc-supervisor/pyproject.toml` `[project.scripts]` to find it; likely `kc_supervisor/main.py` or `kc_supervisor/cli.py`)
- Test: `kc-supervisor/tests/test_channel_routing_cli.py` (new)

- [ ] **Step 1: Locate the CLI entrypoint**

Run: `grep -rn "argparse\|click\|typer" kc-supervisor/src/kc_supervisor/main.py kc-supervisor/pyproject.toml | head -20`

Identify the framework. The plan assumes `argparse` (most common for kc-supervisor based on style); adjust subcommand wiring to match.

- [ ] **Step 2: Write the failing test**

Create `kc-supervisor/tests/test_channel_routing_cli.py`:

```python
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

def test_channel_routing_add_writes_row(tmp_path: Path):
    db = tmp_path / "kc.db"
    # Seed schema.
    from kc_supervisor.storage import Storage
    Storage(db).init()
    # Run CLI: kc-supervisor channel-routing add --db <path> telegram 8627206839
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "add",
         "--db", str(db), "telegram", "8627206839"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    routing = Storage(db).get_channel_routing("telegram")
    assert routing == {"default_chat_id": "8627206839", "enabled": 1}


def test_channel_routing_list_prints_entries(tmp_path: Path):
    db = tmp_path / "kc.db"
    from kc_supervisor.storage import Storage
    s = Storage(db); s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    s.upsert_channel_routing("imessage", "I", enabled=0)
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "list", "--db", str(db)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "telegram" in result.stdout
    assert "imessage" in result.stdout


def test_channel_routing_disable_flips_enabled(tmp_path: Path):
    db = tmp_path / "kc.db"
    from kc_supervisor.storage import Storage
    s = Storage(db); s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "disable",
         "--db", str(db), "telegram"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    routing = Storage(db).get_channel_routing("telegram")
    assert routing["enabled"] == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_channel_routing_cli.py -v`
Expected: FAIL — subcommand doesn't exist.

- [ ] **Step 4: Add the subcommand**

If the existing `main.py` doesn't have an argparse CLI, add a minimal one. If it does, slot the subcommand alongside existing ones.

Sketch (adapt to the actual entrypoint structure):

```python
# In whatever module hosts the argparse top-level parser:

def _add_channel_routing_subcommands(subparsers) -> None:
    cr = subparsers.add_parser(
        "channel-routing",
        help="Manage cross-channel allowlist for scheduled reminders",
    )
    cr_sub = cr.add_subparsers(dest="cr_action", required=True)

    add = cr_sub.add_parser("add", help="Add or replace a routing entry")
    add.add_argument("--db", required=True)
    add.add_argument("channel", choices=["telegram", "dashboard", "imessage"])
    add.add_argument("default_chat_id")
    add.set_defaults(func=_cmd_channel_routing_add)

    lst = cr_sub.add_parser("list", help="List routing entries")
    lst.add_argument("--db", required=True)
    lst.set_defaults(func=_cmd_channel_routing_list)

    dis = cr_sub.add_parser("disable", help="Disable a routing entry without deleting it")
    dis.add_argument("--db", required=True)
    dis.add_argument("channel")
    dis.set_defaults(func=_cmd_channel_routing_disable)


def _cmd_channel_routing_add(args) -> int:
    from kc_supervisor.storage import Storage
    Storage(args.db).upsert_channel_routing(args.channel, args.default_chat_id, enabled=1)
    print(f"OK: {args.channel} -> {args.default_chat_id} (enabled)")
    return 0


def _cmd_channel_routing_list(args) -> int:
    from kc_supervisor.storage import Storage
    rows = Storage(args.db).list_channel_routing()
    if not rows:
        print("(no routing entries)")
        return 0
    for r in rows:
        flag = "enabled" if r["enabled"] else "disabled"
        print(f"{r['channel']:12s} {r['default_chat_id']:20s} {flag}")
    return 0


def _cmd_channel_routing_disable(args) -> int:
    from kc_supervisor.storage import Storage
    s = Storage(args.db)
    cur = s.get_channel_routing(args.channel)
    if cur is None:
        print(f"ERROR: no routing entry for {args.channel!r}", flush=True)
        return 2
    s.upsert_channel_routing(args.channel, cur["default_chat_id"], enabled=0)
    print(f"OK: {args.channel} disabled")
    return 0
```

Wire `_add_channel_routing_subcommands(subparsers)` into the existing parser, and ensure `python -m kc_supervisor` dispatches to `args.func(args)` if present.

If kc-supervisor doesn't currently support `python -m kc_supervisor`, add a `__main__.py`:

```python
# kc-supervisor/src/kc_supervisor/__main__.py
from kc_supervisor.main import _cli_main  # or the function name actually used
import sys
sys.exit(_cli_main(sys.argv[1:]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_channel_routing_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/ kc-supervisor/tests/test_channel_routing_cli.py
git commit -m "feat(kc-supervisor): channel-routing CLI subcommand (add/list/disable)"
```

---

### Task 8.2: Seed Sammy's telegram routing entry

**Files:**
- One-shot command run against the production DB.

- [ ] **Step 1: Locate the production DB path**

Run: `grep -rn "kc\.db\|kc_supervisor.*db" kc-supervisor/src/kc_supervisor/main.py | head -5`

Confirm where production reads the SQLite path from (env var, config file, or hardcoded path).

- [ ] **Step 2: Run the seed command**

Replace `<KC_DB_PATH>` with the actual production path:

```bash
cd kc-supervisor && uv run python -m kc_supervisor channel-routing add --db <KC_DB_PATH> telegram 8627206839
```

Expected output: `OK: telegram -> 8627206839 (enabled)`

Verify:
```bash
cd kc-supervisor && uv run python -m kc_supervisor channel-routing list --db <KC_DB_PATH>
```

Expected: a line starting with `telegram` showing `8627206839 enabled`.

- [ ] **Step 3: Document the seed step in SMOKE doc (Task 10.2)**

(No commit here — this is operational, not code.)

---

## Part 9 — End-to-end integration test

### Task 9.1: One end-to-end test for cross-channel literal

**Files:**
- Test: `kc-supervisor/tests/test_phase2_integration.py` (new)

- [ ] **Step 1: Write the test**

Create `kc-supervisor/tests/test_phase2_integration.py`:

```python
"""End-to-end Phase 2: schedule cross-channel + agent_phrased, fire, verify dispatch."""
from __future__ import annotations
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from kc_core.messages import AssistantMessage
from kc_supervisor.storage import Storage
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.runner import ReminderRunner


def _setup(tmp_path: Path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cm = ConversationManager(s)
    return s, cm


def test_e2e_literal_cross_channel_dashboard_to_telegram(tmp_path):
    s, cm = _setup(tmp_path)
    sched_cid = cm.start(agent="kona", channel="dashboard")

    # Build runner + service.
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")

    # Schedule literal cross-channel reminder.
    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=sched_cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="literal",
    )
    job_id = out["id"]

    # Fire manually (bypass APS scheduling delay).
    runner.fire(job_id)

    # Connector got the literal payload with prefix.
    connector.send.assert_called_once()
    chat_id, text = connector.send.call_args.args
    assert chat_id == "8627206839"
    assert text == "⏰ dinner"

    # Persisted to a conversation that was created on demand for telegram:8627206839.
    dest_cid = s.get_conv_for_chat("telegram", "8627206839", "kona")
    assert dest_cid is not None and dest_cid != sched_cid
    msgs = cm.list_messages(dest_cid)
    assert len(msgs) == 1
    assert isinstance(msgs[0], AssistantMessage)
    assert msgs[0].content == "⏰ dinner"

    # Row marked done.
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"


def test_e2e_agent_phrased_cross_channel(tmp_path):
    s, cm = _setup(tmp_path)
    sched_cid = cm.start(agent="kona", channel="dashboard")

    # Build a fake agent registry whose core_agent yields a Complete frame.
    class FakeToolRegistry:
        def names(self): return []
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry()
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        class FakeComplete:
            reply = AssistantMessage(content="Hey, dinner reminder!")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")

    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner trigger description",
        conversation_id=sched_cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    runner.fire(out["id"])

    # Composed text dispatched (no PREFIX).
    text = connector.send.call_args.args[1]
    assert text == "Hey, dinner reminder!"

    # Destination conversation has the composed message, NOT the trigger description.
    dest_cid = s.get_conv_for_chat("telegram", "8627206839", "kona")
    msgs = cm.list_messages(dest_cid)
    assert len(msgs) == 1
    assert msgs[0].content == "Hey, dinner reminder!"
```

- [ ] **Step 2: Run the integration test**

Run: `cd kc-supervisor && uv run pytest tests/test_phase2_integration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 3: Commit**

```bash
git add kc-supervisor/tests/test_phase2_integration.py
git commit -m "test(kc-supervisor): Phase 2 end-to-end integration (cross-channel literal + agent_phrased)"
```

---

## Part 10 — Verification + manual smoke gates

### Task 10.1: Run the full kc-supervisor test suite

**Files:** none modified, just verification.

- [ ] **Step 1: Run everything**

Run: `cd kc-supervisor && uv run pytest -v`

Expected: ALL PASS. Phase 1's baseline was 253 tests; Phase 2 adds ~50, so target is ~300+ tests, all green.

- [ ] **Step 2: If anything fails, fix the underlying issue (do NOT skip)**

Common failure modes:
- A Phase 1 test that called `list_reminders` without `scope` and expected conversation-scoped results — fix the test to pass `scope="conversation"` explicitly (the default flipped). The test expectation is the bug, not the new default.
- A test that constructed `ReminderRunner` without `agent_registry` — already handled by making the arg optional.
- A test that constructed `ScheduleService.schedule_one_shot` with positional args — should still work; the new args are kwargs with defaults.

- [ ] **Step 3: When green, commit any test fixups**

```bash
git add -A
git commit -m "fix(kc-supervisor): adjust Phase 1 tests for Phase 2 default changes"
```

(Skip this commit if no fixups were needed.)

---

### Task 10.2: Update SMOKE doc with Phase 2 manual gates

**Files:**
- Modify: `docs/SMOKE.md` or wherever Phase 1's smoke gates live (run `find docs -name "SMOKE*"` to locate).

- [ ] **Step 1: Find the SMOKE doc**

Run: `find docs -iname "smoke*" -o -iname "*smoke*.md" | head -5`

Pick the one that holds Phase 1 reminder smoke gates.

- [ ] **Step 2: Append Phase 2 gates**

Add a section:

```markdown
## Reminders Phase 2 — manual smoke gates (post-merge)

Pre-req: `channel_routing` table seeded with `telegram → 8627206839 (enabled)`.
Run via: `python -m kc_supervisor channel-routing add --db <KC_DB_PATH> telegram 8627206839`

1. **Literal cross-channel dashboard → telegram.**
   In the dashboard chat: "Kona, set a reminder on Telegram in 2 minutes saying 'phase 2 smoke 1'".
   Wait 2 minutes. Verify: a Telegram message arrives reading "⏰ phase 2 smoke 1".

2. **Agent-phrased same-channel on dashboard.**
   In the dashboard chat: "Kona, in 2 minutes use agent-phrased mode to remind me about my standup notes".
   Wait 2 minutes. Verify: a freshly composed message appears in the dashboard (NOT prefixed with ⏰), with text the model wrote at fire time. Run the same scenario twice — composed text should differ across runs (it's a new model call each time).

3. **Agent-phrased cross-channel telegram → dashboard.**
   In the Telegram chat: "Kona, agent-phrase a reminder to my dashboard in 2 minutes about checking the build".
   Wait 2 minutes. Verify: a composed message appears in the dashboard chat thread (NOT in the Telegram chat), without ⏰ prefix.

4. **Disabled-channel safety.**
   Run: `python -m kc_supervisor channel-routing disable --db <KC_DB_PATH> telegram`.
   Schedule a NEW cross-channel reminder to telegram from the dashboard. Verify: the agent surfaces an error like "channel 'telegram' is disabled". Re-enable: `python -m kc_supervisor channel-routing add --db <KC_DB_PATH> telegram 8627206839`. Verify any in-flight reminder scheduled BEFORE the disable still fires (already-scheduled rows are immune to allowlist toggles).
```

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: SMOKE gates for reminders Phase 2"
```

---

## Self-review (run by the author of this plan; not by executing engineer)

**Spec coverage:**

- ✅ `mode` column ALTER → Task 1.1
- ✅ `channel_routing` table → Task 1.2
- ✅ Storage methods (`get_channel_routing`, `upsert_channel_routing`, `list_channel_routing`) → Task 1.2
- ✅ `add_scheduled_job` accepts `mode` → Task 1.3
- ✅ `ConversationManager.get_or_create` → Task 2.1
- ✅ `target_channel` + `mode` on `schedule_one_shot` → Task 3.1
- ✅ `target_channel` + `mode` on `schedule_cron` → Task 3.2
- ✅ `scope` on `list_reminders`, default `"user"`, view includes `channel` + `mode` → Task 3.3
- ✅ `scope` on `cancel_reminder`, default `"user"` → Task 3.4
- ✅ Tool surface updates (4 tools) → Tasks 4.1, 4.2
- ✅ Runner: `dest_conv_id` resolution → Task 5.1
- ✅ Runner: `agent_registry` plumbing → Task 6.1
- ✅ Runner: `_compose_agent_phrased` (tool stripping, system prompt addendum, failure → None) → Task 6.2
- ✅ Runner: `fire()` branches on `mode` → Task 6.3
- ✅ Always-construct `ConnectorRegistry` → Task 7.1
- ✅ CLI helper for `channel_routing` → Task 8.1
- ✅ Seed Sammy's telegram entry → Task 8.2
- ✅ End-to-end integration test → Task 9.1
- ✅ SMOKE gates → Task 10.2

**Placeholder scan:** No "TBD"/"TODO"/"add appropriate error handling" — every step has concrete code. Two task callouts ("locate the CLI entrypoint", "find the SMOKE doc") are intentionally exploratory because the codebase varies; both have grep commands to anchor the engineer.

**Type/signature consistency:**

- `mode` is always a string defaulting to `"literal"`. ✓
- `target_channel` is always a string defaulting to `"current"`. ✓
- `scope` is always a string defaulting to `"user"`. ✓
- `get_or_create` signature: `(*, channel: str, chat_id: str, agent: str) -> int`. Used identically in runner and inbound. ✓
- `_compose_agent_phrased` signature: `(row: dict, *, dest_conv_id: int) -> Optional[str]`. Caller passes both. ✓
- `ReminderRunner.__init__` adds `agent_registry: Optional[Any] = None` — `main.py` always passes it; tests pass it when needed. ✓
- `_filter_tools` exposes `.names()`, `.to_openai_schema()`, `.invoke(name, args)` — matches the three methods kc-core's agent calls (verified at `kc-core/src/kc_core/agent.py:59,82,113,135,140,170`). ✓

**Things the planner deliberately left for the executing engineer:**

1. Exact CLI entrypoint discovery (Task 8.1 step 1 — `argparse` assumed; adapt to actual framework).
2. `main.py` connector-registry fix exact lines (Task 7.1 step 1 — locate the gating block).
3. SMOKE doc location (Task 10.2 step 1).
4. Production DB path (Task 8.2 step 1).

These are low-risk adaptations. The HOW for each is shown; the WHERE is found by grep.

**Risks called out for the executing engineer:**

- The fake `Complete` frame in agent-phrased tests assumes the real `Complete` frame has a `.reply` attribute holding an `AssistantMessage`. Verified at `inbound.py:130-143`. If kc-core changes the frame shape, tests break loudly — desired behavior.
- `_filter_tools` proxies tool methods; if kc-core's `AuditingToolRegistry` adds new methods that the proxy doesn't forward, agent-phrased turns may fail in surprising ways. Mitigation: tests cover the happy path; production breakage will surface immediately on the first agent-phrased fire.
- The CLI tests use `subprocess.run` with `python -m kc_supervisor`. If the package doesn't expose a `__main__`, Task 8.1 step 4 adds one.

---

## Plan complete.

**Plan saved to:** `docs/superpowers/plans/2026-05-09-reminders-phase2.md`

**Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using the executing-plans skill, with checkpoints for review.

Which approach?
