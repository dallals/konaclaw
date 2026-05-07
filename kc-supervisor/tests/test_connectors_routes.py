from fastapi.testclient import TestClient


def test_get_connectors_lists_five_with_status(app):
    with TestClient(app) as client:
        res = client.get("/connectors")
    assert res.status_code == 200
    body = res.json()
    names = [c["name"] for c in body["connectors"]]
    assert names == ["telegram", "imessage", "gmail", "calendar", "zapier"]
    for c in body["connectors"]:
        assert "status" in c
        assert c["status"] in ("not_configured", "connected", "unavailable", "error")


def test_get_telegram_detail_shape_when_unconfigured(app):
    with TestClient(app) as client:
        res = client.get("/connectors/telegram")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "telegram"
    assert body["has_token"] is False
    assert body["token_hint"] is None
    assert body["allowlist"] == []
    assert "bot_token" not in body  # plaintext NEVER returned


def test_get_unknown_connector_returns_404(app):
    with TestClient(app) as client:
        res = client.get("/connectors/nope")
    assert res.status_code == 404


def test_patch_telegram_persists_through_secrets_store(app):
    with TestClient(app) as client:
        res = client.patch("/connectors/telegram", json={
            "bot_token": "8000:secret",
            "allowlist": ["@alice", "@bob"],
        })
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        assert "bot_token" not in res.json()

        detail = client.get("/connectors/telegram").json()
    assert detail["has_token"] is True
    assert detail["token_hint"] == "...cret"
    assert detail["allowlist"] == ["@alice", "@bob"]


def test_patch_zapier_api_key(app):
    with TestClient(app) as client:
        client.patch("/connectors/zapier", json={"api_key": "zk_live_xyz"})
        detail = client.get("/connectors/zapier").json()
    assert detail["has_token"] is True
    assert detail["token_hint"] == "..._xyz"


def test_patch_imessage_allowlist_only(app):
    with TestClient(app) as client:
        client.patch("/connectors/imessage", json={"allowlist": ["+15551234567"]})
        detail = client.get("/connectors/imessage").json()
    assert detail["allowlist"] == ["+15551234567"]


def test_patch_unknown_connector_returns_404(app):
    with TestClient(app) as client:
        res = client.patch("/connectors/nope", json={"x": 1})
    assert res.status_code == 404


def test_google_status_initial_is_idle(app):
    with TestClient(app) as client:
        body = client.get("/connectors/google/status").json()
    assert body["state"] == "idle"
    assert body["last_error"] is None


def test_google_connect_returns_202_pending(app, monkeypatch):
    # Patch the InstalledAppFlow runner so the test doesn't open a browser.
    import kc_supervisor.connectors_routes as cr
    monkeypatch.setattr(cr, "_run_google_flow", lambda deps: None)

    with TestClient(app) as client:
        res = client.post("/connectors/google/connect")
    assert res.status_code == 202
    assert res.json()["state"] == "pending"


def test_google_connect_double_click_is_noop_while_pending(app, monkeypatch):
    import threading as _threading
    import kc_supervisor.connectors_routes as cr
    started = []
    started_event = _threading.Event()

    def fake_runner(deps):
        started.append(1)
        started_event.set()
        # Don't change deps.google_oauth.state — we want the second POST to
        # observe "pending" and short-circuit.

    monkeypatch.setattr(cr, "_run_google_flow", fake_runner)
    with TestClient(app) as client:
        r1 = client.post("/connectors/google/connect")
        # Wait for the first thread's runner to actually execute, so we can be
        # sure the second POST observes state="pending" set by the endpoint.
        # (The endpoint sets state BEFORE Thread.start(), so this is belt-and-
        # suspenders, but the wait also guarantees `started` is populated by
        # the time we assert.)
        assert started_event.wait(timeout=2.0)
        r2 = client.post("/connectors/google/connect")
    assert r1.status_code == 202
    assert r2.status_code == 202
    # Only one flow kicked off; second POST saw state="pending" and skipped.
    assert len(started) == 1


def test_zapier_zaps_returns_empty_when_unconfigured(app):
    # deps.mcp_manager is unset on the conftest fixture, so the route's
    # `if manager is not None` guard short-circuits to an empty live list.
    with TestClient(app) as client:
        body = client.get("/connectors/zapier/zaps").json()
    assert body == {"zaps": []}


def test_zapier_refresh_calls_registry_load_all(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.state.deps.registry, "load_all",
                        lambda: calls.append(1))
    with TestClient(app) as client:
        res = client.post("/connectors/zapier/refresh")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert calls == [1]


def test_google_disconnect_resets_state(app, deps):
    # Simulate a previously-completed flow: write a token file and mark
    # state="connected", then verify disconnect clears both.
    deps.google_token_path.write_text('{"token":"fake"}')
    deps.google_oauth.state = "connected"

    with TestClient(app) as client:
        res = client.post("/connectors/google/disconnect")
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        body = client.get("/connectors/google/status").json()
    assert body["state"] == "idle"
    assert not deps.google_token_path.exists()
