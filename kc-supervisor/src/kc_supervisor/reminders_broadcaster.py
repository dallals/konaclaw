from __future__ import annotations
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventType = str  # one of "reminder.created" | "cancelled" | "snoozed" | "fired" | "failed"
ReminderRow = dict[str, Any]
SubscriberCallback = Callable[[EventType, ReminderRow], None]


class _Subscription:
    def __init__(self, broker: "RemindersBroadcaster", callback: SubscriberCallback) -> None:
        self._broker = broker
        self.callback = callback

    def unsubscribe(self) -> None:
        self._broker._subs.discard(self)


class RemindersBroadcaster:
    """Synchronous pub/sub for reminder lifecycle events.

    Mirrors ApprovalBroker.subscribe semantics. Subscribers register a callback
    that takes (event_type, reminder_row_dict). publish() fans out to all
    current subscribers; misbehaving callbacks are logged and swallowed so a
    single bad subscriber can't block the publisher.

    Producers (ScheduleService, ReminderRunner) MUST call publish() *after*
    their DB transaction commits. Pre-commit publishing risks broadcasting a
    state that rolls back.
    """

    def __init__(self) -> None:
        self._subs: set[_Subscription] = set()

    def subscribe(self, callback: SubscriberCallback) -> _Subscription:
        sub = _Subscription(broker=self, callback=callback)
        self._subs.add(sub)
        return sub

    def publish(self, event_type: EventType, reminder_row: ReminderRow) -> None:
        for sub in list(self._subs):
            try:
                sub.callback(event_type, reminder_row)
            except Exception:
                logger.exception("reminders subscriber raised; ignoring")
