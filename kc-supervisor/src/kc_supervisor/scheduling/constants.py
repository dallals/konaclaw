"""Allowed values for reminder query/path parameters. Single source of truth
shared across REST routes (http_routes.py), the WS endpoint (ws_routes.py),
and ScheduleService validation."""

ALLOWED_REMINDER_STATUSES: frozenset[str] = frozenset(
    {"pending", "done", "cancelled", "failed", "missed"}
)
ALLOWED_REMINDER_KINDS: frozenset[str] = frozenset({"reminder", "cron"})
ALLOWED_REMINDER_CHANNELS: frozenset[str] = frozenset(
    {"dashboard", "telegram", "imessage"}
)
