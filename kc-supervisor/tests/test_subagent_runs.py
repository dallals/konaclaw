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
