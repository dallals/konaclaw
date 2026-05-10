from __future__ import annotations
from datetime import datetime, timedelta, timezone as _tz_mod
from typing import Optional
import dateparser


def parse_when(when: str, tz_name: str) -> datetime:
    """Parse a natural-language time string into a tz-aware datetime."""
    if not when or not when.strip():
        raise ValueError("could not parse 'when': empty string")
    settings = {
        "TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
    }
    parsed = dateparser.parse(when, settings=settings)
    if parsed is None:
        raise ValueError(f"could not parse 'when': {when!r}")
    if parsed.tzinfo is None:
        # dateparser sometimes returns naive even with RETURN_AS_TIMEZONE_AWARE.
        from zoneinfo import ZoneInfo
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed


def is_past(dt: datetime, *, grace_seconds: float = 5.0) -> bool:
    """Return True iff dt is more than grace_seconds in the past relative to now."""
    now = datetime.now(_tz_mod.utc)
    return (dt.astimezone(_tz_mod.utc) + timedelta(seconds=grace_seconds)) < now


def humanize(dt: datetime) -> str:
    """Format a tz-aware datetime as 'Sat May 9 5:00 PM PT'."""
    return dt.strftime("%a %b ") + dt.strftime("%-d %-I:%M %p %Z")
