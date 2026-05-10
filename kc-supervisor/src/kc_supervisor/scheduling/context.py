"""Per-invocation context for scheduling tools.

The supervisor's WS / inbound handlers set the active conversation_id, channel,
chat_id, and agent into a contextvar before invoking the agent. The scheduling
tools read this contextvar to know "where am I scheduling for".
"""
from __future__ import annotations
from contextvars import ContextVar


_current_context: ContextVar[dict] = ContextVar("scheduling_context")


def set_current_context(ctx: dict) -> None:
    """Called by ws_routes / inbound before agent.send_stream."""
    _current_context.set(ctx)


def get_current_context() -> dict:
    """Called by the scheduling tools at invocation time."""
    try:
        return _current_context.get()
    except LookupError:
        raise RuntimeError(
            "scheduling tool invoked outside a conversation context — "
            "this is a wiring bug; ws_routes/inbound must set_current_context "
            "before invoking the agent"
        )
