import asyncio

from fastapi.testclient import TestClient


def test_messages_endpoint_includes_scheduled_job_id(app_with_scheduler, deps_with_scheduler):
    """After a reminder fires, the assistant message it produced should carry the
    scheduled_job_id link in GET /conversations/{cid}/messages."""
    svc = deps_with_scheduler.schedule_service
    # Pre-map (channel, chat_id, agent) -> conversation_id=1 so the runner's
    # get_or_create resolves the destination back to conversation 1 instead of
    # creating a fresh conversation for the synthetic chat id.
    deps_with_scheduler.storage.put_conv_for_chat("dashboard", "c1", "alice", 1)
    res = svc.schedule_one_shot(
        when="in 1 second", content="hi", conversation_id=1,
        channel="dashboard", chat_id="c1", agent="alice",
    )
    from kc_supervisor.scheduling.runner import (
        ReminderRunner,
        clear_active_runner,
        fire_reminder,
        set_active_runner,
    )
    runner = ReminderRunner(
        storage=deps_with_scheduler.storage,
        conversations=deps_with_scheduler.conversations,
        connector_registry=None,
        coroutine_runner=lambda c: asyncio.run(c),
        broadcaster=deps_with_scheduler.reminders_broadcaster,
    )
    set_active_runner(runner)
    try:
        fire_reminder(res["id"])
        with TestClient(app_with_scheduler) as client:
            r = client.get("/conversations/1/messages")
            assert r.status_code == 200
            body = r.json()["messages"]
        stamped = [m for m in body if m.get("scheduled_job_id") == res["id"]]
        assert len(stamped) == 1
        # The non-stamped messages (if any) should not carry the field.
        for m in body:
            if m.get("scheduled_job_id") is None:
                assert "scheduled_job_id" not in m
    finally:
        clear_active_runner()
