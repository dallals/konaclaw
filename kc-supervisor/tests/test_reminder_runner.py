from __future__ import annotations
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.runner import ReminderRunner


def _make_runner(tmp_path) -> tuple[ReminderRunner, Storage, MagicMock, MagicMock]:
    s = Storage(tmp_path / "kc.db")
    s.init()
    cm = MagicMock()
    # Default: get_or_create returns whatever conversation_id matches the (channel, chat_id, agent)
    # mapping, falling back to creating a new conversation. Tests that need a different destination
    # override this on `cm.get_or_create`.
    cm.get_or_create.side_effect = lambda channel, chat_id, agent: (
        s.get_conv_for_chat(channel, chat_id, agent)
        or s.create_conversation(agent=agent, channel=channel)
    )
    connector_registry = MagicMock()
    connector = MagicMock()
    connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    return runner, s, cm, connector_registry


def _seed(s: Storage, cm: MagicMock, *, kind: str = "reminder") -> int:
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    return s.add_scheduled_job(
        kind=kind, agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=time.time() + 60 if kind == "reminder" else None,
        cron_spec=None if kind == "reminder" else "0 9 * * *",
    )


def test_fire_sends_via_connector_with_prefix(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    connector = registry.get.return_value
    connector.send.assert_called_once()
    args, kwargs = connector.send.call_args
    chat_id, content = args[0], args[1]
    assert chat_id == "C1"
    assert content == "⏰ dinner"


def test_fire_persists_assistant_message(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    cm.append.assert_called_once()
    args, kwargs = cm.append.call_args
    conversation_id, message = args[0], args[1]
    assert message.__class__.__name__ == "AssistantMessage"
    assert message.content == "⏰ dinner"


def test_fire_marks_one_shot_done(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"
    assert row["attempts"] == 1
    assert row["last_fired_at"] is not None


def test_fire_keeps_cron_pending(tmp_path):
    runner, s, cm, _ = _make_runner(tmp_path)
    job_id = _seed(s, cm, kind="cron")
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "pending"
    assert row["attempts"] == 1


def test_fire_unknown_job_id_is_noop(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    runner.fire(99999)
    connector = registry.get.return_value
    connector.send.assert_not_called()
    cm.append.assert_not_called()


def test_fire_connector_failure_marks_failed(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    connector = registry.get.return_value
    connector.send.side_effect = RuntimeError("network down")
    job_id = _seed(s, cm)
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    cm.append.assert_not_called()


def test_fire_persist_failure_still_marks_done(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    cm.append.side_effect = Exception("DB lock")
    job_id = _seed(s, cm)
    runner.fire(job_id)
    connector = registry.get.return_value
    connector.send.assert_called_once()  # User got the message
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"
    assert row["attempts"] == 1


def test_fire_cron_connector_failure_marks_failed(tmp_path):
    runner, s, cm, registry = _make_runner(tmp_path)
    connector = registry.get.return_value
    connector.send.side_effect = RuntimeError("403")
    job_id = _seed(s, cm, kind="cron")
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"


def test_fire_dashboard_channel_persists_without_connector(tmp_path):
    """Dashboard reminders skip the connector and persist directly."""
    runner, s, cm, registry = _make_runner(tmp_path)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id=f"dashboard:{cid}",
        payload="dashboard reminder",
        when_utc=time.time() + 60, cron_spec=None,
    )
    runner.fire(job_id)
    # Connector NOT called
    connector = registry.get.return_value
    connector.send.assert_not_called()
    # AssistantMessage IS persisted
    cm.append.assert_called_once()
    args = cm.append.call_args.args
    assert args[1].content == "⏰ dashboard reminder"
    # Status flipped to done
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "done"


def test_fire_dashboard_persist_failure_marks_failed(tmp_path):
    """Dashboard reminders that fail to persist should be marked failed (since
    persist IS the user-visible side effect for this channel)."""
    runner, s, cm, registry = _make_runner(tmp_path)
    cm.append.side_effect = Exception("DB lock")
    cid = s.create_conversation(agent="kona", channel="dashboard")
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id=f"dashboard:{cid}",
        payload="x",
        when_utc=time.time() + 60, cron_spec=None,
    )
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"


def test_fire_persists_to_destination_conversation_for_cross_channel(tmp_path):
    """When a row's channel differs from where it was scheduled, persist to the
    destination conversation (resolved via get_or_create), not the originating one."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    # Scheduling conversation: dashboard. Destination: telegram.
    sched_cid = s.create_conversation(agent="kona", channel="dashboard")
    dest_cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", dest_cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=sched_cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=1.0, cron_spec=None,
    )
    cm = MagicMock()
    cm.get_or_create.return_value = dest_cid
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    runner.fire(job_id)
    cm.get_or_create.assert_called_once_with(channel="telegram", chat_id="C1", agent="kona")
    cm.append.assert_called_once()
    persisted_cid = cm.append.call_args.args[0]
    assert persisted_cid == dest_cid


def test_fire_dashboard_destination_takes_dashboard_branch(tmp_path):
    """A row with channel=dashboard never invokes the connector, even when
    scheduled from telegram. Persists directly to the dashboard conversation."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    sched_cid = s.create_conversation(agent="kona", channel="telegram")
    dest_cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", dest_cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=sched_cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None,
    )
    cm = MagicMock()
    cm.get_or_create.return_value = dest_cid
    connector_registry = MagicMock()
    connector_registry.get.side_effect = AssertionError("dashboard branch should not call connector")
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
    )
    runner.fire(job_id)
    cm.append.assert_called_once()
    persisted_cid = cm.append.call_args.args[0]
    assert persisted_cid == dest_cid
