import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.todos.storage import TodoStorage


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
