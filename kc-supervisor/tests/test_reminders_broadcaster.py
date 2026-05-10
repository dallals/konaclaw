from __future__ import annotations
from kc_supervisor.reminders_broadcaster import RemindersBroadcaster


def test_subscribe_receives_published_events():
    b = RemindersBroadcaster()
    received: list[tuple[str, dict]] = []
    sub = b.subscribe(lambda et, row: received.append((et, row)))

    row = {"id": 1, "kind": "reminder", "status": "pending"}
    b.publish("reminder.created", row)
    b.publish("reminder.cancelled", row)

    assert received == [("reminder.created", row), ("reminder.cancelled", row)]
    sub.unsubscribe()


def test_unsubscribe_stops_delivery():
    b = RemindersBroadcaster()
    received: list = []
    sub = b.subscribe(lambda et, row: received.append((et, row)))
    sub.unsubscribe()
    b.publish("reminder.created", {"id": 1})
    assert received == []


def test_misbehaving_subscriber_does_not_break_others(caplog):
    b = RemindersBroadcaster()
    received: list = []
    b.subscribe(lambda et, row: (_ for _ in ()).throw(RuntimeError("boom")))
    b.subscribe(lambda et, row: received.append(et))
    b.publish("reminder.fired", {"id": 1})
    assert received == ["reminder.fired"]


def test_publish_is_safe_with_no_subscribers():
    b = RemindersBroadcaster()
    b.publish("reminder.created", {"id": 1})  # must not raise
