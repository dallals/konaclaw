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
