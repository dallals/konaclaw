# tests/test_subagent_runs.py
from pathlib import Path
import time
from kc_supervisor.storage import Storage


def _new(tmp_path: Path) -> Storage:
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init()
    return s


def test_subagent_runs_table_exists(tmp_path: Path):
    s = _new(tmp_path)
    with s.connect() as c:
        row = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_runs'"
        ).fetchone()
    assert row is not None


def test_audit_has_attribution_cols(tmp_path: Path):
    s = _new(tmp_path)
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(audit)").fetchall()}
    assert {"parent_agent", "subagent_id", "subagent_template"} <= cols


def test_start_finish_subagent_run(tmp_path: Path):
    s = _new(tmp_path)
    s.start_subagent_run(
        id="ep_abc123",
        parent_conversation_id="conv_1",
        parent_agent="Kona-AI",
        template="web-researcher",
        label="berlin",
        task_preview="weather",
        context_keys=["recent"],
    )
    s.finish_subagent_run(
        id="ep_abc123",
        status="ok",
        duration_ms=1000,
        tool_calls_used=3,
        reply_chars=200,
        error_message=None,
    )
    with s.connect() as c:
        row = c.execute(
            "SELECT status, duration_ms, tool_calls_used FROM subagent_runs WHERE id=?",
            ("ep_abc123",),
        ).fetchone()
    assert row["status"] == "ok"
    assert row["duration_ms"] == 1000
    assert row["tool_calls_used"] == 3


def test_reap_running_on_startup(tmp_path: Path):
    s = _new(tmp_path)
    s.start_subagent_run(
        id="ep_zombie", parent_conversation_id="c", parent_agent="Kona-AI",
        template="x", label=None, task_preview=None, context_keys=None,
    )
    # Simulate restart: new Storage instance against the same DB file.
    s2 = Storage(db_path=tmp_path / "audit.sqlite")
    s2.init()
    reaped = s2.reap_running_subagent_runs()
    assert reaped == 1
    with s2.connect() as c:
        row = c.execute(
            "SELECT status, error_message FROM subagent_runs WHERE id=?", ("ep_zombie",),
        ).fetchone()
    assert row["status"] == "interrupted"
    assert "restart" in (row["error_message"] or "").lower()


def test_reap_is_idempotent(tmp_path: Path):
    s = _new(tmp_path)
    s.start_subagent_run(
        id="ep_a", parent_conversation_id="c", parent_agent="Kona-AI",
        template="x", label=None, task_preview=None, context_keys=None,
    )
    assert s.reap_running_subagent_runs() == 1
    assert s.reap_running_subagent_runs() == 0   # nothing left in 'running'


def test_finish_run_stores_reply_text(tmp_path: Path):
    s = _new(tmp_path)
    s.start_subagent_run(
        id="ep_rt1", parent_conversation_id="c", parent_agent="Kona-AI",
        template="x", label=None, task_preview=None, context_keys=None,
    )
    s.finish_subagent_run(
        id="ep_rt1", status="ok", duration_ms=500,
        tool_calls_used=1, reply_chars=11, error_message=None,
        reply_text="hello world",
    )
    with s.connect() as c:
        row = c.execute(
            "SELECT reply_text FROM subagent_runs WHERE id=?", ("ep_rt1",)
        ).fetchone()
    assert row["reply_text"] == "hello world"


def test_finish_run_truncates_reply_at_32kb(tmp_path: Path):
    s = _new(tmp_path)
    s.start_subagent_run(
        id="ep_trunc", parent_conversation_id="c", parent_agent="Kona-AI",
        template="x", label=None, task_preview=None, context_keys=None,
    )
    big_text = "a" * 50000
    s.finish_subagent_run(
        id="ep_trunc", status="ok", duration_ms=100,
        tool_calls_used=0, reply_chars=50000, error_message=None,
        reply_text=big_text,
    )
    with s.connect() as c:
        row = c.execute(
            "SELECT reply_text FROM subagent_runs WHERE id=?", ("ep_trunc",)
        ).fetchone()
    stored = row["reply_text"]
    assert stored is not None
    assert "[TRUNCATED" in stored
    assert stored.startswith("a" * 32000)
    assert "18000 bytes" in stored


def test_idempotent_migration_adds_reply_text_column(tmp_path: Path):
    """Open a DB, manually drop the reply_text column (by recreating the table
    without it), re-init, and assert Storage.init() adds it back."""
    import sqlite3

    db_path = tmp_path / "migrate_test.sqlite"
    # First init — creates table with reply_text.
    s = Storage(db_path=db_path)
    s.init()

    # Manually drop reply_text by recreating the table without it.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE subagent_runs_old AS
           SELECT id, parent_conversation_id, parent_agent, template, label,
                  task_preview, context_keys, started_ts, ended_ts, status,
                  duration_ms, tool_calls_used, reply_chars, error_message
           FROM subagent_runs"""
    )
    conn.execute("DROP TABLE subagent_runs")
    conn.execute(
        """CREATE TABLE subagent_runs (
             id TEXT PRIMARY KEY,
             parent_conversation_id TEXT NOT NULL,
             parent_agent TEXT NOT NULL,
             template TEXT NOT NULL,
             label TEXT,
             task_preview TEXT,
             context_keys TEXT,
             started_ts REAL NOT NULL,
             ended_ts REAL,
             status TEXT NOT NULL DEFAULT 'running',
             duration_ms INTEGER,
             tool_calls_used INTEGER NOT NULL DEFAULT 0,
             reply_chars INTEGER,
             error_message TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO subagent_runs
           SELECT id, parent_conversation_id, parent_agent, template, label,
                  task_preview, context_keys, started_ts, ended_ts, status,
                  duration_ms, tool_calls_used, reply_chars, error_message
           FROM subagent_runs_old"""
    )
    conn.execute("DROP TABLE subagent_runs_old")
    conn.commit()
    conn.close()

    # Verify reply_text is absent before re-init.
    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    cols_before = {r["name"] for r in conn2.execute("PRAGMA table_info(subagent_runs)").fetchall()}
    conn2.close()
    assert "reply_text" not in cols_before

    # Re-init — migration guard should add the column back.
    s2 = Storage(db_path=db_path)
    s2.init()

    with s2.connect() as c:
        cols_after = {r["name"] for r in c.execute("PRAGMA table_info(subagent_runs)").fetchall()}
    assert "reply_text" in cols_after
