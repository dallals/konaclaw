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
