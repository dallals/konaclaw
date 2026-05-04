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
    s.link_audit_undo(audit_id=aid, undo_op_id="op-abc-123")
    assert s.get_undo_op_for_audit(aid) == "op-abc-123"


def test_audit_undo_link_missing_returns_none(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.get_undo_op_for_audit(99) is None


def test_audit_undo_link_idempotent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, "op-1")
    s.link_audit_undo(aid, "op-1")
    assert s.get_undo_op_for_audit(aid) == "op-1"


def test_audit_undo_link_first_wins_on_conflict(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, "op-original")
    s.link_audit_undo(aid, "op-replacement")  # different op_id, same audit
    assert s.get_undo_op_for_audit(aid) == "op-original"


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
