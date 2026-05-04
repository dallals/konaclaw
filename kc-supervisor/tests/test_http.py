from fastapi.testclient import TestClient


def test_health_returns_ok(app):
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_s" in body
    assert isinstance(body["uptime_s"], (int, float))
    assert body["agents"] == 2  # alice + bob from fixture


def test_list_agents(app):
    with TestClient(app) as client:
        r = client.get("/agents")
    assert r.status_code == 200
    names = [a["name"] for a in r.json()["agents"]]
    assert "alice" in names
    assert "bob" in names


def test_list_conversations_empty(app):
    with TestClient(app) as client:
        r = client.get("/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_create_conversation(app):
    with TestClient(app) as client:
        r = client.post("/agents/alice/conversations", json={"channel": "dashboard"})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]
    assert isinstance(cid, int)


def test_create_conversation_unknown_agent(app):
    with TestClient(app) as client:
        r = client.post("/agents/ghost/conversations", json={"channel": "dashboard"})
    assert r.status_code == 404
    assert "unknown agent" in r.json()["detail"]


def test_list_conversations_filters_by_agent(app):
    with TestClient(app) as client:
        client.post("/agents/alice/conversations", json={"channel": "dashboard"})
        client.post("/agents/bob/conversations", json={"channel": "dashboard"})
        r = client.get("/conversations?agent=alice")
    assert r.status_code == 200
    convs = r.json()["conversations"]
    assert len(convs) == 1
    assert convs[0]["agent"] == "alice"


def test_list_messages_for_conversation(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


def test_audit_endpoint(app, deps):
    deps.storage.append_audit(
        agent="alice", tool="file.read",
        args_json='{"share":"r"}', decision="safe·auto", result="ok", undoable=False,
    )
    with TestClient(app) as client:
        r = client.get("/audit")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["tool"] == "file.read"


def test_audit_endpoint_filter_by_agent(app, deps):
    deps.storage.append_audit(agent="alice", tool="x", args_json="{}",
                              decision="d", result="r", undoable=False)
    deps.storage.append_audit(agent="bob", tool="y", args_json="{}",
                              decision="d", result="r", undoable=False)
    with TestClient(app) as client:
        r = client.get("/audit?agent=alice")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["agent"] == "alice"


def test_undo_returns_501(app, deps):
    aid = deps.storage.append_audit(
        agent="alice", tool="file.delete", args_json="{}",
        decision="destructive·callback", result="ok", undoable=True,
    )
    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 501
    assert "not yet wired" in r.json()["detail"]


def test_list_messages_unknown_cid_returns_404(app):
    with TestClient(app) as client:
        r = client.get("/conversations/99999/messages")
    assert r.status_code == 404
    assert "unknown conversation" in r.json()["detail"]
