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
