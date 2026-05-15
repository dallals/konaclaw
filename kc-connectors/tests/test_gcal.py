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


def test_normalize_rfc3339_passes_through_with_z():
    from kc_connectors.gcal_adapter import _normalize_rfc3339
    assert _normalize_rfc3339("2026-05-15T00:00:00Z") == "2026-05-15T00:00:00Z"


def test_normalize_rfc3339_passes_through_with_offset():
    from kc_connectors.gcal_adapter import _normalize_rfc3339
    assert _normalize_rfc3339("2026-05-15T00:00:00-07:00") == "2026-05-15T00:00:00-07:00"
    assert _normalize_rfc3339("2026-05-15T00:00:00+05:30") == "2026-05-15T00:00:00+05:30"


def test_normalize_rfc3339_promotes_date_only():
    """'2026-05-15' → '2026-05-15T00:00:00<local-tz-offset>' — Google would
    400 otherwise. The exact offset depends on the host; assert structure."""
    import re
    from kc_connectors.gcal_adapter import _normalize_rfc3339
    out = _normalize_rfc3339("2026-05-15")
    assert re.match(r"^2026-05-15T00:00:00[+-]\d{2}:\d{2}$", out), out


def test_normalize_rfc3339_adds_offset_to_naive_datetime():
    import re
    from kc_connectors.gcal_adapter import _normalize_rfc3339
    out = _normalize_rfc3339("2026-05-15T14:30:00")
    assert re.match(r"^2026-05-15T14:30:00[+-]\d{2}:\d{2}$", out), out


def test_normalize_rfc3339_rejects_empty():
    import pytest
    from kc_connectors.gcal_adapter import _normalize_rfc3339
    with pytest.raises(ValueError):
        _normalize_rfc3339("")
    with pytest.raises(ValueError):
        _normalize_rfc3339("   ")


def test_list_events_normalizes_time_args_before_calling_google():
    """Regression for 2026-05-14: the model passed '2026-05-15' as time_min;
    Google 400'd because the value wasn't RFC3339 with a tz. The adapter
    must coerce the value before handing it to the Google client."""
    from kc_connectors.gcal_adapter import build_gcal_tools

    captured: dict = {}

    class _Events:
        def list(self, **kw):
            captured.update(kw)
            class _R:
                def execute(self):
                    return {"items": []}
            return _R()

    class _CL:
        def list(self):
            class _R:
                def execute(self):
                    return {"items": [{"id": "primary", "summary": "primary"}]}
            return _R()

    class _Service:
        def events(self): return _Events()
        def calendarList(self): return _CL()

    tools = build_gcal_tools(_Service())
    tools["gcal.list_events"].impl(time_min="2026-05-15", time_max="2026-05-22")

    import re
    assert re.match(r"^2026-05-15T00:00:00[+-]\d{2}:\d{2}$", captured["timeMin"]), captured
    assert re.match(r"^2026-05-22T00:00:00[+-]\d{2}:\d{2}$", captured["timeMax"]), captured
