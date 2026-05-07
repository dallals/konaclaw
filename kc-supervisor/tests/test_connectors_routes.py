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
