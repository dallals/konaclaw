"""End-to-end Phase 2: schedule cross-channel + agent_phrased, fire, verify dispatch."""
from __future__ import annotations
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from kc_core.messages import AssistantMessage
from kc_supervisor.storage import Storage
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.scheduling.service import ScheduleService
from kc_supervisor.scheduling.runner import ReminderRunner


def _setup(tmp_path: Path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cm = ConversationManager(s)
    return s, cm


def test_e2e_literal_cross_channel_dashboard_to_telegram(tmp_path):
    s, cm = _setup(tmp_path)
    sched_cid = cm.start(agent="kona", channel="dashboard")

    # Build runner + service.
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")

    # Schedule literal cross-channel reminder.
    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=sched_cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="literal",
    )
    job_id = out["id"]

    # Fire manually (bypass APS scheduling delay).
    runner.fire(job_id)

    # Connector got the literal payload with prefix.
    connector.send.assert_called_once()
    chat_id, text = connector.send.call_args.args
    assert chat_id == "8627206839"
    assert text == "⏰ dinner"

    # Persisted to a conversation that was created on demand for telegram:8627206839.
    dest_cid = s.get_conv_for_chat("telegram", "8627206839", "kona")
    assert dest_cid is not None and dest_cid != sched_cid
    msgs = cm.list_messages(dest_cid)
    assert len(msgs) == 1
    assert isinstance(msgs[0], AssistantMessage)
    assert msgs[0].content == "⏰ dinner"

    # Row marked done.
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"


def test_e2e_agent_phrased_cross_channel(tmp_path):
    s, cm = _setup(tmp_path)
    sched_cid = cm.start(agent="kona", channel="dashboard")

    # Build a fake agent registry whose core_agent yields a Complete frame.
    class FakeToolRegistry:
        def names(self): return []
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry()
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        class FakeComplete:
            reply = AssistantMessage(content="Hey, dinner reminder!")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")

    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner trigger description",
        conversation_id=sched_cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    runner.fire(out["id"])

    # Composed text dispatched (no PREFIX).
    text = connector.send.call_args.args[1]
    assert text == "Hey, dinner reminder!"

    # Destination conversation has the composed message, NOT the trigger description.
    dest_cid = s.get_conv_for_chat("telegram", "8627206839", "kona")
    msgs = cm.list_messages(dest_cid)
    assert len(msgs) == 1
    assert msgs[0].content == "Hey, dinner reminder!"
