from __future__ import annotations

from typing import Any, Optional

from kc_core.tools import Tool


GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def build_gcal_tools(service: Any, calendar_id: str = "primary") -> dict[str, Tool]:

    def list_events(time_min: str, time_max: str, calendar_id: Optional[str] = None) -> str:
        if calendar_id:
            cals = [{"id": calendar_id, "summary": calendar_id}]
        else:
            cl = service.calendarList().list().execute()
            cals = cl.get("items", []) or [{"id": "primary", "summary": "primary"}]
        lines: list[str] = []
        for cal in cals:
            cid = cal["id"]
            try:
                r = service.events().list(
                    calendarId=cid,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
            except Exception as exc:
                lines.append(f"[{cal.get('summary', '(unnamed)')}] error: {exc}")
                continue
            for e in r.get("items", []):
                start = e.get("start", {})
                when = start.get("dateTime") or start.get("date") or "(no start)"
                lines.append(
                    f"[{cal.get('summary', '(unnamed)')}] {when} — "
                    f"{e.get('summary', '(no title)')} (id={e['id']})"
                )
        return "\n".join(lines) or "(no events)"

    def create_event(summary: str, start: str, end: str, description: str = "") -> str:
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        r = service.events().insert(calendarId=calendar_id, body=body).execute()
        return f"created event {r['id']}"

    def update_event(
        event_id: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> str:
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = {"dateTime": start}
        if end is not None:
            body["end"] = {"dateTime": end}
        r = service.events().update(calendarId=calendar_id, eventId=event_id, body=body).execute()
        return f"updated event {r['id']}"

    def delete_event(event_id: str) -> str:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return f"deleted event {event_id}"

    def make(n, d, p, i):
        return Tool(name=n, description=d, parameters=p, impl=i)

    return {
        "gcal.list_events": make(
            "gcal.list_events",
            "List calendar events between two RFC3339 times. By default scans "
            "ALL of the user's calendars (primary plus shared/secondary). Pass "
            "calendar_id to restrict to a single calendar; use 'primary' for "
            "just the primary calendar. Recurring events are expanded into "
            "individual instances and results are ordered by start time.",
            {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "RFC3339 lower bound (inclusive)"},
                    "time_max": {"type": "string", "description": "RFC3339 upper bound (exclusive)"},
                    "calendar_id": {"type": "string", "description": "Optional calendar id; omit to scan all calendars."},
                },
                "required": ["time_min", "time_max"],
            },
            list_events,
        ),
        "gcal.create_event": make(
            "gcal.create_event",
            "Create a calendar event. Destructive.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["summary", "start", "end"],
            },
            create_event,
        ),
        "gcal.update_event": make(
            "gcal.update_event",
            "Update a calendar event.",
            {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["event_id"],
            },
            update_event,
        ),
        "gcal.delete_event": make(
            "gcal.delete_event",
            "Delete a calendar event. Destructive.",
            {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
            delete_event,
        ),
    }


def build_gcal_service(credentials):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=credentials)
