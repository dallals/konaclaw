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


def test_audit_endpoint_decision_filter(app, deps):
    deps.storage.append_audit(agent="kona", tool="t1", args_json="{}", decision="allowed", result="ok", undoable=False)
    deps.storage.append_audit(agent="kona", tool="t2", args_json="{}", decision="denied", result="r", undoable=False)

    with TestClient(app) as client:
        all_rows = client.get("/audit").json()["entries"]
        only_denied = client.get("/audit?decision=denied").json()["entries"]

    assert len(all_rows) == 2
    assert len(only_denied) == 1
    assert only_denied[0]["decision"] == "denied"


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


def test_undo_round_trips_memory_append(tmp_path):
    """End-to-end memory.append + POST /undo. The supervisor must merge the
    memory journal into Undoer.journals so reverse_payload share="memory"
    can be resolved."""
    import yaml
    from fastapi.testclient import TestClient
    from kc_sandbox.shares import SharesRegistry
    from kc_supervisor.storage import Storage
    from kc_supervisor.approvals import ApprovalBroker
    from kc_supervisor.agents import AgentRegistry
    from kc_supervisor.conversations import ConversationManager
    from kc_supervisor.locks import ConversationLocks
    from kc_supervisor.service import Deps, create_app
    from kc_supervisor.audit_tools import _decision_contextvar, _eid_contextvar
    from kc_sandbox.permissions import Decision, Tier

    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: hi\n"
    )

    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
        memory_root=home / "memory",
    )
    registry.load_all()
    deps = Deps(
        storage=storage, registry=registry,
        conversations=ConversationManager(storage), approvals=broker,
        home=home, shares=shares, conv_locks=ConversationLocks(),
    )
    app = create_app(deps)

    rt = registry.get("alice")
    assert rt.assembled is not None
    assert "memory.append" in rt.assembled.registry.names()

    _decision_contextvar.set(Decision(allowed=True, tier=Tier.MUTATING, source="tier", reason=None))
    _eid_contextvar.set(None)
    rt.assembled.registry.invoke("memory.append", {
        "scope": "user",
        "content": "Sammy prefers concise replies.\n",
    })
    user_md = home / "memory" / "user.md"
    assert "concise replies" in user_md.read_text()

    rows = storage.list_audit()
    mem_rows = [r for r in rows if r["tool"] == "memory.append"]
    assert len(mem_rows) == 1
    aid = mem_rows[0]["id"]
    assert storage.get_undo_op_for_audit(aid) is not None

    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 200, r.json()
    assert r.json()["reversed"]["kind"] == "git-revert"
    # Memory file is reverted to its pre-append state (no concise-replies line).
    # When the append was the file's first commit, revert removes it entirely;
    # either case is correct — the appended content is gone.
    assert (not user_md.exists()) or "concise replies" not in user_md.read_text()

    # The audit row now reports undone=1 so the dashboard can hide its Undo
    # button and prevent a double-click that would 500.
    fresh = next(r for r in storage.list_audit() if r["id"] == aid)
    assert fresh["undone"] == 1
    # And re-undoing now returns 200 with kind="noop" (the user's intent —
    # undo this — is already satisfied; we backfill undone_at if it wasn't
    # already set so the dashboard stops offering the button).
    with TestClient(app) as client:
        r2 = client.post(f"/undo/{aid}")
    assert r2.status_code == 200
    assert r2.json()["reversed"]["kind"] == "noop"


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


