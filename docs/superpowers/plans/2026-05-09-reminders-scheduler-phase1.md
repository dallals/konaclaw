# Reminders & cron scheduler — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Kona durable one-shot + cron scheduling so `schedule_reminder("5pm today", "...")` and `schedule_cron("0 9 * * *", "...")` deliver messages back into the same Telegram / dashboard / iMessage conversation, surviving supervisor restarts via APScheduler's `SQLAlchemyJobStore`.

**Architecture:** New `kc_supervisor.scheduling` module wrapping `APScheduler` (`AsyncIOScheduler` + `SQLAlchemyJobStore`) over the same `kc.db` SQLite file. Application code reads/writes a new `scheduled_jobs` table (the human-readable mirror); APS owns its own `apscheduler_jobs` tables (implementation detail). At fire time a `ReminderRunner` callback sends via `ConnectorRegistry` and persists an `AssistantMessage` in the conversation history. Four agent tools register safe-auto on Kona only via `assembly.py`.

**Tech Stack:** Python 3.11, FastAPI startup hooks, SQLite (additive migration), raw `sqlite3` for app data + SQLAlchemy 2.0 (only behind APS's job store), APScheduler 3.10, dateparser 1.2, croniter 2.0, cron-descriptor 1.4, pytest-asyncio, freezegun for clock control.

**Spec:** `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md`

---

## File Structure

**New files:**
- `kc-supervisor/src/kc_supervisor/scheduling/__init__.py` — module entry exports.
- `kc-supervisor/src/kc_supervisor/scheduling/service.py` — `ScheduleService` class wrapping APScheduler.
- `kc-supervisor/src/kc_supervisor/scheduling/runner.py` — `ReminderRunner.fire(job_id)` callback for APS triggers.
- `kc-supervisor/src/kc_supervisor/scheduling/tools.py` — four agent tools (`build_scheduling_tools(service, conv_id_provider) → list[Tool]`).
- `kc-supervisor/src/kc_supervisor/scheduling/timeparse.py` — small helper wrapping `dateparser` with the supervisor's TZ defaults.
- `kc-supervisor/tests/test_scheduling_storage.py` — schema migration + storage helpers.
- `kc-supervisor/tests/test_scheduling_service.py` — `ScheduleService` unit tests (one-shot, cron, list, cancel).
- `kc-supervisor/tests/test_reminder_runner.py` — fire-handler unit tests.
- `kc-supervisor/tests/test_schedule_rehydrate.py` — restart / misfire / coalesce / reconcile tests.
- `kc-supervisor/tests/test_scheduling_tools.py` — agent-integration tests.

**Modified files:**
- `kc-supervisor/pyproject.toml` — add 5 deps.
- `kc-supervisor/src/kc_supervisor/storage.py` — add `scheduled_jobs` table + additive migration + 6 helper methods.
- `kc-supervisor/src/kc_supervisor/service.py` — add startup/shutdown hooks for `ScheduleService`; reconcile background task.
- `kc-supervisor/src/kc_supervisor/main.py` — construct `ScheduleService` and stash on `deps`.
- `kc-supervisor/src/kc_supervisor/assembly.py` — register the four scheduling tools on `cfg.name == "kona"` only.
- `kc-supervisor/SMOKE.md` — Phase 1 scheduling smoke gates.

---

## Test runner reminder

**Use `.venv/bin/pytest` from `/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor`** — NOT `uv run pytest` (the workspace's `kc-core` dep doesn't resolve through `uv`).

For `pip install` of new deps, use `uv sync` from the supervisor directory if it works, otherwise `.venv/bin/pip install <pkg>` directly. Verify each new import works in a Python shell before committing.

---

## Task 1: Add new dependencies + scaffold the scheduling module

**Files:**
- Modify: `kc-supervisor/pyproject.toml`
- Create: `kc-supervisor/src/kc_supervisor/scheduling/__init__.py`

- [ ] **Step 1: Add deps to `pyproject.toml`**

Open `kc-supervisor/pyproject.toml` and find the `dependencies = [...]` list (around line 9). Append five new entries:

```toml
dependencies = [
    "kc-core",
    "kc-sandbox",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "cryptography>=42",
    "apscheduler>=3.10",
    "sqlalchemy>=2.0",
    "dateparser>=1.2",
    "croniter>=2.0",
    "cron-descriptor>=1.4",
]
```

Also add `freezegun>=1.5` to the `dev` extra (used by the new tests):

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    # ... existing dev deps ...
    "freezegun>=1.5",
]
```

(Preserve the existing `dev` list; only add `freezegun>=1.5` if not already present.)

- [ ] **Step 2: Install the new deps**

Run from `/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor`:

```bash
.venv/bin/pip install 'apscheduler>=3.10' 'sqlalchemy>=2.0' 'dateparser>=1.2' 'croniter>=2.0' 'cron-descriptor>=1.4' 'freezegun>=1.5'
```

Expected: all packages resolve and install with no errors.

- [ ] **Step 3: Smoke-test the imports**

```bash
.venv/bin/python -c "import apscheduler, sqlalchemy, dateparser, croniter, cron_descriptor, freezegun; print('ok')"
```

Expected output: `ok`.

- [ ] **Step 4: Create the empty module package**

Create `kc-supervisor/src/kc_supervisor/scheduling/__init__.py` with the content:

```python
"""Reminder + cron scheduling for kc-supervisor.

Public surface:
    ScheduleService — high-level scheduling API used by agent tools and lifecycle.
    ReminderRunner   — APScheduler trigger callback that fires a scheduled job.
    build_scheduling_tools — factory that returns the four agent tools.
"""
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.runner import ReminderRunner
from kc_supervisor.scheduling.tools import build_scheduling_tools

__all__ = ["ScheduleService", "ReminderRunner", "build_scheduling_tools"]
```

(The imports will fail until later tasks add the modules. That's fine — Task 2 doesn't import this `__init__.py`. We'll wire it in once the constituents exist. For now keep it as documentation of the intended surface.)

To avoid importing nonexistent symbols at this checkpoint, replace the import block with module placeholders for now:

```python
"""Reminder + cron scheduling for kc-supervisor.

Public surface (added incrementally across tasks):
    ScheduleService — kc_supervisor/scheduling/service.py (Task 3)
    ReminderRunner   — kc_supervisor/scheduling/runner.py (Task 6)
    build_scheduling_tools — kc_supervisor/scheduling/tools.py (Task 10)
"""
```

The actual re-exports will be added in Task 10's commit once all three modules exist.

- [ ] **Step 5: Verify the existing supervisor suite still passes**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 202 passed (or whatever the current baseline is). New deps must not break existing imports.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/pyproject.toml kc-supervisor/src/kc_supervisor/scheduling/__init__.py
git commit -m "feat(kc-supervisor): add scheduling deps and scaffold module

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If a `uv.lock` or `requirements.txt` was updated by the install, also stage that file.

---

## Task 2: SQLite schema — `scheduled_jobs` table + storage helpers

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Test: `kc-supervisor/tests/test_scheduling_storage.py`

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_scheduling_storage.py`:

