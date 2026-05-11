import json
import pytest

from kc_supervisor.storage import Storage
from kc_supervisor.todos.storage import TodoStorage
from kc_supervisor.todos.tools import build_todo_tools


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (id, agent, channel, started_at) VALUES (?, ?, ?, ?)",
                  (40, "Kona-AI", "dashboard", 1.0))
    return TodoStorage(s)


@pytest.fixture
def tools(store):
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    return {t.name: t for t in build_todo_tools(
        storage=store,
        current_context=lambda: ctx,
        broadcast=lambda event: None,  # tested separately in Task 9
    )}


def test_builder_returns_six_tools(tools):
    assert set(tools.keys()) == {
        "todo.add", "todo.list", "todo.complete",
        "todo.update", "todo.delete", "todo.clear_done",
    }


def test_add_happy_path(tools):
    out = json.loads(tools["todo.add"].impl(title="Book hotel"))
    assert out["title"] == "Book hotel"
    assert out["scope"] == "conversation"
    assert out["status"] == "open"


def test_add_missing_title(tools):
    out = json.loads(tools["todo.add"].impl(title="   "))
    assert out == {"error": "missing_title"}


def test_add_persist(tools):
    out = json.loads(tools["todo.add"].impl(title="Renew passport", persist=True))
    assert out["scope"] == "agent"


def test_list_default_status_open(tools):
    tools["todo.add"].impl(title="A")
    tools["todo.add"].impl(title="B")
    out = json.loads(tools["todo.list"].impl())
    assert out["count"] == 2
    assert {i["title"] for i in out["items"]} == {"A", "B"}


def test_list_invalid_status(tools):
    out = json.loads(tools["todo.list"].impl(status="garbage"))
    assert out == {"error": "invalid_status", "value": "garbage"}


def test_list_invalid_scope(tools):
    out = json.loads(tools["todo.list"].impl(scope="garbage"))
    assert out == {"error": "invalid_scope", "value": "garbage"}


def test_complete_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    r = json.loads(tools["todo.complete"].impl(id=a["id"]))
    assert r["status"] == "done"


def test_complete_missing_id(tools):
    out = json.loads(tools["todo.complete"].impl())
    assert out == {"error": "missing_id"}


def test_complete_not_found(tools):
    out = json.loads(tools["todo.complete"].impl(id=99999))
    assert out == {"error": "not_found", "id": 99999}


def test_update_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.update"].impl(id=a["id"], title="A renamed", notes="n"))
    assert out["title"] == "A renamed"
    assert out["notes"] == "n"


def test_update_missing_fields(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.update"].impl(id=a["id"]))
    assert out == {"error": "missing_fields"}


def test_delete_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    out = json.loads(tools["todo.delete"].impl(id=a["id"]))
    assert out == {"id": a["id"], "deleted": True}


def test_clear_done_happy_path(tools):
    a = json.loads(tools["todo.add"].impl(title="A"))
    b = json.loads(tools["todo.add"].impl(title="B"))
    tools["todo.complete"].impl(id=a["id"])
    tools["todo.complete"].impl(id=b["id"])
    out = json.loads(tools["todo.clear_done"].impl())
    assert out == {"deleted_count": 2}


def test_broadcast_called_on_mutation(store):
    events = []
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    tools = {t.name: t for t in build_todo_tools(
        storage=store,
        current_context=lambda: ctx,
        broadcast=lambda event: events.append(event),
    )}
    tools["todo.add"].impl(title="A")
    # We expect one event after add. Shape verified in Task 9.
    assert len(events) == 1
    assert events[0]["action"] == "added"


def test_broadcast_receives_event_via_real_broadcaster(store):
    """End-to-end: a tool call -> _broadcast_todo -> TodoBroadcaster.publish ->
    subscriber sees the {type:todo_event, action:added, item:..., ...} dict."""
    from kc_supervisor.service import TodoBroadcaster
    bc = TodoBroadcaster()
    captured = []
    bc.subscribe(lambda e: captured.append(e))

    def emit(event):
        bc.publish({"type": "todo_event", **event})

    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    tools = {t.name: t for t in build_todo_tools(
        storage=store, current_context=lambda: ctx, broadcast=emit,
    )}
    tools["todo.add"].impl(title="A")
    assert len(captured) == 1
    e = captured[0]
    assert e["type"] == "todo_event"
    assert e["action"] == "added"
    assert e["item"]["title"] == "A"
    assert e["conversation_id"] == 40
    assert e["agent"] == "Kona-AI"
