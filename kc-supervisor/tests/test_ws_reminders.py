from __future__ import annotations
from fastapi.testclient import TestClient


def test_ws_reminders_pushes_lifecycle_events(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    with TestClient(app_with_scheduler) as client:
        with client.websocket_connect("/ws/reminders") as ws:
            res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                        channel="dashboard", chat_id="c1", agent="alice")
            msg = ws.receive_json()
            assert msg["type"] == "reminder.created"
            assert msg["reminder"]["id"] == res["id"]
            assert "ts" in msg


def test_ws_reminders_disconnect_cleans_up(app_with_scheduler, deps_with_scheduler):
    broadcaster = deps_with_scheduler.reminders_broadcaster
    with TestClient(app_with_scheduler) as client:
        with client.websocket_connect("/ws/reminders") as ws:
            pass
    assert len(broadcaster._subs) == 0
