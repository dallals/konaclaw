from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService


def _make_service(tmp_path) -> tuple[ScheduleService, Storage]:
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


@freeze_time("2026-05-09 14:30:00")  # 2:30pm UTC = 7:30am PT
def test_schedule_one_shot_resolves_pt_5pm_today(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_one_shot(
            when="5pm today", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert "id" in result
        assert "fires_at" in result
        # 5pm PT during PDT == 17:00-07:00 in ISO
        assert "T17:00:00" in result["fires_at"]
        assert "5:00 PM" in result["fires_at_human"]
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert len(rows) == 1
        assert rows[0]["payload"] == "dinner"
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_one_shot_relative_in_two_hours(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_one_shot(
            when="in 2 hours", content="t",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        # Either UTC representation (16:30Z) or PT (09:30-07:00) is acceptable.
        assert "T16:30" in result["fires_at"] or "T09:30" in result["fires_at"]
    finally:
        svc.shutdown()


@freeze_time("2026-05-09 14:30:00")
def test_schedule_one_shot_past_time_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="past"):
            svc.schedule_one_shot(
                when="yesterday", content="t",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_unparseable_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="parse"):
            svc.schedule_one_shot(
                when="!@#$%", content="t",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_empty_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_one_shot(
                when="in 1 hour", content="",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_one_shot_oversized_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_one_shot(
                when="in 1 hour", content="x" * 4001,
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_cron_valid_spec(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        result = svc.schedule_cron(
            cron="0 9 * * 1-5", content="standup",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        assert "id" in result
        assert "next_fire" in result
        assert "next_fire_human" in result
        assert "weekday" in result["human_summary"].lower() or "Monday" in result["human_summary"]
        rows = s.list_scheduled_jobs(conversation_id=cid)
        assert rows[0]["kind"] == "cron"
        assert rows[0]["cron_spec"] == "0 9 * * 1-5"
    finally:
        svc.shutdown()


def test_schedule_cron_invalid_spec_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="invalid cron"):
            svc.schedule_cron(
                cron="not a real cron", content="x",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()


def test_schedule_cron_empty_content_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="content"):
            svc.schedule_cron(
                cron="0 9 * * *", content="",
                conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
            )
    finally:
        svc.shutdown()
