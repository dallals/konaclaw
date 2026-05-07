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
