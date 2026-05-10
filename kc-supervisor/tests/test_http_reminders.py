from __future__ import annotations
from fastapi.testclient import TestClient


def test_get_reminders_returns_pending_and_cancelled(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    a = svc.schedule_one_shot(when="in 1 hour", content="A", conversation_id=1,
                              channel="dashboard", chat_id="c1", agent="alice")
    b = svc.schedule_one_shot(when="in 2 hours", content="B", conversation_id=1,
                              channel="dashboard", chat_id="c1", agent="alice")
    svc.cancel_reminder(str(b["id"]), conversation_id=1, scope="user")

    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders")
        assert r.status_code == 200
        body = r.json()
        assert "reminders" in body
        ids = {row["id"] for row in body["reminders"]}
        assert a["id"] in ids and b["id"] in ids


def test_get_reminders_filters_by_status_and_kind(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    one = svc.schedule_one_shot(when="in 1 hour", content="o", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    cron = svc.schedule_cron(cron="0 9 * * *", content="c", conversation_id=1,
                             channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders?kind=cron")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()["reminders"]]
        assert cron["id"] in ids and one["id"] not in ids

        r2 = client.get("/reminders?status=pending&kind=reminder")
        ids2 = [row["id"] for row in r2.json()["reminders"]]
        assert one["id"] in ids2 and cron["id"] not in ids2


def test_get_reminders_invalid_status_returns_422(app_with_scheduler):
    with TestClient(app_with_scheduler) as client:
        r = client.get("/reminders?status=bogus")
        assert r.status_code == 422
