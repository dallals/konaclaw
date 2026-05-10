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
    s.init()

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
                "INSERT INTO messages (conversation_id, role, content, ts, scheduled_job_id) "
                "VALUES (1, 'user', 'x', ?, 99999)", (time.time(),)
            )
