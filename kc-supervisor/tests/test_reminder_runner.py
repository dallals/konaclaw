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
