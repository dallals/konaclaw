from kc_supervisor.service import SubagentBroadcaster


def test_broadcaster_fans_out_to_all_subscribers():
    b = SubagentBroadcaster()
    a_received: list[dict] = []
    b_received: list[dict] = []
    b.subscribe(a_received.append)
    b.subscribe(b_received.append)
    frame = {"type": "subagent_started", "subagent_id": "ep_a"}
    b.publish(frame)
    assert a_received == [frame]
    assert b_received == [frame]


def test_unsubscribe_stops_delivery():
    b = SubagentBroadcaster()
    received: list[dict] = []
    unsub = b.subscribe(received.append)
    b.publish({"type": "subagent_started", "subagent_id": "ep_a"})
    assert len(received) == 1
    unsub()
    b.publish({"type": "subagent_started", "subagent_id": "ep_b"})
    assert len(received) == 1   # second publish not delivered


def test_subscriber_exception_does_not_kill_others():
    b = SubagentBroadcaster()
    good: list[dict] = []
    def bad(_frame):
        raise RuntimeError("boom")
    b.subscribe(bad)
    b.subscribe(good.append)
    b.publish({"type": "subagent_finished", "subagent_id": "ep_x"})
    # The good subscriber must still receive the frame even though the bad one raised.
    assert len(good) == 1


def test_publish_with_no_subscribers_is_a_noop():
    b = SubagentBroadcaster()
    b.publish({"type": "subagent_started"})   # must not raise
