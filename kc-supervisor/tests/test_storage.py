# tests/test_storage.py
from pathlib import Path
import time
from kc_supervisor.storage import Storage


def test_init_creates_tables(tmp_path):
    s = Storage(db_path=tmp_path / "kc.db"); s.init()
    with s.connect() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "messages", "audit", "audit_undo_link"} <= names


def test_create_conversation_returns_id(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    assert isinstance(cid, int) and cid > 0


def test_append_and_list_messages(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    s.append_message(cid, role="user", content="hi", tool_call_json=None)
    s.append_message(cid, role="assistant", content="hello", tool_call_json=None)
    msgs = s.list_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["content"] == "hello"


def test_list_conversations_filters_by_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.create_conversation(agent="kc", channel="dashboard")
    s.create_conversation(agent="EmailBot", channel="dashboard")
    convs = s.list_conversations(agent="kc")
    assert len(convs) == 1
    assert convs[0]["agent"] == "kc"


def test_audit_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(
        agent="kc", tool="file.read",
        args_json='{"share":"r","relpath":"x"}',
        decision="safe·auto", result="14 bytes", undoable=False,
    )
    rows = s.list_audit(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == aid
    assert rows[0]["tool"] == "file.read"


def test_audit_filter_by_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.append_audit(agent="kc", tool="file.read", args_json="{}", decision="safe", result="ok", undoable=False)
    s.append_audit(agent="EmailBot", tool="file.read", args_json="{}", decision="safe", result="ok", undoable=False)
    rows = s.list_audit(agent="kc")
    assert len(rows) == 1
    assert rows[0]["agent"] == "kc"


def test_audit_undo_link_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(
        agent="kc", tool="file.delete", args_json="{}",
        decision="destructive·callback", result="ok", undoable=True,
    )
    s.link_audit_undo(audit_id=aid, undo_op_id=42)
    assert s.get_undo_op_for_audit(aid) == 42


def test_audit_undo_link_missing_returns_none(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.get_undo_op_for_audit(99) is None


def test_audit_undo_link_idempotent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, 42)
    s.link_audit_undo(aid, 42)
    assert s.get_undo_op_for_audit(aid) == 42


def test_audit_undo_link_first_wins_on_conflict(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, 100)
    s.link_audit_undo(aid, 200)
    assert s.get_undo_op_for_audit(aid) == 100


def test_get_conversation_returns_dict(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    conv = s.get_conversation(cid)
    assert conv is not None
    assert conv["id"] == cid
    assert conv["agent"] == "kc"
    assert conv["channel"] == "dashboard"


def test_get_conversation_missing_returns_none(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.get_conversation(99999) is None


def test_pin_and_list_orders_pinned_first(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    a = s.create_conversation(agent="kc", channel="dashboard")
    time.sleep(0.01)
    b = s.create_conversation(agent="kc", channel="dashboard")
    time.sleep(0.01)
    c = s.create_conversation(agent="kc", channel="dashboard")
    assert s.set_conversation_pinned(a, True) is True
    convs = s.list_conversations(agent="kc")
    assert [r["id"] for r in convs] == [a, c, b]
    assert convs[0]["pinned"] == 1
    assert convs[1]["pinned"] == 0


def test_set_pinned_false_unpins(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    s.set_conversation_pinned(cid, True)
    s.set_conversation_pinned(cid, False)
    assert s.get_conversation(cid)["pinned"] == 0


def test_set_pinned_unknown_returns_false(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.set_conversation_pinned(99999, True) is False


def test_delete_conversation_removes_messages_and_row(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    s.append_message(cid, role="user", content="hi", tool_call_json=None)
    s.append_message(cid, role="assistant", content="hello", tool_call_json=None)
    s.append_audit(agent="kc", tool="t", args_json="{}", decision="d", result="r", undoable=False)
    assert s.delete_conversation(cid) is True
    assert s.get_conversation(cid) is None
    assert s.list_messages(cid) == []
    assert len(s.list_audit()) == 1  # audit untouched


def test_delete_conversation_unknown_returns_false(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.delete_conversation(99999) is False


def test_set_title_and_clear(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    assert s.set_conversation_title(cid, "Trip planning") is True
    assert s.get_conversation(cid)["title"] == "Trip planning"
    assert s.set_conversation_title(cid, None) is True
    assert s.get_conversation(cid)["title"] is None


def test_set_title_unknown_returns_false(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.set_conversation_title(99999, "x") is False


def test_list_audit_includes_undone_flag(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid_undone = s.append_audit(
        agent="kc", tool="file.write", args_json="{}",
        decision="tier", result="ok", undoable=True,
    )
    s.link_audit_undo(aid_undone, undo_op_id=42)
    aid_pending = s.append_audit(
        agent="kc", tool="file.write", args_json="{}",
        decision="tier", result="ok", undoable=True,
    )
    s.link_audit_undo(aid_pending, undo_op_id=43)
    aid_no_link = s.append_audit(
        agent="kc", tool="file.read", args_json="{}",
        decision="tier", result="ok", undoable=False,
    )

    # Nothing undone yet.
    rows = {r["id"]: r for r in s.list_audit()}
    assert rows[aid_undone]["undone"] == 0
    assert rows[aid_pending]["undone"] == 0
    assert rows[aid_no_link]["undone"] == 0

    # Mark one as undone — only that row's `undone` flips.
    assert s.mark_audit_undone(aid_undone) is True
    rows = {r["id"]: r for r in s.list_audit()}
    assert rows[aid_undone]["undone"] == 1
    assert rows[aid_pending]["undone"] == 0


def test_mark_undone_unknown_audit_returns_false(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.mark_audit_undone(99999) is False


def test_legacy_audit_undo_link_table_gets_undone_at_column(tmp_path):
    """Idempotent migration: a DB whose audit_undo_link pre-dates undone_at
    (a real KonaClaw install on Sammy's Mac) must keep working after init.
    Simulate by initing a fresh DB, dropping the column, then re-initing."""
    import sqlite3
    db = tmp_path / "kc.db"
    s = Storage(db); s.init()  # full SCHEMA, including undone_at

    # Force-drop undone_at to simulate a pre-migration DB.
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("ALTER TABLE audit_undo_link DROP COLUMN undone_at")
    conn.execute("INSERT INTO audit_undo_link (audit_id, undo_op_id) VALUES (1, 5)")
    conn.close()

    # init() again — the migration block must add undone_at back without error.
    s.init()
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(audit_undo_link)").fetchall()}
    assert "undone_at" in cols


def test_init_idempotent_adds_pinned_column_to_legacy_db(tmp_path):
    db = tmp_path / "kc.db"
    import sqlite3
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "agent TEXT NOT NULL, channel TEXT NOT NULL, started_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
        ("legacy", "dashboard", time.time()),
    )
    conn.close()
    s = Storage(db); s.init()
    convs = s.list_conversations()
    assert len(convs) == 1
    assert convs[0]["pinned"] == 0
    assert convs[0]["title"] is None
