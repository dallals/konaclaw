import asyncio
import threading
import pytest
from fastapi.testclient import TestClient


def test_approval_request_broadcasts_and_resolves(app, deps):
    """Round-trip an approval request: agent loop → ws broadcast → ws response → resolution.

    The agent's request_approval() runs in a separate thread with its own loop,
    while the WS handler runs on TestClient's loop. resolve() bridges them via
    call_soon_threadsafe (configured in ApprovalBroker.resolve).
    """
    broker = deps.approvals
    result_box: dict = {}

    def trigger():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            allowed, reason = loop.run_until_complete(
                broker.request_approval(
                    agent="alice", tool="file.delete", arguments={"share": "r"}
                )
            )
            result_box["allowed"] = allowed
            result_box["reason"] = reason
        finally:
            loop.close()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/approvals") as ws:
            t = threading.Thread(target=trigger, daemon=True)
            t.start()

            msg = ws.receive_json()
            assert msg["type"] == "approval_request"
            assert msg["agent"] == "alice"
            assert msg["tool"] == "file.delete"
            assert msg["arguments"] == {"share": "r"}
            req_id = msg["request_id"]

            ws.send_json({
                "type": "approval_response",
                "request_id": req_id,
                "allowed": True,
                "reason": None,
            })

            t.join(timeout=2.0)
            assert not t.is_alive(), "trigger thread did not complete"

    assert result_box["allowed"] is True
    assert result_box["reason"] is None


def test_approval_request_deny(app, deps):
    """Deny path: response with allowed=False resolves with the reason intact."""
    broker = deps.approvals
    result_box: dict = {}

    def trigger():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            allowed, reason = loop.run_until_complete(
                broker.request_approval(
                    agent="alice", tool="file.delete", arguments={}
                )
            )
            result_box["allowed"] = allowed
            result_box["reason"] = reason
        finally:
            loop.close()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/approvals") as ws:
            t = threading.Thread(target=trigger, daemon=True)
            t.start()
            msg = ws.receive_json()
            ws.send_json({
                "type": "approval_response",
                "request_id": msg["request_id"],
                "allowed": False,
                "reason": "user declined",
            })
            t.join(timeout=2.0)

    assert result_box["allowed"] is False
    assert result_box["reason"] == "user declined"


def test_approval_replays_pending_on_connect(app, deps):
    """A request that's already pending when the dashboard connects should be replayed."""
    broker = deps.approvals

    def trigger():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                broker.request_approval(agent="alice", tool="x", arguments={})
            )
        finally:
            loop.close()

    t = threading.Thread(target=trigger, daemon=True)
    t.start()
    # Wait until the broker has seen the request
    import time
    deadline = time.time() + 1.0
    while time.time() < deadline and not broker.pending():
        time.sleep(0.01)
    assert broker.pending(), "broker did not register the request"
    pending_req_id = broker.pending()[0].request_id

    with TestClient(app) as client:
        with client.websocket_connect("/ws/approvals") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "approval_request"
            assert msg["request_id"] == pending_req_id
            ws.send_json({
                "type": "approval_response",
                "request_id": pending_req_id,
                "allowed": True,
                "reason": None,
            })
            t.join(timeout=2.0)
            assert not t.is_alive()


def test_malformed_approval_response_does_not_crash_handler(app, deps):
    """approval_response with no request_id should be ignored, connection stays alive."""
    broker = deps.approvals
    result_box: dict = {}

    def trigger():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            allowed, reason = loop.run_until_complete(
                broker.request_approval(agent="alice", tool="x", arguments={})
            )
            result_box["allowed"] = allowed
            result_box["reason"] = reason
        finally:
            loop.close()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/approvals") as ws:
            t = threading.Thread(target=trigger, daemon=True)
            t.start()
            req_msg = ws.receive_json()
            req_id = req_msg["request_id"]
            # Malformed: no request_id
            ws.send_json({"type": "approval_response", "allowed": True})
            # Connection should still be alive — send a proper response
            ws.send_json({
                "type": "approval_response",
                "request_id": req_id,
                "allowed": False,
                "reason": "via second response",
            })
            t.join(timeout=2.0)
            assert not t.is_alive()

    assert result_box["allowed"] is False
    assert result_box["reason"] == "via second response"