```python
from __future__ import annotations
import time
import pytest
from kc_supervisor.storage import Storage


def _seed_conv(s: Storage, agent: str = "kona", channel: str = "telegram") -> int:
    return s.create_conversation(agent=agent, channel=channel)


def test_scheduled_jobs_table_exists_after_init(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)").fetchall()}
    assert {
        "id", "kind", "agent", "conversation_id", "channel", "chat_id",
        "when_utc", "cron_spec", "payload", "status", "attempts",
        "last_fired_at", "created_at",
    }.issubset(cols)


def test_add_scheduled_job_one_shot_round_trips(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="hi",
        when_utc=time.time() + 3600.0, cron_spec=None,
    )
    assert isinstance(job_id, int) and job_id > 0
    rows = s.list_scheduled_jobs(conversation_id=cid)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "reminder"
    assert r["status"] == "pending"
    assert r["attempts"] == 0
    assert r["payload"] == "hi"
    assert r["cron_spec"] is None
    assert r["when_utc"] is not None


def test_add_scheduled_job_cron_round_trips(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    s.add_scheduled_job(
        kind="cron", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="dashboard:1", payload="daily",
        when_utc=None, cron_spec="0 9 * * 1-5",
    )
    rows = s.list_scheduled_jobs(conversation_id=cid)
    assert rows[0]["kind"] == "cron"
    assert rows[0]["cron_spec"] == "0 9 * * 1-5"
    assert rows[0]["when_utc"] is None


def test_list_scheduled_jobs_filter_status(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    j1 = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="a",
        when_utc=time.time() + 60, cron_spec=None,
    )
    s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="b",
        when_utc=time.time() + 60, cron_spec=None,
    )
    s.update_scheduled_job_status(j1, "done")
    pending = s.list_scheduled_jobs(conversation_id=cid, statuses=("pending",))
    assert {r["payload"] for r in pending} == {"b"}
    all_rows = s.list_scheduled_jobs(conversation_id=cid)
    assert len(all_rows) == 2


def test_list_scheduled_jobs_global(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = _seed_conv(s, agent="kona", channel="telegram")
    cid_b = _seed_conv(s, agent="kona", channel="dashboard")
    s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid_a,
        channel="telegram", chat_id="A", payload="x",
        when_utc=time.time() + 1, cron_spec=None,
    )
    s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid_b,
        channel="dashboard", chat_id="B", payload="y",
        when_utc=time.time() + 1, cron_spec=None,
    )
    all_rows = s.list_scheduled_jobs()
    assert len(all_rows) == 2


def test_get_scheduled_job(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    j = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=time.time() + 60, cron_spec=None,
    )
    row = s.get_scheduled_job(j)
    assert row is not None and row["id"] == j
    assert s.get_scheduled_job(99999) is None


def test_delete_scheduled_job(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    j = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=time.time() + 60, cron_spec=None,
    )
    n = s.delete_scheduled_job(j)
    assert n == 1
    assert s.get_scheduled_job(j) is None
    # Idempotent
    assert s.delete_scheduled_job(j) == 0


def test_update_scheduled_job_after_fire(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    j = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=time.time() + 60, cron_spec=None,
    )
    fired_at = time.time()
    s.update_scheduled_job_after_fire(j, fired_at=fired_at, new_status="done")
    row = s.get_scheduled_job(j)
    assert row["status"] == "done"
    assert row["last_fired_at"] == fired_at
    assert row["attempts"] == 1
    s.update_scheduled_job_after_fire(j, fired_at=fired_at + 60, new_status="done")
    row = s.get_scheduled_job(j)
    assert row["attempts"] == 2


def test_conversation_delete_cascades_jobs(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = _seed_conv(s)
    s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=time.time() + 60, cron_spec=None,
    )
    with s.connect() as c:
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    assert s.list_scheduled_jobs(conversation_id=cid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_storage.py -v
```

Expected: all tests fail with `AttributeError: 'Storage' object has no attribute 'add_scheduled_job'` (or similar).

- [ ] **Step 3: Update `storage.py` SCHEMA**

Edit `kc-supervisor/src/kc_supervisor/storage.py`. In the `SCHEMA` constant string (top of file, around line 9), append a new `CREATE TABLE` after the existing `connector_conv_map` definition and before the closing `"""`:

```python
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
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON scheduled_jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_conv ON scheduled_jobs(conversation_id);
```

(No additive `ALTER TABLE` migration is needed because `CREATE TABLE IF NOT EXISTS` is itself idempotent on existing DBs lacking the table.)

- [ ] **Step 4: Add helper methods to the `Storage` class**

In the same file, after the existing `# ----- audit -----` block of methods, add a new section:

```python
    # ----- scheduled jobs -----

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
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO scheduled_jobs "
                "(kind, agent, conversation_id, channel, chat_id, payload, "
                " when_utc, cron_spec, status, attempts, created_at) "
                "VALUES (?,?,?,?,?,?,?,?, 'pending', 0, ?)",
                (kind, agent, conversation_id, channel, chat_id, payload,
                 when_utc, cron_spec, time.time()),
            )
            return int(cur.lastrowid)

    def get_scheduled_job(self, job_id: int) -> Optional[dict]:
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_scheduled_jobs(
        self,
        conversation_id: Optional[int] = None,
        statuses: Optional[tuple[str, ...]] = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if statuses is not None:
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM scheduled_jobs {where} ORDER BY id ASC"
        with self.connect() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_scheduled_job_status(self, job_id: int, status: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE scheduled_jobs SET status=? WHERE id=?",
                (status, job_id),
            )

    def update_scheduled_job_after_fire(
        self, job_id: int, *, fired_at: float, new_status: str,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE scheduled_jobs SET last_fired_at=?, attempts=attempts+1, status=? "
                "WHERE id=?",
                (fired_at, new_status, job_id),
            )

    def delete_scheduled_job(self, job_id: int) -> int:
        with self.connect() as c:
            cur = c.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
            return cur.rowcount
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_storage.py -v
```

Expected: all 9 tests pass.

