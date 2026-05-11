import pytest
from fastapi.testclient import TestClient

from kc_supervisor.storage import Storage
from kc_supervisor.todos.storage import TodoStorage


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up a minimal FastAPI app with /todos routes mounted against a
    real TodoStorage backed by a tmp SQLite."""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from kc_supervisor.http_routes import register_http_routes
    from kc_supervisor.approvals import ApprovalBroker

    s = Storage(tmp_path / "kc.db"); s.init()
    with s.connect() as c:
        c.execute("INSERT INTO conversations (id, agent, channel, started_at) VALUES (?,?,?,?)",
                  (40, "Kona-AI", "dashboard", 1.0))
    todo_storage = TodoStorage(s)

    app = FastAPI()
    app.state.deps = SimpleNamespace(
        storage=s, todo_storage=todo_storage, approvals=ApprovalBroker(),
        started_at=0.0, registry=None, conversations=None,
        news_client=None, schedule_service=None, skill_index=None,
        home=None,
    )
    register_http_routes(app)
    return TestClient(app)


def test_get_todos_empty(client):
    r = client.get("/todos?conversation_id=40")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "count": 0}


def test_post_creates_todo(client):
    r = client.post("/todos", json={"conversation_id": 40, "title": "A"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "A"
    assert body["scope"] == "conversation"


def test_post_missing_title_422(client):
    r = client.post("/todos", json={"conversation_id": 40, "title": ""})
    assert r.status_code == 422


def test_post_persist_true(client):
    r = client.post("/todos", json={"conversation_id": 40,
                                     "title": "P", "persist": True})
    body = r.json()
    assert body["scope"] == "agent"


def test_get_after_add_returns_item(client):
    client.post("/todos", json={"conversation_id": 40, "title": "A"})
    r = client.get("/todos?conversation_id=40")
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["title"] == "A"


def test_patch_updates_title(client):
    a = client.post("/todos", json={"conversation_id": 40, "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "title": "renamed"})
    assert r.status_code == 200
    assert r.json()["title"] == "renamed"


def test_patch_status_done(client):
    a = client.post("/todos", json={"conversation_id": 40, "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "status": "done"})
    assert r.json()["status"] == "done"


def test_patch_invalid_status_422(client):
    a = client.post("/todos", json={"conversation_id": 40, "title": "A"}).json()
    r = client.patch(f"/todos/{a['id']}",
                     json={"conversation_id": 40, "status": "garbage"})
    assert r.status_code == 422


def test_delete_removes(client):
    a = client.post("/todos", json={"conversation_id": 40, "title": "A"}).json()
    r = client.delete(f"/todos/{a['id']}?conversation_id=40")
    assert r.status_code == 204
    r2 = client.delete(f"/todos/{a['id']}?conversation_id=40")
    assert r2.status_code == 404


def test_bulk_delete_clear_done(client):
    for t in ("A", "B", "C"):
        client.post("/todos", json={"conversation_id": 40, "title": t})
    items = client.get("/todos?conversation_id=40").json()["items"]
    client.patch(f"/todos/{items[0]['id']}",
                 json={"conversation_id": 40, "status": "done"})
    client.patch(f"/todos/{items[1]['id']}",
                 json={"conversation_id": 40, "status": "done"})
    r = client.delete("/todos?conversation_id=40&scope=all&status=done")
    assert r.status_code == 200
    assert r.json() == {"deleted_count": 2}
