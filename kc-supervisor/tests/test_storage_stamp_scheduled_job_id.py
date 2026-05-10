import time
import sqlite3
import pytest
from kc_supervisor.storage import Storage


def _seed(tmp_path):
    s = Storage(tmp_path / "t.db"); s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                  ("a", "dashboard", time.time()))
        c.execute(
            "INSERT INTO messages (conversation_id, role, content, ts) VALUES (1, 'assistant', 'hi', ?)",
            (time.time(),),
        )
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
    s = _seed(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        s.set_message_scheduled_job_id(message_id=1, scheduled_job_id=99999)
