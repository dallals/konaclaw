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
