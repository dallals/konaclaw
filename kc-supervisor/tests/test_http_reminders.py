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
        body = r.json()
        assert body["detail"]["error"] == "invalid_status"
        assert body["detail"]["invalid"] == ["bogus"]
        assert "pending" in body["detail"]["allowed"]


def test_get_reminders_503_when_no_scheduler(app):
    """The bare `app` fixture has schedule_service=None — endpoint returns 503."""
    with TestClient(app) as client:
        r = client.get("/reminders")
        assert r.status_code == 503


def test_delete_reminder_cancels_pending(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.delete(f"/reminders/{res['id']}")
        assert r.status_code == 204
    row = deps_with_scheduler.storage.get_scheduled_job(res["id"])
    assert row["status"] == "cancelled"


def test_delete_reminder_unknown_id_returns_404(app_with_scheduler):
    with TestClient(app_with_scheduler) as client:
        r = client.delete("/reminders/999999")
        assert r.status_code == 404


def test_delete_reminder_already_cancelled_returns_409(app_with_scheduler, deps_with_scheduler):
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        client.delete(f"/reminders/{res['id']}")
        r = client.delete(f"/reminders/{res['id']}")
        assert r.status_code == 409


def test_patch_reminder_snoozes(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    new_when = time.time() + 3600 * 3
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": new_when})
        assert r.status_code == 200
        body = r.json()
        assert abs(body["when_utc"] - new_when) < 1.0


def test_patch_reminder_cron_returns_409(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_cron(cron="0 9 * * *", content="x", conversation_id=1,
                            channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": time.time() + 3600})
        assert r.status_code == 409
        assert r.json().get("detail", {}).get("code") == "cron_not_snoozable"


def test_patch_reminder_past_time_returns_422(app_with_scheduler, deps_with_scheduler):
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": time.time() - 60})
        assert r.status_code == 422


def test_patch_reminder_unknown_returns_404(app_with_scheduler):
    import time
    with TestClient(app_with_scheduler) as client:
        r = client.patch("/reminders/999999", json={"when_utc": time.time() + 3600})
        assert r.status_code == 404


def test_patch_reminder_already_fired_returns_409(app_with_scheduler, deps_with_scheduler):
    """If the reminder's status is no longer pending (e.g., done/failed/cancelled),
    PATCH returns 409 with code=already_fired."""
    import time
    svc = deps_with_scheduler.schedule_service
    res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=1,
                                channel="dashboard", chat_id="c1", agent="alice")
    # Mark the reminder as done out-of-band to simulate a fired-then-snoozed race.
    deps_with_scheduler.storage.update_scheduled_job_status(res["id"], "done")
    with TestClient(app_with_scheduler) as client:
        r = client.patch(f"/reminders/{res['id']}", json={"when_utc": time.time() + 3600})
        assert r.status_code == 409
        body = r.json()
        assert body["detail"]["code"] == "already_fired"
        assert "message" in body["detail"]