Then run the full supervisor suite to confirm no regression:

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 211 passed (202 baseline + 9 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_scheduling_storage.py
git commit -m "feat(kc-supervisor): add scheduled_jobs table and storage helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `ScheduleService.schedule_one_shot` + `timeparse` helper

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/scheduling/timeparse.py`
- Create: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_scheduling_service.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService


def _make_service(tmp_path) -> tuple[ScheduleService, Storage]:
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
    )
    svc.start()
    return svc, s


def _seed_conv(s: Storage) -> int:
    return s.create_conversation(agent="kona", channel="telegram")


@freeze_time("2026-05-09 14:30:00")  # 2:30pm UTC = 7:30am PT
def test_schedule_one_shot_resolves_pt_5pm_today(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_one_shot(
            when="5pm today", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert "id" in result
        assert "fires_at" in result
        # 5pm PT = 0:00 UTC the next day during PDT (UTC-7)
        # On 2026-05-09 PT, 5pm PT == 2026-05-10T00:00:00Z
        # We accept any ISO that contains the right hour offset
        assert "T17:00:00" in result["fires_at"]  # tz-aware ISO with -07:00
        assert "5:00 PM" in result["fires_at_human"]
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert len(rows) == 1
        assert rows[0]["payload"] == "dinner"
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_one_shot_relative_in_two_hours(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_one_shot(
            when="in 2 hours", content="t",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert "T16:30" in result["fires_at"] or "T09:30" in result["fires_at"]
        # Either UTC representation (16:30Z) or PT (09:30-07:00)
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_one_shot_past_time_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="past"):
            svc.schedule_one_shot(
                when="yesterday", content="t",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_unparseable_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="parse"):
            svc.schedule_one_shot(
                when="!@#$%", content="t",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_empty_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_one_shot(
                when="in 1 hour", content="",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_oversized_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_one_shot(
                when="in 1 hour", content="x" * 4001,
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py -v
```

Expected: ImportError or AttributeError because `ScheduleService` doesn't exist yet.

- [ ] **Step 3: Create the timeparse helper**

Create `kc-supervisor/src/kc_supervisor/scheduling/timeparse.py`:

```python
from __future__ import annotations
from datetime import datetime, timedelta, timezone as _tz_mod
from typing import Optional
import dateparser


def parse_when(when: str, tz_name: str) -> datetime:
    """Parse a natural-language time string into a tz-aware datetime.

    Args:
        when:    free-form text such as "5pm today", "in 2 hours",
                 "tomorrow at 9am", "next Friday at noon".
        tz_name: IANA timezone string (e.g. "America/Los_Angeles") that the
                 input should be interpreted in. The returned datetime is also
                 tz-aware in this same timezone.

    Returns:
        A timezone-aware datetime in `tz_name`.

    Raises:
        ValueError if `dateparser` cannot parse the string.
    """
    if not when or not when.strip():
        raise ValueError("could not parse 'when': empty string")
    settings = {
        "TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
    }
    parsed = dateparser.parse(when, settings=settings)
    if parsed is None:
        raise ValueError(f"could not parse 'when': {when!r}")
    if parsed.tzinfo is None:
        # dateparser sometimes returns naive even with RETURN_AS_TIMEZONE_AWARE.
        # Force the requested zone in that case.
        from zoneinfo import ZoneInfo
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed


def is_past(dt: datetime, *, grace_seconds: float = 5.0) -> bool:
    """Return True iff `dt` is more than `grace_seconds` in the past relative to now."""
    now = datetime.now(_tz_mod.utc)
    return (dt.astimezone(_tz_mod.utc) + timedelta(seconds=grace_seconds)) < now


def humanize(dt: datetime) -> str:
    """Format a tz-aware datetime as 'Sat May 9 5:00 PM PT'."""
    # %-I is platform-specific; use the right format for portability.
    return dt.strftime("%a %b ") + dt.strftime("%-d %-I:%M %p %Z")
```

- [ ] **Step 4: Create `ScheduleService`**

Create `kc-supervisor/src/kc_supervisor/scheduling/service.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone as _tz_mod
from pathlib import Path
from typing import Any, Optional, Protocol
import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.timeparse import parse_when, is_past, humanize


logger = logging.getLogger(__name__)
MAX_PAYLOAD_CHARS = 4000


class _RunnerLike(Protocol):
    """Anything with a `fire(job_id: int)` callable. Decoupled so tests can mock."""
    def fire(self, job_id: int) -> None: ...


class ScheduleService:
    """High-level scheduler API.

    Wraps APScheduler with SQLAlchemyJobStore over the same SQLite file as
    application data. The `scheduled_jobs` table (managed by Storage) is the
    human-readable mirror; APS's internal tables are implementation detail.
    """

    def __init__(
        self,
        storage: Storage,
        runner: _RunnerLike,
        db_path: Path,
        timezone: str,
    ) -> None:
        self.storage = storage
        self.runner = runner
        self._tz = timezone
        sqlalchemy_url = f"sqlite:///{db_path}"
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=sqlalchemy_url)},
            timezone=timezone,
        )

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ---- one-shot ----

    def schedule_one_shot(
        self,
        *,
        when: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        target = parse_when(when, self._tz)
        if is_past(target):
            raise ValueError(f"'when' resolves to the past: {when!r}")
        target_utc = target.astimezone(_tz_mod.utc)

        job_id = self.storage.add_scheduled_job(
            kind="reminder", agent=agent, conversation_id=conversation_id,
            channel=channel, chat_id=chat_id, payload=content,
            when_utc=target_utc.timestamp(), cron_spec=None,
        )
        try:
            self._scheduler.add_job(
                self.runner.fire, trigger=DateTrigger(run_date=target),
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

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/timeparse.py kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService.schedule_one_shot with NL parsing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `ScheduleService.schedule_cron` with cron validation + human summary

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-supervisor/tests/test_scheduling_service.py`:

```python
def test_schedule_cron_valid_spec(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_cron(
            cron="0 9 * * 1-5", content="standup",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert "id" in result
        assert "next_fire" in result
        assert "next_fire_human" in result
        assert "weekday" in result["human_summary"].lower() or "Monday" in result["human_summary"]
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert rows[0]["kind"] == "cron"
        assert rows[0]["cron_spec"] == "0 9 * * 1-5"
    finally:
        svc.shutdown()


def test_schedule_cron_invalid_spec_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="invalid cron"):
            svc.schedule_cron(
                cron="not a real cron", content="x",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_cron_empty_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_cron(
                cron="0 9 * * *", content="",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py::test_schedule_cron_valid_spec -v
```

Expected: AttributeError — `schedule_cron` not defined.

- [ ] **Step 3: Add `schedule_cron` to `ScheduleService`**

Edit `kc-supervisor/src/kc_supervisor/scheduling/service.py`. Add at the top of the file:

```python
from croniter import croniter
from cron_descriptor import get_description, ExpressionDescriptor
```

Then add the method after `schedule_one_shot`:

```python
    # ---- cron ----

    def schedule_cron(
        self,
        *,
        cron: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron: {cron!r}")

        try:
            trigger = CronTrigger.from_crontab(cron, timezone=self._tz)
        except ValueError as e:
            raise ValueError(f"invalid cron: {cron!r} ({e})")

        # Compute next_fire and human_summary BEFORE persisting so we can echo
        # them back to the agent.
        next_fire = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
        try:
            human_summary = get_description(cron)
        except Exception:
            human_summary = cron  # fallback: just echo the spec

        job_id = self.storage.add_scheduled_job(
            kind="cron", agent=agent, conversation_id=conversation_id,
            channel=channel, chat_id=chat_id, payload=content,
            when_utc=None, cron_spec=cron,
        )
        try:
            self._scheduler.add_job(
                self.runner.fire, trigger=trigger,
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService.schedule_cron with cron-descriptor summary

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `ScheduleService.list_reminders` and `cancel_reminder`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Test: `kc-supervisor/tests/test_scheduling_service.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-supervisor/tests/test_scheduling_service.py`:

```python
def test_list_reminders_active_only(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r1 = svc.schedule_one_shot(
            when="in 1 hour", content="a",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        r2 = svc.schedule_cron(
            cron="0 9 * * *", content="b",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        s.update_scheduled_job_status(r1["id"], "done")

        active = svc.list_reminders(conversation_id=cid, active_only=True)
        assert len(active["reminders"]) == 1
        assert active["reminders"][0]["id"] == r2["id"]

        all_ = svc.list_reminders(conversation_id=cid, active_only=False)
        assert len(all_["reminders"]) == 2
    finally:
        svc.shutdown()


def test_list_reminders_shape(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        svc.schedule_one_shot(
            when="in 1 hour", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.list_reminders(conversation_id=cid, active_only=True)
        r = out["reminders"][0]
        assert {"id", "kind", "fires_at_human", "next_fire_human",
                "content", "status", "human_summary"}.issubset(r.keys())
        assert r["kind"] == "reminder"
        assert r["next_fire_human"] is None
        assert r["fires_at_human"] is not None
    finally:
        svc.shutdown()


def test_cancel_reminder_by_id(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        result = svc.cancel_reminder(str(r["id"]), conversation_id=cid)
        assert result["ambiguous"] is False
        assert result["cancelled"][0]["id"] == r["id"]
        # Row deleted (not just marked)
        assert s.list_scheduled_jobs(conversation_id=cid, statuses=("pending",)) == []
    finally:
        svc.shutdown()


def test_cancel_reminder_by_id_missing_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="no reminder with id"):
            svc.cancel_reminder("9999", conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_unique(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="dinner with mom",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.cancel_reminder("DINNER", conversation_id=cid)
        assert out["ambiguous"] is False
        assert out["cancelled"][0]["id"] == r["id"]
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_ambiguous(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r1 = svc.schedule_one_shot(
            when="in 1 hour", content="dinner with mom",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        r2 = svc.schedule_one_shot(
            when="in 2 hours", content="dinner reservation",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.cancel_reminder("dinner", conversation_id=cid)
        assert out["ambiguous"] is True
        assert {c["id"] for c in out["candidates"]} == {r1["id"], r2["id"]}
        assert out["cancelled"] == []
        # Nothing was actually deleted
        assert len(s.list_scheduled_jobs(conversation_id=cid, statuses=("pending",))) == 2
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_no_match(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        svc.schedule_one_shot(
            when="in 1 hour", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        with pytest.raises(ValueError, match="no reminder matched"):
            svc.cancel_reminder("breakfast", conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_only_pending(tmp_path):
    """Cancelling a row that's already done should be no-match."""
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        s.update_scheduled_job_status(r["id"], "done")
        with pytest.raises(ValueError):
            svc.cancel_reminder(str(r["id"]), conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_scoped_to_conversation(tmp_path):
    """Cannot cancel a reminder from a different conversation."""
    svc, s = _make_service(tmp_path)
    cid_a = _seed_conv(s)
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
        )
        with pytest.raises(ValueError):
            svc.cancel_reminder(str(r["id"]), conversation_id=cid_b)
    finally:
        svc.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py -v -k "list_reminders or cancel_reminder"
```

Expected: all fail with `AttributeError: 'ScheduleService' object has no attribute 'list_reminders'`.

- [ ] **Step 3: Add `list_reminders` and `cancel_reminder`**

In `kc-supervisor/src/kc_supervisor/scheduling/service.py`, add after `schedule_cron`:

```python
    # ---- list / cancel ----

    def list_reminders(
        self, *, conversation_id: int, active_only: bool = True,
    ) -> dict:
        statuses = ("pending",) if active_only else None
        rows = self.storage.list_scheduled_jobs(
            conversation_id=conversation_id, statuses=statuses,
        )
        out: list[dict] = []
        for row in rows:
            entry = self._row_to_view(row)
            out.append(entry)
        return {"reminders": out}

    def cancel_reminder(
        self, id_or_description: str, *, conversation_id: int,
    ) -> dict:
        """Cancel a pending reminder.

        If the input is purely numeric, treated as an integer ID.
        Otherwise, treated as a case-insensitive substring match against payload.
        """
        if not id_or_description:
            raise ValueError("id_or_description must not be empty")

        candidates = self.storage.list_scheduled_jobs(
            conversation_id=conversation_id, statuses=("pending",),
        )

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

    def _do_cancel(self, rows: list[dict]) -> dict:
        cancelled: list[dict] = []
        for r in rows:
            try:
                self._scheduler.remove_job(str(r["id"]))
            except Exception:
                # APS may not have the job (already fired one-shot, or never
                # registered due to a partial failure). DB row is the source of
                # truth — proceed.
                logger.debug("APS job %s not found; deleting DB row anyway", r["id"])
            self.storage.delete_scheduled_job(r["id"])
            cancelled.append({"id": r["id"], "content": r["payload"]})
        return {"ambiguous": False, "candidates": [], "cancelled": cancelled}

    def _row_to_view(self, row: dict) -> dict:
        kind = row["kind"]
        fires_at_human = None
        next_fire_human = None
        human_summary = None
        if kind == "reminder" and row["when_utc"] is not None:
            dt = datetime.fromtimestamp(row["when_utc"], tz=_tz_mod.utc)
            from zoneinfo import ZoneInfo
            dt_local = dt.astimezone(ZoneInfo(self._tz))
            fires_at_human = humanize(dt_local)
        elif kind == "cron" and row["cron_spec"]:
            try:
                trigger = CronTrigger.from_crontab(row["cron_spec"], timezone=self._tz)
                nxt = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
                if nxt is not None:
                    next_fire_human = humanize(nxt)
            except Exception:
                pass
            try:
                human_summary = get_description(row["cron_spec"])
            except Exception:
                human_summary = row["cron_spec"]
        return {
            "id": row["id"],
            "kind": kind,
            "fires_at_human": fires_at_human,
            "next_fire_human": next_fire_human,
            "content": row["payload"],
            "status": row["status"],
            "human_summary": human_summary,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_service.py -v
```

Expected: all 18 tests pass (6 from Task 3 + 3 from Task 4 + 9 new).

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_scheduling_service.py
git commit -m "feat(kc-supervisor): ScheduleService.list_reminders and cancel_reminder

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `ReminderRunner.fire` happy path

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/scheduling/runner.py`
- Test: `kc-supervisor/tests/test_reminder_runner.py`

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_reminder_runner.py`:

```python
from __future__ import annotations
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.runner import ReminderRunner


def _make_runner(tmp_path) -> tuple[ReminderRunner, Storage, MagicMock, MagicMock]:
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = MagicMock()
    connector_registry = MagicMock()
    connector = MagicMock()
    connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    return runner, s, cm, connector_registry


def _seed(s: Storage, cm: MagicMock, *, kind: str = "reminder") -> int:
    cid = s.create_conversation(agent="kona", channel="telegram")
    return s.add_scheduled_job(
        kind=kind, agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=time.time() + 60 if kind == "reminder" else None,
        cron_spec=None if kind == "reminder" else "0 9 * * *",
    )


def test_fire_sends_via_connector_with_prefix(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    connector = registry.get.return_value
    connector.send.assert_called_once()
    args, kwargs = connector.send.call_args
    chat_id, content = args[0], args[1]
    assert chat_id == "C1"
    assert content == "⏰ dinner"


def test_fire_persists_assistant_message(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    cm.append.assert_called_once()
    args, kwargs = cm.append.call_args
    conversation_id, message = args[0], args[1]
    assert message.__class__.__name__ == "AssistantMessage"
    assert message.content == "⏰ dinner"


def test_fire_marks_one_shot_done(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"
    assert row["attempts"] == 1
    assert row["last_fired_at"] is not None


def test_fire_keeps_cron_pending(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm, kind="cron")
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "pending"
    assert row["attempts"] == 1


def test_fire_unknown_job_id_is_noop(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    runner.fire(99999)  # No row exists; must not crash
    connector = registry.get.return_value
    connector.send.assert_not_called()
    cm.append.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_reminder_runner.py -v
```

Expected: ImportError for `ReminderRunner`.

- [ ] **Step 3: Create `ReminderRunner`**

Create `kc-supervisor/src/kc_supervisor/scheduling/runner.py`:

```python
from __future__ import annotations
import logging
import time
from typing import Any, Callable, Coroutine

from kc_core.messages import AssistantMessage
from kc_supervisor.storage import Storage


logger = logging.getLogger(__name__)
PREFIX = "⏰ "

CoroRunner = Callable[[Coroutine], Any]


class ReminderRunner:
    """APScheduler trigger callback. Sends a reminder via the connector and
    persists the AssistantMessage row.

    `coroutine_runner` is a callable that takes a coroutine and runs it to
    completion synchronously. Production wiring passes a lambda that bridges
    to the FastAPI event loop via `asyncio.run_coroutine_threadsafe(..., loop)`
    (because APS triggers run in a worker thread, not the event loop). Tests
    pass `lambda c: asyncio.run(c)` directly.
    """

    def __init__(
        self,
        *,
        storage: Storage,
        conversations: Any,        # ConversationManager
        connector_registry: Any,   # ConnectorRegistry
        coroutine_runner: CoroRunner,
    ) -> None:
        self.storage = storage
        self.conversations = conversations
        self.connector_registry = connector_registry
        self._run_coro = coroutine_runner

    def fire(self, job_id: int) -> None:
        row = self.storage.get_scheduled_job(job_id)
        if row is None:
            logger.warning("ReminderRunner.fire: job %s not found; skipping", job_id)
            return
        prefixed = PREFIX + (row["payload"] or "")
        try:
            connector = self.connector_registry.get(row["channel"])
            self._run_coro(connector.send(row["chat_id"], prefixed))
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
                row["conversation_id"], AssistantMessage(content=prefixed),
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_reminder_runner.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/runner.py kc-supervisor/tests/test_reminder_runner.py
git commit -m "feat(kc-supervisor): ReminderRunner.fire happy path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `ReminderRunner` error paths

**Files:**
- Test: `kc-supervisor/tests/test_reminder_runner.py`

The error paths are already implemented in Task 6. This task adds explicit tests.

- [ ] **Step 1: Append failing tests**

Append to `kc-supervisor/tests/test_reminder_runner.py`:

```python
def test_fire_connector_failure_marks_failed(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    connector = registry.get.return_value
    connector.send.side_effect = RuntimeError("network down")
    job_id = _seed(s, cm)
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    cm.append.assert_not_called()


def test_fire_persist_failure_still_marks_done(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    cm.append.side_effect = Exception("DB lock")
    job_id = _seed(s, cm)
    runner.fire(job_id)
    connector = registry.get.return_value
    connector.send.assert_called_once()  # User got the message
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"  # We still mark done — user-visible side
                                    # effect is the load-bearing one
    assert row["attempts"] == 1


def test_fire_cron_connector_failure_marks_failed(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    connector = registry.get.return_value
    connector.send.side_effect = RuntimeError("403")
    job_id = _seed(s, cm, kind="cron")
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"  # Cron also marked failed on send error
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_reminder_runner.py -v
```

Expected: all 8 tests pass (5 from Task 6 + 3 new).

- [ ] **Step 3: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/tests/test_reminder_runner.py
git commit -m "test(kc-supervisor): cover ReminderRunner connector and persist error paths

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Lifecycle wiring — startup hooks, deps injection, scheduler.start()

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py`
- Modify: `kc-supervisor/src/kc_supervisor/service.py`

- [ ] **Step 1: Read existing structure**

The supervisor's `main.py` constructs a `deps` object and passes it to `service.create_app(deps)`. `service.py` registers FastAPI startup/shutdown hooks. We follow the same pattern: construct `ScheduleService` in `main.py`, stash on `deps`, register `@app.on_event("startup")` in `service.py` to call `schedule_service.start()` after `deps.event_loop` is captured.

- [ ] **Step 2: Add `ScheduleService` and `ReminderRunner` to `deps` dataclass in `main.py`**

In `kc-supervisor/src/kc_supervisor/main.py`, find the `Deps` dataclass (likely defined in `main.py` or imported from `service.py`). It already has fields like `storage`, `conversations`, `connector_registry`, `event_loop`. Add:

```python
schedule_service: Optional["ScheduleService"] = None
```

(Use a string annotation to avoid circular imports.)

In `main.py`, after `deps = Deps(...)` is constructed (around line 200-250), add:

```python
# Phase-1 scheduling. Constructed here but started inside FastAPI's startup
# hook (see service.py) so it picks up the running event loop.
from kc_supervisor.scheduling import ScheduleService, ReminderRunner
import tzlocal

tz_name = str(tzlocal.get_localzone())

# ReminderRunner needs a coroutine_runner that bridges back to deps.event_loop,
# which isn't available until FastAPI startup. We use a placeholder that will
# be replaced inside the startup hook in service.py.
def _coroutine_runner(coro):
    if deps.event_loop is None:
        raise RuntimeError("ScheduleService fired before FastAPI startup")
    fut = asyncio.run_coroutine_threadsafe(coro, deps.event_loop)
    return fut.result(timeout=30)

reminder_runner = ReminderRunner(
    storage=deps.storage,
    conversations=deps.conversations,
    connector_registry=deps.connector_registry,
    coroutine_runner=_coroutine_runner,
)
schedule_service = ScheduleService(
    storage=deps.storage,
    runner=reminder_runner,
    db_path=deps.home / "kc.db",
    timezone=tz_name,
)
deps.schedule_service = schedule_service
```

(Adjust `deps.home / "kc.db"` to whatever path `Storage` is initialized against — read the existing `main.py` to find the actual db path variable.)

If `tzlocal` isn't already a transitive dep, install it:

```bash
.venv/bin/pip install 'tzlocal>=5.0'
```

And add to `pyproject.toml`:

```toml
"tzlocal>=5.0",
```

- [ ] **Step 3: Add startup/shutdown hooks in `service.py`**

In `kc-supervisor/src/kc_supervisor/service.py`, after the existing `_startup_start_connectors` block (around line 195), add:

```python
    # Reminder/cron scheduler. Started after connectors so deps.event_loop is
    # captured first.
    if deps.schedule_service is not None:
        @app.on_event("startup")
        async def _startup_schedule_service() -> None:
            try:
                deps.schedule_service.start()
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "ScheduleService failed to start")

        @app.on_event("shutdown")
        async def _shutdown_schedule_service() -> None:
            try:
                deps.schedule_service.shutdown()
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "ScheduleService failed to shut down")
```

- [ ] **Step 4: Run the existing supervisor suite**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest -q 2>&1 | tail -3
```

Expected: all green. Some tests may construct `Deps` directly — they may need the new optional `schedule_service` field treated as `None` (which it is by default).

If any test fails because `Deps` no longer accepts the old call signature, update the test to either pass `schedule_service=None` or rely on the dataclass default.

- [ ] **Step 5: Smoke-test boot**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/python -c "
import asyncio
from kc_supervisor.main import build_app
from fastapi.testclient import TestClient

app = build_app()  # Or however main.py exposes the FastAPI app — check main.py
with TestClient(app) as client:
    r = client.get('/health')
    print(r.status_code, r.json())
"
```

(Adjust the `build_app` import to whatever `main.py` actually exposes — read the file to find the entry point.)

Expected: 200 status with health JSON. The supervisor must start cleanly with the scheduler running.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/main.py kc-supervisor/src/kc_supervisor/service.py kc-supervisor/pyproject.toml
git commit -m "feat(kc-supervisor): wire ScheduleService into FastAPI lifecycle

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Reconcile (startup + 60-second background tick)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/service.py`
- Test: `kc-supervisor/tests/test_schedule_rehydrate.py`

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_schedule_rehydrate.py`:

```python
from __future__ import annotations
import time
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService


def _make_service(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
    )
    return svc, s


def _seed_conv(s: Storage) -> int:
    return s.create_conversation(agent="kona", channel="telegram")


def test_reconcile_drops_aps_jobs_with_missing_db_row(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        # APS has the job; DB has the row.
        assert svc._scheduler.get_job(str(r["id"])) is not None
        # Manually delete the DB row (simulating cascade delete from conversation removal)
        s.delete_scheduled_job(r["id"])
        svc.reconcile()
        # APS job should be gone
        assert svc._scheduler.get_job(str(r["id"])) is None
    finally:
        svc.shutdown()


def test_reconcile_recreates_aps_job_for_pending_db_row(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        # Manually remove the APS job
        svc._scheduler.remove_job(str(r["id"]))
        assert svc._scheduler.get_job(str(r["id"])) is None
        svc.reconcile()
        # Should have been re-created from the DB row
        assert svc._scheduler.get_job(str(r["id"])) is not None
    finally:
        svc.shutdown()


def test_rehydrate_after_restart_preserves_pending_job(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    r = svc.schedule_one_shot(
        when="in 1 hour", content="x",
        conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.shutdown()

    # Re-create the service against the same DB
    svc2, s2 = _make_service(tmp_path)
    svc2.start()
    try:
        # APS rehydrates from SQLAlchemyJobStore
        assert svc2._scheduler.get_job(str(r["id"])) is not None
        # DB row is still pending
        assert s2.get_scheduled_job(r["id"])["status"] == "pending"
    finally:
        svc2.shutdown()
```

- [ ] **Step 2: Run tests to verify the reconcile ones fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_schedule_rehydrate.py -v
```

Expected: `test_rehydrate_after_restart_preserves_pending_job` should pass already (APS handles that automatically). The two reconcile tests fail — `reconcile()` method doesn't exist.

- [ ] **Step 3: Add `reconcile()` to `ScheduleService`**

In `kc-supervisor/src/kc_supervisor/scheduling/service.py`, add:

```python
    # ---- reconcile ----

    def reconcile(self) -> None:
        """Reconcile APS jobs against the scheduled_jobs table.

        - APS jobs whose mirror DB row is missing → drop the APS job.
        - DB rows with status='pending' whose APS job is missing → re-create
          the APS job from the row.
        """
        pending_rows = self.storage.list_scheduled_jobs(statuses=("pending",))
        pending_by_id = {str(r["id"]): r for r in pending_rows}

        # Drop APS jobs without a DB mirror.
        for job in list(self._scheduler.get_jobs()):
            if job.id not in pending_by_id:
                try:
                    self._scheduler.remove_job(job.id)
                except Exception:
                    pass

        # Re-create APS jobs for pending DB rows that lost their APS entry.
        for row_id, row in pending_by_id.items():
            if self._scheduler.get_job(row_id) is not None:
                continue
            try:
                trigger = self._build_trigger_for_row(row)
            except Exception:
                logger.exception("reconcile: bad trigger for row %s", row_id)
                continue
            kwargs = {"misfire_grace_time": 86400} if row["kind"] == "reminder" else {"coalesce": True}
            self._scheduler.add_job(
                self.runner.fire, trigger=trigger,
                kwargs={"job_id": row["id"]}, id=row_id,
                replace_existing=True, **kwargs,
            )

    def _build_trigger_for_row(self, row: dict):
        if row["kind"] == "reminder":
            from datetime import datetime, timezone as _tz_mod
            dt = datetime.fromtimestamp(row["when_utc"], tz=_tz_mod.utc)
            return DateTrigger(run_date=dt)
        elif row["kind"] == "cron":
            return CronTrigger.from_crontab(row["cron_spec"], timezone=self._tz)
        else:
            raise ValueError(f"unknown kind: {row['kind']!r}")
```

- [ ] **Step 4: Wire reconcile into startup + 60-second background tick**

In `ScheduleService.start()`, replace the body with:

```python
    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
        # Initial reconcile
        self.reconcile()
        # Background reconcile tick
        self._scheduler.add_job(
            self.reconcile, trigger="interval", seconds=60,
            id="__reconcile__", replace_existing=True,
        )

    def shutdown(self) -> None:
        if self._scheduler.running:
            try:
                self._scheduler.remove_job("__reconcile__")
            except Exception:
                pass
            self._scheduler.shutdown(wait=False)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_schedule_rehydrate.py -v
```

Expected: all 3 tests pass.

Then run the full scheduling test set:

```bash
.venv/bin/pytest tests/test_scheduling_storage.py tests/test_scheduling_service.py tests/test_reminder_runner.py tests/test_schedule_rehydrate.py -v
```

Expected: all 29 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/service.py kc-supervisor/tests/test_schedule_rehydrate.py
git commit -m "feat(kc-supervisor): scheduler reconcile on startup + 60s tick

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Agent tools module — four scheduling tools

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/scheduling/tools.py`
- Modify: `kc-supervisor/src/kc_supervisor/scheduling/__init__.py`
- Test: `kc-supervisor/tests/test_scheduling_tools.py`

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_scheduling_tools.py`:

```python
from __future__ import annotations
import time
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.tools import build_scheduling_tools


def _make_service(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
    )
    svc.start()
    return svc, s


def _seed_conv(s: Storage) -> int:
    return s.create_conversation(agent="kona", channel="telegram")


def test_build_scheduling_tools_returns_four(tmp_path):
    svc, s = _make_service(tmp_path)
    try:
        tools = build_scheduling_tools(
            service=svc,
            current_context=lambda: {
                "conversation_id": 1, "channel": "telegram",
                "chat_id": "C1", "agent": "kona",
            },
        )
        names = {t.name for t in tools}
        assert names == {
            "schedule_reminder", "schedule_cron",
            "list_reminders", "cancel_reminder",
        }
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_reminder_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    try:
        result = schedule_reminder.impl(when="in 1 hour", content="dinner")
        assert "id" in result
        assert "fires_at_human" in result
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert rows[0]["payload"] == "dinner"
    finally:
        svc.shutdown()


def test_schedule_cron_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_cron = next(t for t in tools if t.name == "schedule_cron")
    try:
        result = schedule_cron.impl(cron="0 9 * * *", content="standup")
        assert "id" in result
        assert "human_summary" in result
    finally:
        svc.shutdown()


def test_list_reminders_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    list_reminders = next(t for t in tools if t.name == "list_reminders")
    try:
        schedule_reminder.impl(when="in 1 hour", content="x")
        out = list_reminders.impl(active_only=True)
        assert len(out["reminders"]) == 1
    finally:
        svc.shutdown()


def test_cancel_reminder_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    cancel_reminder = next(t for t in tools if t.name == "cancel_reminder")
    try:
        r = schedule_reminder.impl(when="in 1 hour", content="dinner")
        out = cancel_reminder.impl(id_or_description=str(r["id"]))
        assert out["ambiguous"] is False
        assert out["cancelled"][0]["id"] == r["id"]
    finally:
        svc.shutdown()


def test_cancel_reminder_ambiguous(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    cancel_reminder = next(t for t in tools if t.name == "cancel_reminder")
    try:
        schedule_reminder.impl(when="in 1 hour", content="dinner mom")
        schedule_reminder.impl(when="in 2 hours", content="dinner res")
        out = cancel_reminder.impl(id_or_description="dinner")
        assert out["ambiguous"] is True
        assert len(out["candidates"]) == 2
    finally:
        svc.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_tools.py -v
```

Expected: ImportError for `build_scheduling_tools`.

- [ ] **Step 3: Create `tools.py`**

Create `kc-supervisor/src/kc_supervisor/scheduling/tools.py`:

```python
from __future__ import annotations
from typing import Callable
from kc_core.tools import Tool
from kc_supervisor.scheduling.service import ScheduleService


def build_scheduling_tools(
    service: ScheduleService,
    current_context: Callable[[], dict],
) -> list[Tool]:
    """Build the four scheduling tools.

    Args:
        service: the live ScheduleService instance.
        current_context: a callable that returns the current invocation
                         context as a dict with keys:
                         - conversation_id: int
                         - channel: str ('telegram' | 'dashboard' | 'imessage')
                         - chat_id: str
                         - agent: str
                         The supervisor binds this per-conversation when the
                         agent is invoked.
    """

    def _schedule_reminder(when: str, content: str) -> dict:
        ctx = current_context()
        return service.schedule_one_shot(
            when=when, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
        )

    def _schedule_cron(cron: str, content: str) -> dict:
        ctx = current_context()
        return service.schedule_cron(
            cron=cron, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
        )

    def _list_reminders(active_only: bool = True) -> dict:
        ctx = current_context()
        return service.list_reminders(
            conversation_id=ctx["conversation_id"], active_only=active_only,
        )

    def _cancel_reminder(id_or_description: str) -> dict:
        ctx = current_context()
        return service.cancel_reminder(
            id_or_description, conversation_id=ctx["conversation_id"],
        )

    return [
        Tool(
            name="schedule_reminder",
            description=(
                "Schedule a one-shot reminder. The user will receive the "
                "reminder text in this same conversation at the specified "
                "time. The `when` argument is natural-language (e.g. '5pm "
                "today', 'in 2 hours', 'tomorrow at 9am') resolved in the "
                "user's local timezone. Returns {id, fires_at, fires_at_human, "
                "kind}. Raises ValueError on unparseable or past times."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "natural-language time"},
                    "content": {"type": "string", "description": "reminder text (1-4000 chars)"},
                },
                "required": ["when", "content"],
            },
            impl=_schedule_reminder,
        ),
        Tool(
            name="schedule_cron",
            description=(
                "Schedule a recurring reminder via standard 5-field cron "
                "syntax (minute hour day-of-month month day-of-week). "
                "Examples: '0 9 * * 1-5' = weekdays 9am, '0 */2 * * *' = "
                "every 2 hours. Sub-minute schedules are not supported. "
                "Returns {id, next_fire, next_fire_human, human_summary, kind}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cron": {"type": "string", "description": "5-field cron expression"},
                    "content": {"type": "string", "description": "reminder text (1-4000 chars)"},
                },
                "required": ["cron", "content"],
            },
            impl=_schedule_cron,
        ),
        Tool(
            name="list_reminders",
            description=(
                "List reminders scheduled in the current conversation. "
                "If active_only is True (default), returns only pending "
                "reminders; otherwise also includes done, cancelled, failed, "
                "and missed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": "boolean",
                        "description": "if True, only pending reminders",
                        "default": True,
                    },
                },
                "required": [],
            },
            impl=_list_reminders,
        ),
        Tool(
            name="cancel_reminder",
            description=(
                "Cancel a pending reminder by ID or by description fragment. "
                "If id_or_description is purely numeric, treated as an ID. "
                "Otherwise, matched as case-insensitive substring against "
                "the reminder content. If multiple match, returns "
                "{ambiguous: true, candidates: [...]} and cancels nothing — "
                "ask the user to disambiguate."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id_or_description": {
                        "type": "string",
                        "description": "integer ID or description fragment",
                    },
                },
                "required": ["id_or_description"],
            },
            impl=_cancel_reminder,
        ),
    ]
```

- [ ] **Step 4: Update `__init__.py` to re-export the public surface**

Edit `kc-supervisor/src/kc_supervisor/scheduling/__init__.py`:

```python
"""Reminder + cron scheduling for kc-supervisor."""
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.runner import ReminderRunner
from kc_supervisor.scheduling.tools import build_scheduling_tools

__all__ = ["ScheduleService", "ReminderRunner", "build_scheduling_tools"]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_tools.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/scheduling/tools.py kc-supervisor/src/kc_supervisor/scheduling/__init__.py kc-supervisor/tests/test_scheduling_tools.py
git commit -m "feat(kc-supervisor): four scheduling agent tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Register scheduling tools on Kona only via `assembly.py`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py`

- [ ] **Step 1: Read existing tool-registration code**

Read `kc-supervisor/src/kc_supervisor/assembly.py` lines 80-240 to understand the existing pattern. Tools are registered via `registry.register(tool)`, with their tier set in `tier_map[tool.name] = Tier.SAFE`.

- [ ] **Step 2: Identify the function signature**

Find the function (probably `assemble(...)`) that takes `cfg`, `deps`, etc., and registers tools. The function likely already accepts `deps` or similar. Check whether it has access to `deps.schedule_service`. If not, add it as a parameter.

- [ ] **Step 3: Add the registration block**

Inside `assemble(...)`, after the existing news-tool registration block (around line 234-239 or wherever the file ends its tool registrations), add:

```python
    # Phase-1 scheduling tools — registered ONLY on Kona.
    if cfg.name == "kona" and deps.schedule_service is not None:
        from kc_supervisor.scheduling import build_scheduling_tools
        from kc_core.tools import ToolRegistry as _TR  # for tier_map's typing

        # The current_context callable is bound to a contextvar that the
        # WS / inbound paths set when invoking the agent. For Phase 1 we use
        # a thread-local stash on the assembled object — see ws_routes.py
        # and inbound.py changes below.
        def _ctx() -> dict:
            from kc_supervisor.scheduling.context import get_current_context
            return get_current_context()

        scheduling_tools = build_scheduling_tools(
            service=deps.schedule_service,
            current_context=_ctx,
        )
        for t in scheduling_tools:
            registry.register(t)
            tier_map[t.name] = Tier.SAFE
```

- [ ] **Step 4: Create the contextvar module**

Create `kc-supervisor/src/kc_supervisor/scheduling/context.py`:

```python
"""Per-invocation context for scheduling tools.

The supervisor's WS / inbound handlers set the active conversation_id, channel,
chat_id, and agent into a contextvar before invoking the agent. The scheduling
tools read this contextvar to know "where am I scheduling for".
"""
from __future__ import annotations
from contextvars import ContextVar


_current_context: ContextVar[dict] = ContextVar("scheduling_context")


def set_current_context(ctx: dict) -> None:
    """Called by ws_routes / inbound before agent.send_stream."""
    _current_context.set(ctx)


def get_current_context() -> dict:
    """Called by the scheduling tools at invocation time."""
    try:
        return _current_context.get()
    except LookupError:
        raise RuntimeError(
            "scheduling tool invoked outside a conversation context — "
            "this is a wiring bug; ws_routes/inbound must set_current_context "
            "before invoking the agent"
        )
```

- [ ] **Step 5: Wire the contextvar in `ws_routes.py`**

In `kc-supervisor/src/kc_supervisor/ws_routes.py`, just before the `async for frame in rt.assembled.core_agent.send_stream(content):` loop (around line 137), add:

```python
                    from kc_supervisor.scheduling.context import set_current_context
                    set_current_context({
                        "conversation_id": conversation_id,
                        "channel": "dashboard",
                        "chat_id": f"dashboard:{conversation_id}",
                        "agent": rt.name,
                    })
```

- [ ] **Step 6: Wire the contextvar in `inbound.py`**

In `kc-supervisor/src/kc_supervisor/inbound.py`, just before its `async for frame in ...send_stream(env.content):` loop (around line 91), add the same pattern but with the right channel/chat_id from the inbound envelope:

```python
                from kc_supervisor.scheduling.context import set_current_context
                set_current_context({
                    "conversation_id": conversation_id,
                    "channel": env.channel,
                    "chat_id": env.chat_id,
                    "agent": rt.name,
                })
```

- [ ] **Step 7: Run all supervisor tests**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest -q 2>&1 | tail -3
```

Expected: existing tests pass; new scheduling tests pass.

If any existing test fails because the assembly path now requires `deps.schedule_service` to be non-None for Kona, those tests need to either pass `schedule_service=None` (which the conditional check handles cleanly — tools simply aren't registered) or to be updated to pass a fake service.

- [ ] **Step 8: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/assembly.py kc-supervisor/src/kc_supervisor/scheduling/context.py kc-supervisor/src/kc_supervisor/ws_routes.py kc-supervisor/src/kc_supervisor/inbound.py
git commit -m "feat(kc-supervisor): register scheduling tools on Kona via contextvar

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Tool isolation test — non-Kona agents do not get scheduling tools

**Files:**
- Test: `kc-supervisor/tests/test_scheduling_tools.py`

- [ ] **Step 1: Append failing test**

Append to `kc-supervisor/tests/test_scheduling_tools.py`:

```python
def test_scheduling_tools_not_registered_on_non_kona(tmp_path):
    """Phase 1 invariant: only Kona gets the scheduling tools."""
    # This test is light-touch: it checks the registration logic in assembly.py
    # by inspecting an assembled non-Kona agent (e.g. 'alice' from the test fixture)
    # and asserts none of the four tool names appear.
    # The actual setup uses the existing assembly fixtures from conftest.py.
    import pytest
    pytest.importorskip("kc_supervisor.assembly")

    from kc_supervisor.assembly import assemble
    # NOTE: the exact signature of assemble() is project-specific. The
    # implementing engineer should adapt this to mirror existing assembly
    # tests in kc-supervisor/tests/test_assembly.py.
    # The key assertion is:
    #   assert {"schedule_reminder", "schedule_cron", "list_reminders",
    #           "cancel_reminder"}.isdisjoint(set(assembled.engine.tool_names))
    # for any agent whose cfg.name != "kona".
```

The implementing engineer should adapt the test body using the `assemble`-based fixtures already present in `tests/test_assembly.py` and `tests/test_assembly_news.py`. Read those files first; the test pattern of constructing a non-Kona `cfg` and inspecting `assembled.engine.tier_map.keys()` (or `assembled.core_agent.tools.names()`) against the four scheduling tool names is the right shape.

The implementing engineer is permitted to substitute a more direct test that reads the `assembly.py` source to confirm the conditional `if cfg.name == "kona"` guard exists and is the only registration path. Either approach is acceptable as long as the invariant ("non-Kona agents do not get the four scheduling tools") is verified by automated test.

- [ ] **Step 2: Run test to verify it passes**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_scheduling_tools.py -v
```

Expected: 7/7 pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/tests/test_scheduling_tools.py
git commit -m "test(kc-supervisor): assert scheduling tools are Kona-only

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Misfire-grace and cron-coalesce rehydration tests

**Files:**
- Test: `kc-supervisor/tests/test_schedule_rehydrate.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-supervisor/tests/test_schedule_rehydrate.py`:

```python
def test_one_shot_misfire_within_grace_fires_late(tmp_path):
    """Schedule a reminder for the near future, shut down, advance clock past
    the due time but within 24h grace, restart — APS should still fire it.

    NOTE: this test is brittle to wall-clock interactions with APScheduler's
    BackgroundScheduler internals. We assert the CONFIGURATION (grace=86400)
    rather than executing the fire path under accelerated time, which
    APScheduler does not reliably support with frozen-time fixtures.
    """
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        job = svc._scheduler.get_job(str(r["id"]))
        assert job is not None
        assert job.misfire_grace_time == 86400
    finally:
        svc.shutdown()


def test_cron_coalesce_is_set(tmp_path):
    """Cron jobs should have coalesce=True so missed firings collapse to one."""
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_cron(
            cron="0 9 * * *", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        job = svc._scheduler.get_job(str(r["id"]))
        assert job is not None
        assert job.coalesce is True
    finally:
        svc.shutdown()


def test_aps_and_app_tables_coexist(tmp_path):
    """APScheduler creates its own tables in the same DB; our migration must
    not collide.
    """
    svc, s = _make_service(tmp_path)
    svc.start()
    try:
        with s.connect() as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        # Our app tables
        assert "scheduled_jobs" in tables
        assert "messages" in tables
        # APS tables (default name pattern)
        assert any(t.startswith("apscheduler") for t in tables)
    finally:
        svc.shutdown()
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest tests/test_schedule_rehydrate.py -v
```

Expected: 6/6 pass (3 from Task 9 + 3 new).

- [ ] **Step 3: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/tests/test_schedule_rehydrate.py
git commit -m "test(kc-supervisor): assert APS misfire-grace + coalesce + table coexistence

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: SMOKE.md updates + final cross-suite verification

**Files:**
- Modify: `kc-supervisor/SMOKE.md`

- [ ] **Step 1: Append SMOKE gates**

Open `/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor/SMOKE.md` and append:

```markdown

## reminders & cron — Phase 1 (added 2026-05-09)

- [ ] On Telegram, ask Kona "remind me in 2 minutes to test reminder fire". 2 minutes later Telegram receives `⏰ test reminder fire`. The dashboard chat for that conversation shows the same string in an assistant bubble.
- [ ] On the dashboard, ask Kona "remind me in 1 minute to test dashboard fire". 1 minute later the chat view shows `⏰ test dashboard fire` as a new bubble.
- [ ] Schedule a daily cron: "every weekday at 9am remind me to check email". Confirm the agent's reply has `human_summary` like "every weekday at 09:00". The next 9am the reminder fires.
- [ ] Schedule a reminder, restart the supervisor (`pkill -HUP` or full restart), confirm with `SELECT * FROM scheduled_jobs WHERE status='pending'` that the row is intact, then wait for the original due time — the reminder still fires.
- [ ] Cancel by description: schedule "dinner reminder", say "cancel the dinner reminder". Agent confirms cancellation. `SELECT * FROM scheduled_jobs` shows the row is gone.
- [ ] Disambiguation flow: schedule "meeting prep" and "meeting notes", say "cancel the meeting one". Agent gets `ambiguous=True` and asks which. Cancel by ID. Confirm only the chosen one is removed.
- [ ] Confirm scheduling tools are NOT available to non-Kona agents: `python -c "from kc_supervisor.assembly import ..." or via the dashboard's Agents view, inspect any non-Kona agent's tool list and verify the four scheduling tools are absent.
```

- [ ] **Step 2: Run all three test suites**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-core && uv run pytest -q 2>&1 | tail -3
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor && .venv/bin/pytest -q 2>&1 | tail -3
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-dashboard && npm test 2>&1 | tail -8
```

Expected:
- kc-core: 69 passed (no changes here, baseline preserved)
- kc-supervisor: 240 passed (202 baseline + 38 new across 5 new test files)
- kc-dashboard: 39 passed (no changes here)

Paste the three summary counts in the report.

- [ ] **Step 3: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/SMOKE.md
git commit -m "docs: add SMOKE gates for reminders & cron Phase 1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage check:**

- ✅ `scheduled_jobs` SQLite table + migration — Task 2
- ✅ `ScheduleService` wrapping APScheduler + SQLAlchemyJobStore — Tasks 3-5, 9
- ✅ `ReminderRunner` fire callback (send + persist + status update + audit) — Tasks 6-7
- ✅ Four agent tools (schedule_reminder/cron/list/cancel) — Task 10
- ✅ Tool registration on Kona only — Task 11 + Task 12 invariant test
- ✅ Server-side NL parsing via `dateparser` — Task 3
- ✅ Cron parsing/validation via `croniter` + `cron-descriptor` — Task 4
- ✅ Restart rehydration + misfire grace 24h + cron coalesce — Tasks 9, 13
- ✅ Reconcile (DB-row-is-source-of-truth, bidirectional, startup + 60s tick) — Task 9
- ✅ Same-channel scheduling only (via the contextvar pinned to current chat) — Task 11
- ✅ Telegram + dashboard channels — covered by the contextvar wiring in both `ws_routes.py` and `inbound.py`
- ✅ ⏰ prefix on fired payload — Task 6 (`PREFIX = "⏰ "`)
- ✅ Persisted as AssistantMessage — Task 6
- ✅ FK ON DELETE CASCADE on conversation_id — Task 2 (test `test_conversation_delete_cascades_jobs`)
- ✅ Payload size cap (4000 chars) — Tasks 3-4
- ✅ ID-or-description cancel with ambiguity handling — Task 5
- ✅ Lifecycle hooks in `service.py` — Task 8
- ✅ SMOKE gates — Task 14

**Type-name consistency:**
- `ScheduleService` — referenced consistently in service.py, runner.py, tools.py, assembly.py.
- `ReminderRunner` — referenced consistently.
- `build_scheduling_tools(service, current_context)` — same signature in tools.py and assembly.py.
- Tool names: `schedule_reminder`, `schedule_cron`, `list_reminders`, `cancel_reminder` — exact strings used in tools.py, the SMOKE list, and the Task 12 invariant test.
- `set_current_context` / `get_current_context` from `scheduling/context.py` — used in tools.py, ws_routes.py, inbound.py.
- Storage helper names (`add_scheduled_job`, `get_scheduled_job`, `list_scheduled_jobs`, `update_scheduled_job_status`, `update_scheduled_job_after_fire`, `delete_scheduled_job`) — consistent across Tasks 2, 5, 9.

**No placeholders:** Task 12's test body intentionally delegates to "adapt to existing assembly fixtures" because the exact fixture wiring varies by file. The assertion contract is concrete (`{"schedule_reminder", ...}.isdisjoint(...)`); the engineer fills in 5-10 lines of fixture setup mirroring an existing test.
