from __future__ import annotations
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.runner import ReminderRunner


def _make_runner(
    tmp_path, *, agent_registry=None, broadcaster=None, with_connector=True,
) -> tuple[ReminderRunner, Storage, MagicMock, MagicMock]:
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
    # By default, append returns an int message id (mirrors real ConversationManager).
    # Tests that override append (e.g., to raise) replace this entirely.
    cm.append.return_value = 1
    connector_registry = MagicMock()
    if with_connector:
        connector = MagicMock()
        connector.send = AsyncMock()
        connector_registry.get.return_value = connector
    else:
        connector_registry.get.side_effect = KeyError("no connector for channel")
    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=agent_registry,
        broadcaster=broadcaster,
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
    chat_id = f"dashboard:{cid}"
    s.put_conv_for_chat("dashboard", chat_id, "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id=chat_id,
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
    # Assert persisted to correct conversation
    persisted_cid = cm.append.call_args.args[0]
    assert persisted_cid == cid
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


def test_runner_accepts_agent_registry(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = ReminderRunner(
        storage=s, conversations=MagicMock(), connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=MagicMock(),
    )
    assert runner.agent_registry is not None


def test_compose_agent_phrased_returns_assistant_text(tmp_path):
    """The helper invokes the agent's send_stream and returns the Complete frame's text."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="dinner trigger",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    fake_core = MagicMock()
    fake_core.tools = MagicMock()
    fake_core.system_prompt = "you are kona"

    async def fake_send_stream(_content):
        class FakeComplete:
            reply = AssistantMessage(content="hey, dinner time!")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock()
    fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "you are kona"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []  # empty history

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    text = runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)
    assert text == "hey, dinner time!"


def test_compose_agent_phrased_strips_scheduling_tools(tmp_path):
    """During the fire-time turn, the agent's tools must NOT include scheduling tools.
    After the turn, tools are restored."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def __init__(self, names):
            self._names = list(names)
        def names(self):
            return list(self._names)

    original = FakeToolRegistry(["schedule_reminder", "schedule_cron", "cancel_reminder",
                                  "list_reminders", "search_files"])
    captured_tool_names_during_turn = []

    fake_core = MagicMock()
    fake_core.tools = original
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        captured_tool_names_during_turn.extend(fake_core.tools.names())
        class FakeComplete:
            reply = AssistantMessage(content="ok")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)

    assert "schedule_reminder" not in captured_tool_names_during_turn
    assert "schedule_cron" not in captured_tool_names_during_turn
    assert "cancel_reminder" not in captured_tool_names_during_turn
    assert "search_files" in captured_tool_names_during_turn  # unrelated tools preserved
    # After the turn, tools restored.
    assert "schedule_reminder" in fake_core.tools.names()


def test_compose_agent_phrased_returns_none_when_agent_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.put_conv_for_chat("dashboard", "ws-1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="dashboard", chat_id="ws-1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def __init__(self, names): self._names = list(names)
        def names(self): return list(self._names)

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry(["schedule_reminder"])
    fake_core.system_prompt = "x"

    async def boom(_content):
        raise RuntimeError("model exploded")
        yield

    fake_core.send_stream = boom
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock(); cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=MagicMock(),
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    text = runner._compose_agent_phrased(s.get_scheduled_job(job_id), dest_conv_id=cid)
    assert text is None


def test_fire_literal_mode_uses_prefix_unchanged(tmp_path):
    """Existing Phase 1 behavior: literal rows fire with the ⏰ prefix."""
    runner, s, cm, registry = _make_runner(tmp_path)
    job_id = _seed(s, cm)  # mode defaults to literal
    runner.fire(job_id)
    connector = registry.get.return_value
    content = connector.send.call_args.args[1]
    assert content == "⏰ dinner"


def test_fire_agent_phrased_dispatches_composed_text(tmp_path):
    """agent_phrased rows: runner ships the agent's composed text (no prefix)."""
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from kc_core.messages import AssistantMessage
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner trigger",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def __init__(self, names): self._names = list(names)
        def names(self): return list(self._names)
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry([])
    fake_core.system_prompt = "x"

    async def fake_send_stream(_content):
        class FakeComplete:
            reply = AssistantMessage(content="hey, ready for dinner?")
        yield FakeComplete()

    fake_core.send_stream = fake_send_stream
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock()
    cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner.fire(job_id)
    content = connector.send.call_args.args[1]
    assert content == "hey, ready for dinner?"
    # Persisted text matches.
    assert cm.append.call_args.args[1].content == "hey, ready for dinner?"


def test_fire_agent_phrased_failure_marks_row_failed_no_dispatch(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.runner import ReminderRunner
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="x",
        when_utc=1.0, cron_spec=None, mode="agent_phrased",
    )

    class FakeToolRegistry:
        def names(self): return []
        def to_openai_schema(self): return []
        def invoke(self, name, args): raise NotImplementedError

    fake_core = MagicMock()
    fake_core.tools = FakeToolRegistry()
    fake_core.system_prompt = "x"

    async def boom(_content):
        raise RuntimeError("nope")
        yield

    fake_core.send_stream = boom
    fake_assembled = MagicMock(); fake_assembled.core_agent = fake_core
    fake_assembled.base_system_prompt = "x"
    fake_runtime = MagicMock(); fake_runtime.assembled = fake_assembled
    fake_registry = MagicMock(); fake_registry.get.return_value = fake_runtime

    cm = MagicMock(); cm.get_or_create.return_value = cid
    cm.list_messages.return_value = []
    connector_registry = MagicMock()
    connector = MagicMock(); connector.send = AsyncMock()
    connector_registry.get.return_value = connector

    runner = ReminderRunner(
        storage=s, conversations=cm, connector_registry=connector_registry,
        coroutine_runner=lambda c: asyncio.run(c),
        agent_registry=fake_registry,
    )
    runner.fire(job_id)
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "failed"
    connector.send.assert_not_called()
    cm.append.assert_not_called()


def test_fire_stamps_scheduled_job_id_on_assistant_message(tmp_path):
    """fire() should stamp messages.scheduled_job_id with the job id of the
    triggering scheduled_jobs row, so the chat-bubble badge can link back."""
    runner, s, cm, _ = _make_runner(tmp_path)
    # Use real ConversationManager so the message row actually persists.
    from kc_supervisor.conversations import ConversationManager
    real_cm = ConversationManager(s)
    runner.conversations = real_cm
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "C1", "kona", cid)
    job_id = s.add_scheduled_job(
        kind="reminder", agent="kona", conversation_id=cid,
        channel="telegram", chat_id="C1", payload="dinner",
        when_utc=time.time() + 60, cron_spec=None,
    )
    runner.fire(job_id)
    with s.connect() as c:
        row = c.execute(
            "SELECT id, scheduled_job_id FROM messages "
            "WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
    assert row is not None, "no message persisted"
    assert row["scheduled_job_id"] == job_id


def test_fire_publishes_reminder_fired(tmp_path):
    """A successful fire() should publish 'reminder.fired' through the broadcaster."""
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    events: list[tuple[str, int]] = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    runner, s, cm, _ = _make_runner(tmp_path, broadcaster=b)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    assert ("reminder.fired", job_id) in events


def test_fire_failed_publishes_failed_event(tmp_path):
    """When the connector fails (no connector for channel), fire() should publish
    'reminder.failed' through the broadcaster."""
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    events: list[tuple[str, int]] = []
    b.subscribe(lambda et, row: events.append((et, row["id"])))
    runner, s, cm, _ = _make_runner(tmp_path, broadcaster=b, with_connector=False)
    job_id = _seed(s, cm)
    runner.fire(job_id)
    assert ("reminder.failed", job_id) in events


def test_fire_skips_non_pending_row(tmp_path):
    """If the DB row's status is not 'pending' (e.g., cancelled), fire() must
    early-return without sending or persisting anything. Prevents firing
    cancelled rows after Task 3's soft-cancel change."""
    runner, s, cm, registry = _make_runner(tmp_path)
    job_id = _seed(s, cm)
    s.update_scheduled_job_status(job_id, "cancelled")
    runner.fire(job_id)
    # Connector NOT invoked.
    connector = registry.get.return_value
    connector.send.assert_not_called()
    # No message persisted.
    cm.append.assert_not_called()
    # Status remains 'cancelled' (no update_scheduled_job_after_fire side effect).
    row = s.get_scheduled_job(job_id)
    assert row["status"] == "cancelled"
    assert row["attempts"] == 0