def test_post_agents_creates_yaml_and_registry_picks_it_up(app, deps):
    body = {"name": "carol", "system_prompt": "I am carol", "model": "fake-model"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 200
    snap = r.json()
    assert snap["name"] == "carol"
    assert snap["status"] in ("idle", "degraded")
    yaml_path = deps.home / "agents" / "carol.yaml"
    assert yaml_path.exists()
    assert "carol" in deps.registry.names()


def test_post_agents_collision_returns_409(app, deps):
    """alice already exists in the fixture. POSTing alice again should 409."""
    body = {"name": "alice", "system_prompt": "another alice"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 409
    assert "exists" in r.json()["detail"].lower()


def test_post_agents_invalid_name_returns_422(app, deps):
    """Names with path traversal or starting with non-letter are rejected."""
    bad_names = ["../evil", "0name", "name with space", "x" * 80]
    for name in bad_names:
        with TestClient(app) as client:
            r = client.post("/agents", json={"name": name, "system_prompt": "x"})
        assert r.status_code == 422, f"expected 422 for {name!r}, got {r.status_code}"
        assert not (deps.home / "agents" / f"{name}.yaml").exists()


def test_post_agents_uses_default_model_when_omitted(app, deps):
    """When model is omitted from the body, the YAML still validates against the
    registry's default_model fallback."""
    body = {"name": "dave", "system_prompt": "hi"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 200
    yaml_text = (deps.home / "agents" / "dave.yaml").read_text()
    assert "name: dave" in yaml_text
    # The fixture's default_model is "fake-model" — the registry uses it as fallback
    # because the YAML omits a model field
    rt = deps.registry.get("dave")
    assert rt.model == "fake-model"


def test_cors_allows_dashboard_origins(app):
    with TestClient(app) as client:
        for origin in (
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8766",
            "http://localhost:8766",
        ):
            r = client.get("/health", headers={"Origin": origin})
            assert r.status_code == 200
            assert r.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_for_post(app):
    with TestClient(app) as client:
        r = client.options(
            "/agents/alice/conversations",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert "POST" in r.headers.get("access-control-allow-methods", "")


def test_cors_blocks_unknown_origin(app):
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_patch_conversation_pin_and_list_order(app):
    with TestClient(app) as client:
        a = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        b = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.patch(f"/conversations/{a}", json={"pinned": True})
        assert r.status_code == 200
        assert r.json()["pinned"] == 1
        convs = client.get("/conversations?agent=alice").json()["conversations"]
    assert convs[0]["id"] == a
    assert convs[0]["pinned"] == 1
    assert convs[1]["id"] == b


def test_patch_conversation_unknown_404(app):
    with TestClient(app) as client:
        r = client.patch("/conversations/99999", json={"pinned": True})
    assert r.status_code == 404


def test_delete_conversation_cascades_messages(app, deps):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        deps.storage.append_message(cid, role="user", content="hi", tool_call_json=None)
        r = client.delete(f"/conversations/{cid}")
        assert r.status_code == 204
        m = client.get(f"/conversations/{cid}/messages")
    assert m.status_code == 404


def test_delete_conversation_unknown_404(app):
    with TestClient(app) as client:
        r = client.delete("/conversations/99999")
    assert r.status_code == 404


def test_patch_conversation_sets_title(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.patch(f"/conversations/{cid}", json={"title": "Trip planning"})
        assert r.status_code == 200
        assert r.json()["title"] == "Trip planning"


def test_patch_conversation_clears_title_with_empty_string(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        client.patch(f"/conversations/{cid}", json={"title": "x"})
        r = client.patch(f"/conversations/{cid}", json={"title": ""})
    assert r.status_code == 200
    assert r.json()["title"] is None


def test_patch_conversation_pin_and_title_together(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.patch(f"/conversations/{cid}", json={"pinned": True, "title": "Important"})
    assert r.status_code == 200
    body = r.json()
    assert body["pinned"] == 1
    assert body["title"] == "Important"


def test_patch_conversation_empty_body_422(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.patch(f"/conversations/{cid}", json={})
    assert r.status_code == 422


def test_delete_agent_removes_yaml_and_reloads(app, deps):
    target = deps.home / "agents" / "alice.yaml"
    assert target.exists()
    with TestClient(app) as client:
        assert "alice" in [a["name"] for a in client.get("/agents").json()["agents"]]
        r = client.delete("/agents/alice")
        assert r.status_code == 204
        assert not target.exists()
        names = [a["name"] for a in client.get("/agents").json()["agents"]]
    assert "alice" not in names


def test_delete_agent_unknown_404(app):
    with TestClient(app) as client:
        r = client.delete("/agents/ghost")
    assert r.status_code == 404


def test_delete_agent_keeps_conversations_and_audit(app, deps):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        deps.storage.append_audit(
            agent="alice", tool="t", args_json="{}", decision="d", result="r", undoable=False,
        )
        client.delete("/agents/alice")
        convs = client.get("/conversations").json()["conversations"]
        audit = client.get("/audit").json()["entries"]
    assert any(c["id"] == cid and c["agent"] == "alice" for c in convs)
    assert any(e["agent"] == "alice" for e in audit)


def test_get_models_returns_sorted_list(app):
    """GET /models proxies /api/tags from Ollama and sorts the names."""
    import respx
    import httpx

    fake_payload = {
        "models": [
            {"name": "qwen2.5:7b"},
            {"name": "gemma3:4b"},
        ],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json=fake_payload),
        )
        with TestClient(app) as client:
            r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    names = [m["name"] for m in body["models"]]
    assert names == sorted(names)
    assert names == ["gemma3:4b", "qwen2.5:7b"]


def test_get_models_swallows_unreachable(app):
    """When Ollama is unreachable, /models returns 200 with empty list + error msg."""
    import respx
    import httpx

    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:11434/api/tags").mock(
            side_effect=httpx.ConnectError("nope"),
        )
        with TestClient(app) as client:
            r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == []
    assert "error" in body
    assert isinstance(body["error"], str)


def test_patch_agent_updates_model(app, deps):
    """PATCH /agents/{name} with new model writes YAML and reloads registry."""
    with TestClient(app) as client:
        r = client.patch("/agents/alice", json={"model": "newmodel:tag"})
    assert r.status_code == 200
    assert r.json()["model"] == "newmodel:tag"

    yaml_text = (deps.home / "agents" / "alice.yaml").read_text()
    assert "model: newmodel:tag" in yaml_text

    with TestClient(app) as client:
        agents = client.get("/agents").json()["agents"]
    alice = next(a for a in agents if a["name"] == "alice")
    assert alice["model"] == "newmodel:tag"


def test_patch_agent_preserves_system_prompt(app, deps):
    """Patching only model leaves system_prompt unchanged on disk."""
    target = deps.home / "agents" / "alice.yaml"
    original = target.read_text()
    assert "hi from alice" in original

    with TestClient(app) as client:
        r = client.patch("/agents/alice", json={"model": "another:tag"})
    assert r.status_code == 200

    new_text = target.read_text()
    assert "hi from alice" in new_text
    assert "model: another:tag" in new_text


def test_patch_agent_invalid_model_with_newline_422(app):
    with TestClient(app) as client:
        r = client.patch("/agents/alice", json={"model": "bad\nmodel"})
    assert r.status_code == 422
    assert "newline" in r.json()["detail"].lower()


def test_patch_agent_invalid_model_empty_422(app):
    with TestClient(app) as client:
        r = client.patch("/agents/alice", json={"model": "   "})
    assert r.status_code == 422


def test_patch_unknown_agent_404(app):
    with TestClient(app) as client:
        r = client.patch("/agents/ghost", json={"model": "x:y"})
    assert r.status_code == 404
    assert "unknown agent" in r.json()["detail"].lower()


def test_list_messages_route_echoes_usage(app, deps):
    from kc_core.messages import AssistantMessage
    cid = deps.conversations.start("alice", "dashboard")
    deps.conversations.append(
        cid, AssistantMessage(content="hi"),
        usage={"output_tokens": 4, "ttfb_ms": 50.0, "generation_ms": 100.0,
               "input_tokens": 10, "calls": 1, "usage_reported": True},
    )
    with TestClient(app) as client:
        r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assistant = [m for m in msgs if m["type"] == "AssistantMessage"][0]
    assert assistant["usage"] == {
        "output_tokens": 4, "ttfb_ms": 50.0, "generation_ms": 100.0,
        "input_tokens": 10, "calls": 1, "usage_reported": True,
    }


def test_list_messages_route_omits_usage_for_legacy_rows(app, deps):
    cid = deps.conversations.start("alice", "dashboard")
    # Append directly via storage with no usage_json — simulates pre-migration data.
    deps.storage.append_message(cid, "assistant", "legacy", None)
    with TestClient(app) as client:
        r = client.get(f"/conversations/{cid}/messages")
    msgs = r.json()["messages"]
    assistant = [m for m in msgs if m["type"] == "AssistantMessage"][0]
    assert "usage" not in assistant
