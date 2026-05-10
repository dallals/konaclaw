from __future__ import annotations
import time
from unittest.mock import MagicMock
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService


def _make_service(tmp_path):
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
    )
    return svc, s


def _seed_conv(s: Storage) -> int:
    return s.create_conversation(agent="kona", channel="telegram")


def test_reconcile_drops_aps_jobs_with_missing_db_row(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert svc._scheduler.get_job(str(r["id"])) is not None
        # Manually delete the DB row (simulating cascade delete)
        s.delete_scheduled_job(r["id"])
        svc.reconcile()
        assert svc._scheduler.get_job(str(r["id"])) is None
    finally:
        svc.shutdown()


def test_reconcile_recreates_aps_job_for_pending_db_row(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        # Manually remove the APS job
        svc._scheduler.remove_job(str(r["id"]))
        assert svc._scheduler.get_job(str(r["id"])) is None
        svc.reconcile()
        # Should have been re-created
        assert svc._scheduler.get_job(str(r["id"])) is not None
    finally:
        svc.shutdown()


def test_rehydrate_after_restart_preserves_pending_job(tmp_path):
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    r = svc.schedule_one_shot(
        when="in 1 hour", content="x",
        conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.shutdown()

    svc2, s2 = _make_service(tmp_path)
    svc2.start()
    try:
        assert svc2._scheduler.get_job(str(r["id"])) is not None
        assert s2.get_scheduled_job(r["id"])["status"] == "pending"
    finally:
        svc2.shutdown()


def test_one_shot_misfire_grace_is_24h(tmp_path):
    """Schedule a reminder; assert APS job has misfire_grace_time=86400."""
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        job = svc._scheduler.get_job(str(r["id"]))
        assert job is not None
        assert job.misfire_grace_time == 86400
    finally:
        svc.shutdown()


def test_cron_coalesce_is_set(tmp_path):
    """Cron jobs have coalesce=True so missed firings collapse to one."""
    svc, s = _make_service(tmp_path)
    svc.start()
    cid = _seed_conv(s)
    try:
        r = svc.schedule_cron(
            cron="0 9 * * *", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        job = svc._scheduler.get_job(str(r["id"]))
        assert job is not None
        assert job.coalesce is True
    finally:
        svc.shutdown()


def test_aps_and_app_tables_coexist(tmp_path):
    """APScheduler creates its own tables in the same DB; our migration must
    not collide.
    """
    svc, s = _make_service(tmp_path)
    svc.start()
    try:
        with s.connect() as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        # Our app tables
        assert "scheduled_jobs" in tables
        assert "messages" in tables
        # APS tables (default name pattern)
        assert any(t.startswith("apscheduler") for t in tables)
    finally:
        svc.shutdown()
