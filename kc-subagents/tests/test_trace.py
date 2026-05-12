from kc_subagents.trace import TraceBuffer


def test_buffer_replays_in_order():
    buf = TraceBuffer()
    buf.append("conv_1", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("conv_1", {"type": "subagent_tool",    "subagent_id": "ep_a", "tool": "x"})
    buf.append("conv_2", {"type": "subagent_started", "subagent_id": "ep_b"})
    assert [f["type"] for f in buf.snapshot("conv_1")] == [
        "subagent_started", "subagent_tool",
    ]
    assert [f["type"] for f in buf.snapshot("conv_2")] == ["subagent_started"]


def test_buffer_evicts_on_finished_frame():
    buf = TraceBuffer()
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_tool",    "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_finished", "subagent_id": "ep_a", "status": "ok"})
    assert buf.snapshot("c") == []


def test_buffer_keeps_other_instance_frames_after_one_finishes():
    buf = TraceBuffer()
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_b"})
    buf.append("c", {"type": "subagent_finished", "subagent_id": "ep_a", "status": "ok"})
    snap = buf.snapshot("c")
    assert [f["subagent_id"] for f in snap] == ["ep_b"]
