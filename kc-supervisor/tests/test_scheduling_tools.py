from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.tools import build_scheduling_tools


def _make_service(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
    )
    svc.start()
    return svc, s


def _seed_conv(s: Storage) -> int:
    return s.create_conversation(agent="kona", channel="telegram")


def test_build_scheduling_tools_returns_four(tmp_path):
    svc, s = _make_service(tmp_path)
    try:
        tools = build_scheduling_tools(
            service=svc,
            current_context=lambda: {
                "conversation_id": 1, "channel": "telegram",
                "chat_id": "C1", "agent": "kona",
            },
        )
        names = {t.name for t in tools}
        assert names == {
            "schedule_reminder", "schedule_cron",
            "list_reminders", "cancel_reminder",
        }
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_reminder_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    try:
        result = schedule_reminder.impl(when="in 1 hour", content="dinner")
        assert "id" in result
        assert "fires_at_human" in result
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert rows[0]["payload"] == "dinner"
    finally:
        svc.shutdown()


def test_schedule_cron_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_cron = next(t for t in tools if t.name == "schedule_cron")
    try:
        result = schedule_cron.impl(cron="0 9 * * *", content="standup")
        assert "id" in result
        assert "human_summary" in result
    finally:
        svc.shutdown()


def test_list_reminders_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    list_reminders = next(t for t in tools if t.name == "list_reminders")
    try:
        schedule_reminder.impl(when="in 1 hour", content="x")
        out = list_reminders.impl(active_only=True)
        assert len(out["reminders"]) == 1
    finally:
        svc.shutdown()


def test_cancel_reminder_tool_invocation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    cancel_reminder = next(t for t in tools if t.name == "cancel_reminder")
    try:
        r = schedule_reminder.impl(when="in 1 hour", content="dinner")
        out = cancel_reminder.impl(id_or_description=str(r["id"]))
        assert out["ambiguous"] is False
        assert out["cancelled"][0]["id"] == r["id"]
    finally:
        svc.shutdown()


def test_cancel_reminder_ambiguous(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    ctx = lambda: {
        "conversation_id": cid, "channel": "telegram",
        "chat_id": "C1", "agent": "kona",
    }
    tools = build_scheduling_tools(service=svc, current_context=ctx)
    schedule_reminder = next(t for t in tools if t.name == "schedule_reminder")
    cancel_reminder = next(t for t in tools if t.name == "cancel_reminder")
    try:
        schedule_reminder.impl(when="in 1 hour", content="dinner mom")
        schedule_reminder.impl(when="in 2 hours", content="dinner res")
        out = cancel_reminder.impl(id_or_description="dinner")
        assert out["ambiguous"] is True
        assert len(out["candidates"]) == 2
    finally:
        svc.shutdown()
