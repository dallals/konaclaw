from __future__ import annotations

from unittest.mock import MagicMock

from kc_connectors.gcal_adapter import build_gcal_tools


def fake_service(calendars=None, events_per_calendar=None):
    """Build a MagicMock gcal service.

    `calendars` is the calendarList.list result. `events_per_calendar` maps
    calendar id -> events list. The default is a single 'primary' calendar
    with one demo event so existing call sites keep working.
    """
    if calendars is None:
        calendars = [{"id": "primary", "summary": "primary"}]
    if events_per_calendar is None:
        events_per_calendar = {"primary": [{"id": "e1", "summary": "demo", "start": {"dateTime": "2026-06-01T10:00:00Z"}}]}

    svc = MagicMock()
    svc.calendarList().list().execute.return_value = {"items": calendars}

    def events_list(*, calendarId, **_kwargs):
        return MagicMock(execute=MagicMock(return_value={"items": events_per_calendar.get(calendarId, [])}))

    svc.events().list.side_effect = events_list
    svc.events().insert().execute.return_value = {"id": "e2"}
    svc.events().update().execute.return_value = {"id": "e2"}
    svc.events().delete().execute.return_value = ""
    return svc


def test_list_events_default_scans_all_calendars():
    svc = fake_service(
        calendars=[
            {"id": "primary", "summary": "primary"},
            {"id": "hs@group.calendar.google.com", "summary": "Heather and Sammy"},
        ],
        events_per_calendar={
            "primary": [],
            "hs@group.calendar.google.com": [
                {"id": "lunch", "summary": "Mother's Day lunch", "start": {"date": "2026-05-10"}},
            ],
        },
    )
    tools = build_gcal_tools(service=svc)
    out = tools["gcal.list_events"].impl(time_min="2026-05-10T00:00:00Z", time_max="2026-05-11T00:00:00Z")
    assert "Mother's Day lunch" in out
    assert "Heather and Sammy" in out
    assert "2026-05-10" in out


def test_list_events_calendar_id_restricts():
    svc = fake_service(
        calendars=[
            {"id": "primary", "summary": "primary"},
            {"id": "hs@group.calendar.google.com", "summary": "Heather and Sammy"},
        ],
        events_per_calendar={
            "primary": [{"id": "p1", "summary": "primary thing", "start": {"dateTime": "2026-06-01T10:00:00Z"}}],
            "hs@group.calendar.google.com": [
                {"id": "lunch", "summary": "Mother's Day lunch", "start": {"date": "2026-05-10"}},
            ],
        },
    )
    tools = build_gcal_tools(service=svc)
    out = tools["gcal.list_events"].impl(
        time_min="2026-05-01T00:00:00Z",
        time_max="2026-06-30T00:00:00Z",
        calendar_id="primary",
    )
    assert "primary thing" in out
    assert "Mother's Day lunch" not in out


def test_list_events_no_events():
    svc = fake_service(events_per_calendar={"primary": []})
    tools = build_gcal_tools(service=svc)
    assert tools["gcal.list_events"].impl(time_min="2026-01-01T00:00:00Z", time_max="2026-12-31T00:00:00Z") == "(no events)"


def test_list_events_passes_demo_event():
    # Backward-compat: existing default fixture surfaces the demo event.
    svc = fake_service()
    tools = build_gcal_tools(service=svc)
    assert "demo" in tools["gcal.list_events"].impl(time_min="2026-01-01T00:00:00Z", time_max="2026-12-31T00:00:00Z")


def test_create_update_delete():
    svc = fake_service()
    tools = build_gcal_tools(service=svc)
    assert "e2" in tools["gcal.create_event"].impl(summary="x", start="2026-01-01T10:00:00Z", end="2026-01-01T11:00:00Z")
    assert "e2" in tools["gcal.update_event"].impl(event_id="e2", summary="y")
    assert "deleted" in tools["gcal.delete_event"].impl(event_id="e2")
