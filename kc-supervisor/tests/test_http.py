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


def test_list_messages_unknown_cid_returns_404(app):
    with TestClient(app) as client:
        r = client.get("/conversations/99999/messages")
    assert r.status_code == 404
    assert "unknown conversation" in r.json()["detail"]


def test_undo_unknown_audit_id_returns_404(app):
    with TestClient(app) as client:
        r = client.post("/undo/99999")
    assert r.status_code == 404
    assert "unknown audit" in r.json()["detail"].lower()


def test_undo_audit_with_no_link_returns_422(app, deps):
    """An audit row with no audit_undo_link entry is not undoable (e.g. file.read)."""
    aid = deps.storage.append_audit(
        agent="alice", tool="file.read", args_json="{}",
        decision="tier", result="ok", undoable=False,
    )
    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 422
    assert "no journal op" in r.json()["detail"].lower()


def test_undo_happy_path_reverses_a_real_file_write(app, deps):
    """End-to-end: write a file via the assembled agent's tool registry,
    then undo via POST /undo/{audit_id}. The file should disappear."""
    rt = deps.registry.get("alice")
    assert rt.assembled is not None

    from kc_supervisor.audit_tools import _decision_contextvar, _eid_contextvar
    from kc_sandbox.permissions import Decision, Tier
    _decision_contextvar.set(Decision(allowed=True, tier=Tier.MUTATING, source="tier", reason=None))
    _eid_contextvar.set(None)

    target = "hello.txt"
    rt.assembled.registry.invoke("file.write", {
        "share": "main", "relpath": target, "content": "hi from test",
    })

    share_root = deps.shares.get("main").path
    assert (share_root / target).exists()

    rows = deps.storage.list_audit()
    write_rows = [r for r in rows if r["tool"] == "file.write"]
    assert len(write_rows) == 1
    aid = write_rows[0]["id"]
    assert deps.storage.get_undo_op_for_audit(aid) is not None

    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 200
    body = r.json()
    assert "reversed" in body
    assert body["reversed"]["kind"] == "git-revert"

    # File should be reverted (gone)
    assert not (share_root / target).exists()


def test_undo_returns_500_on_undoer_failure(app, deps):
    """If the Undoer raises (sha doesn't exist), /undo returns 500 with audit_id in body."""
    rt = deps.registry.get("alice")
    assert rt.assembled is not None

    from kc_sandbox.undo import UndoEntry

    # Manually record a fake undo entry pointing at a nonexistent sha
    eid = rt.assembled.undo_log.record(UndoEntry(
        agent="alice", tool="file.write",
        reverse_kind="git-revert",
        reverse_payload={"share": "main", "sha": "deadbeefdeadbeef"},
    ))
    aid = deps.storage.append_audit(
        agent="alice", tool="file.write", args_json="{}",
        decision="tier", result="wrote", undoable=True,
    )
    deps.storage.link_audit_undo(aid, eid)

    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"].startswith("undo failed")
    assert body.get("audit_id") == aid
