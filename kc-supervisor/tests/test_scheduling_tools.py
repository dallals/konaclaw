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


def test_scheduling_tools_not_registered_on_non_kona(tmp_path):
    """Phase 1 invariant: only Kona gets the scheduling tools."""
    import yaml
    from kc_core.config import AgentConfig
    from kc_sandbox.shares import SharesRegistry
    from kc_supervisor.approvals import ApprovalBroker
    from kc_supervisor.assembly import assemble_agent

    # Construct a real ScheduleService so the gate's `schedule_service is not None`
    # branch CAN fire — we want to assert the cfg.name=="kona" gate, not the
    # service-presence gate.
    home = tmp_path / "kc-home"
    (home / "config").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))

    storage = Storage(home / "data" / "kc.db")
    storage.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=storage, runner=runner, db_path=home / "data" / "kc.db",
        timezone="America/Los_Angeles",
    )

    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg_alice = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="I am alice.")

    try:
        assembled_alice = assemble_agent(
            cfg=cfg_alice,
            shares=shares,
            audit_storage=storage,
            broker=broker,
            ollama_url="http://localhost:11434",
            default_model="qwen2.5:7b",
            undo_db_path=home / "data" / "undo.db",
            schedule_service=svc,
        )
        tool_names = set(assembled_alice.registry.names())
        scheduling = {"schedule_reminder", "schedule_cron",
                      "list_reminders", "cancel_reminder"}
        assert scheduling.isdisjoint(tool_names), (
            f"Non-Kona agent has scheduling tools: {tool_names & scheduling}"
        )
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


def test_schedule_reminder_tool_accepts_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    schema = sched.parameters
    assert "target_channel" in schema["properties"]
    assert "mode" in schema["properties"]
    assert "target_channel" not in schema["required"]
    assert "mode" not in schema["required"]


def test_schedule_reminder_tool_forwards_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    sched.impl(when="5pm", content="x", target_channel="telegram", mode="agent_phrased")
    kwargs = svc.schedule_one_shot.call_args.kwargs
    assert kwargs["target_channel"] == "telegram"
    assert kwargs["mode"] == "agent_phrased"


def test_schedule_reminder_tool_defaults_when_args_omitted():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_one_shot.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    sched = next(t for t in tools if t.name == "schedule_reminder")
    sched.impl(when="5pm", content="x")
    kwargs = svc.schedule_one_shot.call_args.kwargs
    assert kwargs["target_channel"] == "current"
    assert kwargs["mode"] == "literal"


def test_schedule_cron_tool_accepts_target_channel_and_mode():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.schedule_cron.return_value = {"id": 1}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    cron_tool = next(t for t in tools if t.name == "schedule_cron")
    cron_tool.impl(cron="0 9 * * *", content="x", target_channel="telegram", mode="agent_phrased")
    kwargs = svc.schedule_cron.call_args.kwargs
    assert kwargs["target_channel"] == "telegram"
    assert kwargs["mode"] == "agent_phrased"


def test_list_reminders_tool_accepts_scope():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.list_reminders.return_value = {"reminders": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    lst = next(t for t in tools if t.name == "list_reminders")
    assert "scope" in lst.parameters["properties"]
    lst.impl(scope="conversation")
    assert svc.list_reminders.call_args.kwargs["scope"] == "conversation"


def test_list_reminders_tool_default_scope_user():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.list_reminders.return_value = {"reminders": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    lst = next(t for t in tools if t.name == "list_reminders")
    lst.impl()
    assert svc.list_reminders.call_args.kwargs["scope"] == "user"


def test_cancel_reminder_tool_accepts_scope():
    from kc_supervisor.scheduling.tools import build_scheduling_tools
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.cancel_reminder.return_value = {"ambiguous": False, "candidates": [], "cancelled": []}
    ctx = {"conversation_id": 1, "channel": "dashboard", "chat_id": "ws-1", "agent": "kona"}
    tools = build_scheduling_tools(svc, lambda: ctx)
    can = next(t for t in tools if t.name == "cancel_reminder")
    assert "scope" in can.parameters["properties"]
    can.impl(id_or_description="5", scope="conversation")
    assert svc.cancel_reminder.call_args.kwargs["scope"] == "conversation"
