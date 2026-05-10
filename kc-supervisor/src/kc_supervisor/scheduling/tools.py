from __future__ import annotations
from typing import Callable
from kc_core.tools import Tool
from kc_supervisor.scheduling.service import ScheduleService


def build_scheduling_tools(
    service: ScheduleService,
    current_context: Callable[[], dict],
) -> list[Tool]:
    """Build the four scheduling tools.

    `current_context` returns the current invocation context as a dict with
    keys: conversation_id (int), channel (str), chat_id (str), agent (str).
    The supervisor binds this per-conversation when the agent is invoked.
    """

    def _schedule_reminder(
        when: str, content: str,
        target_channel: str = "current", mode: str = "literal",
    ) -> dict:
        ctx = current_context()
        return service.schedule_one_shot(
            when=when, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
            target_channel=target_channel, mode=mode,
        )

    def _schedule_cron(
        cron: str, content: str,
        target_channel: str = "current", mode: str = "literal",
    ) -> dict:
        ctx = current_context()
        return service.schedule_cron(
            cron=cron, content=content,
            conversation_id=ctx["conversation_id"],
            channel=ctx["channel"], chat_id=ctx["chat_id"], agent=ctx["agent"],
            target_channel=target_channel, mode=mode,
        )

    def _list_reminders(active_only: bool = True, scope: str = "user") -> dict:
        ctx = current_context()
        return service.list_reminders(
            conversation_id=ctx["conversation_id"], active_only=active_only, scope=scope,
        )

    def _cancel_reminder(id_or_description: str, scope: str = "user") -> dict:
        ctx = current_context()
        return service.cancel_reminder(
            id_or_description, conversation_id=ctx["conversation_id"], scope=scope,
        )

    return [
        Tool(
            name="schedule_reminder",
            description=(
                "Schedule a one-shot reminder. The user will receive the "
                "reminder text in this same conversation at the specified "
                "time. The `when` argument is natural-language (e.g. '5pm "
                "today', 'in 2 hours', 'tomorrow at 9am') resolved in the "
                "user's local timezone. Returns {id, fires_at, fires_at_human, "
                "kind}. Raises ValueError on unparseable or past times."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "natural-language time"},
                    "content": {"type": "string", "description": "reminder text (1-4000 chars). When mode='agent_phrased', interpreted as an internal trigger description for you, not the literal text the user sees."},
                    "target_channel": {
                        "type": "string",
                        "enum": ["current", "telegram", "dashboard", "imessage"],
                        "description": "Use only when the user explicitly asks to be reminded somewhere other than this conversation. Channels not in the configured allowlist will raise. Default 'current'.",
                        "default": "current",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["literal", "agent_phrased"],
                        "description": "If 'agent_phrased', you will be re-invoked at fire time to compose the actual message. The 'content' arg is then a trigger description for you, not user-facing text. Default 'literal'.",
                        "default": "literal",
                    },
                },
                "required": ["when", "content"],
            },
            impl=_schedule_reminder,
        ),
        Tool(
            name="schedule_cron",
            description=(
                "Schedule a recurring reminder via standard 5-field cron "
                "syntax (minute hour day-of-month month day-of-week). "
                "Examples: '0 9 * * 1-5' = weekdays 9am, '0 */2 * * *' = "
                "every 2 hours. Sub-minute schedules are not supported. "
                "Returns {id, next_fire, next_fire_human, human_summary, kind}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cron": {"type": "string", "description": "5-field cron expression"},
                    "content": {"type": "string", "description": "reminder text (1-4000 chars). When mode='agent_phrased', interpreted as an internal trigger description for you, not the literal text the user sees."},
                    "target_channel": {
                        "type": "string",
                        "enum": ["current", "telegram", "dashboard", "imessage"],
                        "description": "Use only when the user explicitly asks to be reminded somewhere other than this conversation. Channels not in the configured allowlist will raise. Default 'current'.",
                        "default": "current",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["literal", "agent_phrased"],
                        "description": "If 'agent_phrased', you will be re-invoked at fire time to compose the actual message. The 'content' arg is then a trigger description for you, not user-facing text. Default 'literal'.",
                        "default": "literal",
                    },
                },
                "required": ["cron", "content"],
            },
            impl=_schedule_cron,
        ),
        Tool(
            name="list_reminders",
            description=(
                "List reminders scheduled in the current conversation. "
                "If active_only is True (default), returns only pending "
                "reminders; otherwise also includes done, cancelled, failed, "
                "and missed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": "boolean",
                        "description": "if True, only pending reminders",
                        "default": True,
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "conversation"],
                        "description": "'user' (default) lists all your reminders across channels. 'conversation' restricts to reminders scheduled in this conversation.",
                        "default": "user",
                    },
                },
                "required": [],
            },
            impl=_list_reminders,
        ),
        Tool(
            name="cancel_reminder",
            description=(
                "Cancel a pending reminder by ID or by description fragment. "
                "If id_or_description is purely numeric, treated as an ID. "
                "Otherwise, matched as case-insensitive substring against "
                "the reminder content. If multiple match, returns "
                "{ambiguous: true, candidates: [...]} and cancels nothing — "
                "ask the user to disambiguate."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id_or_description": {
                        "type": "string",
                        "description": "integer ID or description fragment",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "conversation"],
                        "description": "'user' (default) searches all your reminders across channels. 'conversation' restricts to reminders scheduled in this conversation.",
                        "default": "user",
                    },
                },
                "required": ["id_or_description"],
            },
            impl=_cancel_reminder,
        ),
    ]
