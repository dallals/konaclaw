"""Integration tests for the subagent WS branches introduced in Task 15.

- subagent_stop inbound frame → deps.subagent_runner.stop(subagent_id)
- On WS connect, buffered frames from subagent_trace_buffer are replayed
- _handle_subagent_stop_frame unit test (faster, no WS overhead)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from kc_supervisor.ws_routes import _handle_subagent_stop_frame


# ---------------------------------------------------------------------------
# Unit tests for _handle_subagent_stop_frame helper
# ---------------------------------------------------------------------------

def test_handle_subagent_stop_calls_runner_stop():
    """Helper must call runner.stop with the given subagent_id."""
    mock_runner = MagicMock()
    deps = MagicMock()
    deps.subagent_runner = mock_runner
    _handle_subagent_stop_frame(deps, {"type": "subagent_stop", "subagent_id": "ep_abc"})
    mock_runner.stop.assert_called_once_with("ep_abc")


def test_handle_subagent_stop_ignores_missing_runner():
    """When subagent_runner is None, no error should be raised."""
    deps = MagicMock()
    deps.subagent_runner = None
    # Should not raise
    _handle_subagent_stop_frame(deps, {"type": "subagent_stop", "subagent_id": "ep_abc"})


def test_handle_subagent_stop_ignores_non_string_subagent_id():
    """If subagent_id is not a string (e.g. int or missing), do nothing."""
    mock_runner = MagicMock()
    deps = MagicMock()
    deps.subagent_runner = mock_runner
    _handle_subagent_stop_frame(deps, {"type": "subagent_stop", "subagent_id": 42})
    mock_runner.stop.assert_not_called()

    _handle_subagent_stop_frame(deps, {"type": "subagent_stop"})
    mock_runner.stop.assert_not_called()


# ---------------------------------------------------------------------------
# Integration fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def deps_with_runner(deps):
    """Extends the base `deps` fixture with a mock subagent_runner."""
    mock_runner = MagicMock()
    deps.subagent_runner = mock_runner
    return deps, mock_runner


@pytest.fixture
def deps_with_trace_buffer(deps):
    """Extends the base `deps` fixture with a real TraceBuffer (empty; tests seed it themselves)."""
    from kc_subagents.trace import TraceBuffer
    buf = TraceBuffer()
    deps.subagent_trace_buffer = buf
    return deps


# ---------------------------------------------------------------------------
# WS integration tests
# ---------------------------------------------------------------------------

def test_subagent_stop_frame_routes_to_runner(app, deps_with_runner):
    """When the client sends {'type':'subagent_stop','subagent_id':'ep_x'},
    the supervisor must call deps.subagent_runner.stop('ep_x')."""
    import time
    deps, mock_runner = deps_with_runner
    # app already has deps baked in via app.state.deps; update in place.
    app.state.deps = deps

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "subagent_stop", "subagent_id": "ep_abc"})
            # The handler is continue-style — no reply. Wait a brief moment for
            # the message to dispatch through the WS event loop.
            time.sleep(0.1)

    mock_runner.stop.assert_called_once_with("ep_abc")


def test_subagent_stop_with_no_runner_does_not_error(app, deps):
    """subagent_stop with no runner wired must not crash the connection."""
    # deps.subagent_runner is None by default in the base fixture.
    assert deps.subagent_runner is None

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "subagent_stop", "subagent_id": "ep_abc"})
            import time; time.sleep(0.05)
            # Connection should still be alive — send another message and expect a response.
            ws.send_json({"type": "garbage_type_to_test_alive"})
            err = ws.receive_json()
            assert err["type"] == "error"  # the garbage type error — WS is alive


def test_subagent_trace_buffer_replays_on_connect(app, deps_with_trace_buffer):
    """On WS connect, any buffered subagent frames for this conversation must
    be sent to the client immediately."""
    app.state.deps = deps_with_trace_buffer

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        # Seed the trace buffer with a frame for this specific conversation id.
        deps_with_trace_buffer.subagent_trace_buffer.append(str(cid), {
            "type": "subagent_started",
            "subagent_id": "ep_buffered",
            "parent_conversation_id": str(cid),
            "template": "web-researcher",
            "label": None,
            "task_preview": "do research",
        })
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            msg = ws.receive_json()
            # The buffered frame for this conversation should arrive on connect.
            assert msg["type"] == "subagent_started"
            assert msg["subagent_id"] == "ep_buffered"


def test_subagent_trace_buffer_not_wired_is_no_op(app, deps):
    """When subagent_trace_buffer is None, the WS connect must succeed without error."""
    assert deps.subagent_trace_buffer is None

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            # No buffered frames → first thing we receive is from the inbound loop.
            ws.send_json({"type": "garbage_check"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "garbage" in err["message"] or "unexpected" in err["message"]
