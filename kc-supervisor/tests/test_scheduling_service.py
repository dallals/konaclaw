from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from freezegun import freeze_time
from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.service import ScheduleService


def _make_service(tmp_path, broadcaster=None) -> tuple[ScheduleService, Storage]:
    s = Storage(tmp_path / "kc.db")
    s.init()
    runner = MagicMock()
    svc = ScheduleService(
        storage=s, runner=runner, db_path=tmp_path / "kc.db",
        timezone="America/Los_Angeles",
        broadcaster=broadcaster,
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
            svc.cancel_reminder(str(r["id"]), conversation_id=cid_b, scope="conversation")
    finally:
        svc.shutdown()


def test_cancel_soft_deletes_marking_status_cancelled(tmp_path):
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(
            when="in 1 hour", content="ping",
            conversation_id=cid, channel="telegram", chat_id="C1", agent="kona",
        )
        job_id = res["id"]
        svc.cancel_reminder(str(job_id), conversation_id=cid, scope="user")

        row = storage.get_scheduled_job(job_id)
        assert row is not None, "row must persist after cancel"
        assert row["status"] == "cancelled"
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


def test_schedule_cron_target_channel_uses_routing(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "8627206839", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_cron(
        cron="0 9 * * *", content="standup",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    row = s.get_scheduled_job(out["id"])
    assert row["channel"] == "telegram"
    assert row["chat_id"] == "8627206839"
    assert row["mode"] == "agent_phrased"


def test_schedule_cron_invalid_mode_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown mode"):
        svc.schedule_cron(
            cron="0 9 * * *", content="x",
            conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
            mode="bogus",
        )


def test_list_reminders_default_scope_user_returns_all(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="A",
        conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.schedule_one_shot(
        when="in 1 hour", content="B",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    out = svc.list_reminders(conversation_id=cid_a)  # default scope="user"
    contents = [r["content"] for r in out["reminders"]]
    assert "A" in contents and "B" in contents


def test_list_reminders_scope_conversation_filters(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="A",
        conversation_id=cid_a, channel="telegram", chat_id="C1", agent="kona",
    )
    svc.schedule_one_shot(
        when="in 1 hour", content="B",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    out = svc.list_reminders(conversation_id=cid_a, scope="conversation")
    contents = [r["content"] for r in out["reminders"]]
    assert contents == ["A"]


def test_list_reminders_view_includes_channel_and_mode(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="X",
        conversation_id=cid, channel="dashboard", chat_id="ws-1", agent="kona",
        target_channel="telegram", mode="agent_phrased",
    )
    out = svc.list_reminders(conversation_id=cid)
    r = out["reminders"][0]
    assert r["channel"] == "telegram"
    assert r["mode"] == "agent_phrased"


def test_list_reminders_invalid_scope_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown scope"):
        svc.list_reminders(conversation_id=cid, scope="bogus")


def test_cancel_reminder_default_scope_user_finds_other_conversation(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    out = svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    # Cancel from conversation A — must find conversation B's reminder by description.
    result = svc.cancel_reminder("dinner", conversation_id=cid_a)
    assert result["ambiguous"] is False
    assert len(result["cancelled"]) == 1


def test_cancel_reminder_scope_conversation_only_sees_own(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid_a = s.create_conversation(agent="kona", channel="telegram")
    cid_b = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    svc.schedule_one_shot(
        when="in 1 hour", content="dinner",
        conversation_id=cid_b, channel="dashboard", chat_id="ws-1", agent="kona",
    )
    with pytest.raises(ValueError, match="no reminder matched"):
        svc.cancel_reminder("dinner", conversation_id=cid_a, scope="conversation")


def test_cancel_reminder_invalid_scope_raises(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.scheduling.service import ScheduleService
    from unittest.mock import MagicMock
    import pytest
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    svc = ScheduleService(s, MagicMock(), tmp_path / "kc.db", "America/Los_Angeles")
    with pytest.raises(ValueError, match="unknown scope"):
        svc.cancel_reminder("x", conversation_id=cid, scope="bogus")


def test_list_all_reminders_returns_full_rows_with_next_fire_at(tmp_path):
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        one = svc.schedule_one_shot(
            when="in 1 hour", content="A", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        cron = svc.schedule_cron(
            cron="0 9 * * *", content="B", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        out = svc.list_all_reminders()
        ids = [r["id"] for r in out["reminders"]]
        assert one["id"] in ids and cron["id"] in ids
        for r in out["reminders"]:
            assert {
                "id", "kind", "payload", "status", "channel", "chat_id",
                "when_utc", "cron_spec", "attempts", "last_fired_at",
                "created_at", "mode", "agent", "conversation_id",
            } <= set(r.keys())
            assert "next_fire_at" in r
            if r["kind"] == "reminder":
                assert r["next_fire_at"] == r["when_utc"]
            else:
                assert isinstance(r["next_fire_at"], (int, float))
    finally:
        svc.shutdown()


def test_list_all_reminders_filters_by_status(tmp_path):
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        a = svc.schedule_one_shot(
            when="in 1 hour", content="x", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        svc.cancel_reminder(str(a["id"]), conversation_id=cid, scope="user")
        pending = svc.list_all_reminders(statuses=["pending"])
        cancelled = svc.list_all_reminders(statuses=["cancelled"])
        assert all(r["status"] == "pending" for r in pending["reminders"])
        assert all(r["status"] == "cancelled" for r in cancelled["reminders"])
        assert a["id"] in [r["id"] for r in cancelled["reminders"]]
    finally:
        svc.shutdown()


def test_list_all_reminders_filters_by_kind(tmp_path):
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        svc.schedule_one_shot(
            when="in 1 hour", content="o", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        svc.schedule_cron(
            cron="0 9 * * *", content="c", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        only_oneshot = svc.list_all_reminders(kinds=["reminder"])
        assert all(r["kind"] == "reminder" for r in only_oneshot["reminders"])
        only_cron = svc.list_all_reminders(kinds=["cron"])
        assert all(r["kind"] == "cron" for r in only_cron["reminders"])
    finally:
        svc.shutdown()


def test_list_all_reminders_filters_by_channel(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        dashboard_id = storage.add_scheduled_job(
            kind="reminder", agent="kona", conversation_id=cid,
            channel="dashboard", chat_id="c1", payload="A",
            when_utc=time.time() + 3600, cron_spec=None, mode="literal",
        )
        telegram_id = storage.add_scheduled_job(
            kind="reminder", agent="kona", conversation_id=cid,
            channel="telegram", chat_id="c2", payload="B",
            when_utc=time.time() + 3600, cron_spec=None, mode="literal",
        )
        dashboard_only = svc.list_all_reminders(channels=["dashboard"])
        ids = [r["id"] for r in dashboard_only["reminders"]]
        assert dashboard_id in ids
        assert telegram_id not in ids

        telegram_only = svc.list_all_reminders(channels=["telegram"])
        ids2 = [r["id"] for r in telegram_only["reminders"]]
        assert telegram_id in ids2
        assert dashboard_id not in ids2
    finally:
        svc.shutdown()


def test_list_all_reminders_sort_order(tmp_path):
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        far = svc.schedule_one_shot(
            when="in 3 hours", content="far", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        near = svc.schedule_one_shot(
            when="in 30 minutes", content="near", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        rows = svc.list_all_reminders(statuses=["pending"])["reminders"]
        ids = [r["id"] for r in rows]
        assert ids.index(near["id"]) < ids.index(far["id"])
    finally:
        svc.shutdown()


def test_snooze_reschedules_pending_oneshot(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(
            when="in 1 hour", content="x", conversation_id=cid,
            channel="dashboard", chat_id="c1", agent="kona",
        )
        new_when = time.time() + 3600 * 3  # 3 hours from now
        out = svc.snooze_reminder(reminder_id=res["id"], when_utc=new_when)
        assert out["id"] == res["id"]
        row = storage.get_scheduled_job(res["id"])
        assert abs(row["when_utc"] - new_when) < 1.0
        # APS job rescheduled to same job id
        aps = svc._scheduler.get_job(str(res["id"]))
        assert aps is not None
        assert abs(aps.next_run_time.timestamp() - new_when) < 1.0
    finally:
        svc.shutdown()


def test_snooze_rejects_non_pending(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        svc.cancel_reminder(str(res["id"]), conversation_id=cid, scope="user")
        with pytest.raises(ValueError, match="already_fired|cancelled|not pending"):
            svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600)
    finally:
        svc.shutdown()


def test_snooze_rejects_cron(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_cron(cron="0 9 * * *", content="x", conversation_id=cid,
                                channel="dashboard", chat_id="c1", agent="kona")
        with pytest.raises(ValueError, match="cron_not_snoozable"):
            svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600)
    finally:
        svc.shutdown()


def test_snooze_rejects_past_time(tmp_path):
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        with pytest.raises(ValueError, match="past"):
            svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() - 60)
    finally:
        svc.shutdown()


def test_snooze_unknown_id_raises(tmp_path):
    import time
    svc, _ = _make_service(tmp_path)
    try:
        with pytest.raises(LookupError):
            svc.snooze_reminder(reminder_id=999999, when_utc=time.time() + 3600)
    finally:
        svc.shutdown()


def test_snooze_aps_failure_restores_prior_when_utc(tmp_path, monkeypatch):
    """If APS reschedule_job raises a non-JobLookupError, the DB row's when_utc
    must be restored so DB and APS stay consistent."""
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        prior_when = storage.get_scheduled_job(res["id"])["when_utc"]

        def boom(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(svc._scheduler, "reschedule_job", boom)

        with pytest.raises(RuntimeError, match="boom"):
            svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600 * 5)

        # DB should be back to the original when_utc
        assert storage.get_scheduled_job(res["id"])["when_utc"] == prior_when
    finally:
        svc.shutdown()


def test_snooze_orphaned_aps_job_raises_lookup_error(tmp_path):
    """If the APS job is missing but the DB row exists, snooze raises LookupError
    (not the leaky apscheduler.jobstores.base.JobLookupError)."""
    import time
    svc, storage = _make_service(tmp_path)
    cid = _seed_conv(storage)
    try:
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        # Remove the APS job out from under the DB row
        svc._scheduler.remove_job(str(res["id"]))
        prior_when = storage.get_scheduled_job(res["id"])["when_utc"]

        with pytest.raises(LookupError):
            svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600 * 5)

        # DB row's when_utc rolled back to prior value
        assert storage.get_scheduled_job(res["id"])["when_utc"] == prior_when
    finally:
        svc.shutdown()


def _make_service_with_broadcaster(tmp_path):
    """Same as _make_service but also returns the broadcaster so tests can subscribe."""
    from kc_supervisor.reminders_broadcaster import RemindersBroadcaster
    b = RemindersBroadcaster()
    svc, storage = _make_service(tmp_path, broadcaster=b)
    return svc, storage, b


def test_publishes_reminder_created(tmp_path):
    svc, storage, b = _make_service_with_broadcaster(tmp_path)
    cid = _seed_conv(storage)
    try:
        events = []
        b.subscribe(lambda et, row: events.append((et, row["id"])))
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        assert ("reminder.created", res["id"]) in events
    finally:
        svc.shutdown()


def test_publishes_reminder_cancelled(tmp_path):
    svc, storage, b = _make_service_with_broadcaster(tmp_path)
    cid = _seed_conv(storage)
    try:
        events = []
        b.subscribe(lambda et, row: events.append((et, row["id"], row.get("status"))))
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        svc.cancel_reminder(str(res["id"]), conversation_id=cid, scope="user")
        cancelled_events = [e for e in events if e[0] == "reminder.cancelled"]
        assert cancelled_events
        assert cancelled_events[-1][2] == "cancelled"
    finally:
        svc.shutdown()


def test_publishes_reminder_snoozed(tmp_path):
    import time
    svc, storage, b = _make_service_with_broadcaster(tmp_path)
    cid = _seed_conv(storage)
    try:
        events = []
        b.subscribe(lambda et, row: events.append((et, row["id"])))
        res = svc.schedule_one_shot(when="in 1 hour", content="x", conversation_id=cid,
                                    channel="dashboard", chat_id="c1", agent="kona")
        svc.snooze_reminder(reminder_id=res["id"], when_utc=time.time() + 3600 * 3)
        assert ("reminder.snoozed", res["id"]) in events
    finally:
        svc.shutdown()
