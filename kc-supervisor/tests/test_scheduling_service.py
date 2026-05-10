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


def test_list_reminders_active_only(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r1 = svc.schedule_one_shot(
            when="in 1 hour", content="a",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        r2 = svc.schedule_cron(
            cron="0 9 * * *", content="b",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        s.update_scheduled_job_status(r1["id"], "done")

        active = svc.list_reminders(conversation_id=cid, active_only=True)
        assert len(active["reminders"]) == 1
        assert active["reminders"][0]["id"] == r2["id"]

        all_ = svc.list_reminders(conversation_id=cid, active_only=False)
        assert len(all_["reminders"]) == 2
    finally:
        svc.shutdown()


def test_list_reminders_shape(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        svc.schedule_one_shot(
            when="in 1 hour", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.list_reminders(conversation_id=cid, active_only=True)
        r = out["reminders"][0]
        assert {"id", "kind", "fires_at_human", "next_fire_human",
                "content", "status", "human_summary"}.issubset(r.keys())
        assert r["kind"] == "reminder"
        assert r["next_fire_human"] is None
        assert r["fires_at_human"] is not None
    finally:
        svc.shutdown()


def test_cancel_reminder_by_id(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        result = svc.cancel_reminder(str(r["id"]), conversation_id=cid)
        assert result["ambiguous"] is False
        assert result["cancelled"][0]["id"] == r["id"]
        assert s.list_scheduled_jobs(conversation_id=cid, statuses=("pending",)) == []
    finally:
        svc.shutdown()


def test_cancel_reminder_by_id_missing_raises(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        with pytest.raises(ValueError, match="no reminder with id"):
            svc.cancel_reminder("9999", conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_unique(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="dinner with mom",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.cancel_reminder("DINNER", conversation_id=cid)
        assert out["ambiguous"] is False
        assert out["cancelled"][0]["id"] == r["id"]
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_ambiguous(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r1 = svc.schedule_one_shot(
            when="in 1 hour", content="dinner with mom",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        r2 = svc.schedule_one_shot(
            when="in 2 hours", content="dinner reservation",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        out = svc.cancel_reminder("dinner", conversation_id=cid)
        assert out["ambiguous"] is True
        assert {c["id"] for c in out["candidates"]} == {r1["id"], r2["id"]}
        assert out["cancelled"] == []
        assert len(s.list_scheduled_jobs(conversation_id=cid, statuses=("pending",))) == 2
    finally:
        svc.shutdown()


def test_cancel_reminder_by_description_no_match(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        svc.schedule_one_shot(
            when="in 1 hour", content="dinner",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        with pytest.raises(ValueError, match="no reminder matched"):
            svc.cancel_reminder("breakfast", conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_only_pending(tmp_path):
    svc, s = _make_service(tmp_path)
    cid = _seed_conv(s)
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        s.update_scheduled_job_status(r["id"], "done")
        with pytest.raises(ValueError):
            svc.cancel_reminder(str(r["id"]), conversation_id=cid)
    finally:
        svc.shutdown()


def test_cancel_reminder_scoped_to_conversation(tmp_path):
    svc, s = _make_service(tmp_path)
    cid_a = _seed_conv(s)
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    try:
        r = svc.schedule_one_shot(
            when="in 1 hour", content="x",
            conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
        )
        with pytest.raises(ValueError):
            svc.cancel_reminder(str(r["id"]), conversation_id=cid_b)
    finally:
        svc.shutdown()


def test_schedule_one_shot_target_channel_uses_routing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    runner = MagicMock()
    svc = ScheduleService(s, runner, tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="dinner",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "telegram"
    assert row["chat_id"] == "8627206839"
    assert row["mode"] == "literal"


def test_schedule_one_shot_target_channel_current_keeps_ctx(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="x",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="current",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "dashboard"
    assert row["chat_id"] == "ws-1"


def test_schedule_one_shot_target_channel_unknown_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="not configured"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="telegram",
        )


def test_schedule_one_shot_target_channel_disabled_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "X", enabled=0)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="disabled"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="telegram",
        )


def test_schedule_one_shot_invalid_target_channel_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown channel"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            target_channel="bogus",
        )


def test_schedule_one_shot_invalid_mode_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown mode"):
        svc.schedule_one_shot(
            when="in 5 minutes", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            mode="bogus",
        )


def test_schedule_one_shot_mode_agent_phrased_persists(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 5 minutes", content="dinner trigger",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        mode="agent_phrased",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["mode"] == "agent_phrased"
