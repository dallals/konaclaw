import asyncio
import pytest
from fastapi.testclient import TestClient
from kc_core.ollama_client import ChatResponse
from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done


@pytest.fixture
def fake_client_factory():
    """A minimal kc-core-compatible fake chat client supporting both chat and chat_stream."""
    from dataclasses import dataclass, field
    from kc_core.stream_frames import TextDelta, Done
    from typing import Any

    @dataclass
    class FakeClient:
        responses: list[ChatResponse] = field(default_factory=list)
        stream_responses: list[list[Any]] = field(default_factory=list)
        calls: list = field(default_factory=list)
        model: str = "fake-model"

        def __post_init__(self):
            self._iter = iter(self.responses)
            if not self.stream_responses and self.responses:
                self.stream_responses = [
                    (
                        ([TextDelta(content=r.text)] if r.text else [])
                        + [Done(finish_reason=r.finish_reason)]
                    ) for r in self.responses
                ]
            self._stream_iter = iter(self.stream_responses)

        async def chat(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            return next(self._iter)

        async def chat_stream(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            for f in next(self._stream_iter):
                yield f

    return FakeClient


def _inject_fake(deps, agent_name: str, fake):
    """Swap the kc-core Agent's client on the assembled agent with a fake."""
    rt = deps.registry.get(agent_name)
    assert rt.assembled is not None, f"agent {agent_name} not assembled in fixture"
    rt.assembled.core_agent.client = fake


def test_ws_streaming_round_trip_yields_token_then_complete(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[[
        TextDelta(content="Hello "),
        TextDelta(content="back!"),
        Done(finish_reason="stop"),
    ]])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            seen = []
            while True:
                msg = ws.receive_json()
                seen.append(msg)
                if msg["type"] == "assistant_complete":
                    break

    types = [m["type"] for m in seen]
    assert "agent_status" in types
    assert "token" in types
    tokens = [m for m in seen if m["type"] == "token"]
    assert "".join(t["delta"] for t in tokens) == "Hello back!"
    assert seen[-1]["content"] == "Hello back!"

    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" and m.content == "hi" for m in msgs)
    assert any(
        m.__class__.__name__ == "AssistantMessage" and m.content == "Hello back!"
        for m in msgs
    )


def test_ws_streaming_with_tool_call(app, deps, fake_client_factory):
    """Two-turn flow with a tool call. file.list is SAFE — no approval needed."""
    fake = fake_client_factory(stream_responses=[
        [
            ToolCallsBlock(calls=[{
                "id": "c1", "name": "file.list", "arguments": {"share": "main", "relpath": "."},
            }]),
            Done(finish_reason="tool_calls"),
        ],
        [
            TextDelta(content="Empty share."),
            Done(finish_reason="stop"),
        ],
    ])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "list files"})
            frames = []
            while True:
                m = ws.receive_json()
                frames.append(m)
                if m["type"] == "assistant_complete":
                    break

    types = [f["type"] for f in frames]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types.index("tool_call") < types.index("tool_result")
    assert frames[-1]["content"] == "Empty share."

    rows = deps.storage.list_audit()
    assert any(r["agent"] == "alice" and r["tool"] == "file.list" for r in rows)


def test_ws_history_rehydration_across_turns(app, deps, fake_client_factory):
    """Second turn sees prior turn's history because we rehydrate from SQLite."""
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="reply 1"), Done(finish_reason="stop")],
        [TextDelta(content="reply 2"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "first"})
            while ws.receive_json()["type"] != "assistant_complete":
                pass
            ws.send_json({"type": "user_message", "content": "second"})
            while ws.receive_json()["type"] != "assistant_complete":
                pass

    second_call_messages = fake.calls[1]["messages"]
    user_msgs = [m for m in second_call_messages if m.get("role") == "user"]
    assert len(user_msgs) == 2
    assert user_msgs[0]["content"] == "first"
    assert user_msgs[1]["content"] == "second"


def test_ws_unknown_conversation_id_emits_error_and_closes(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/99999") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "unknown conversation" in msg["message"]


def test_ws_agent_not_initialized_emits_error(app, deps):
    """If rt.assembled is None (degraded), the WS should send error and close."""
    from kc_supervisor.agents import AgentStatus
    rt = deps.registry.get("alice")
    rt.assembled = None
    rt.last_error = "synthetic test failure"
    rt.set_status(AgentStatus.DEGRADED)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "degraded" in msg["message"] or "synthetic" in msg["message"]


def test_ws_unexpected_inbound_type_emits_error_then_continues(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="ok"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)
    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "garbage"})
            err = ws.receive_json()
            assert err["type"] == "error"
            ws.send_json({"type": "user_message", "content": "hi"})
            while True:
                m = ws.receive_json()
                if m["type"] == "assistant_complete":
                    assert m["content"] == "ok"
                    break


def test_ws_user_message_with_empty_content_is_rejected(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="should not be called"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)
    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": ""})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "non-empty" in err["message"]
    assert fake.calls == []


def test_ws_streaming_error_mid_stream_emits_error_frame_and_no_assistant_persisted(app, deps):
    """If chat_stream raises, the WS handler emits error frame and does NOT persist AssistantMessage."""
    from dataclasses import dataclass

    @dataclass
    class FailingClient:
        model: str = "fake-model"
        async def chat(self, messages, tools):
            raise RuntimeError("ollama down")
        async def chat_stream(self, messages, tools):
            raise RuntimeError("ollama down")
            yield  # pragma: no cover (unreachable, just to make this a generator)

    _inject_fake(deps, "alice", FailingClient())

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            # Drain frames until we see the error frame (which is final for this turn).
            # WS stays open per the contract — exiting the with-block closes from the
            # client side.
            seen = []
            while True:
                msg = ws.receive_json()
                seen.append(msg)
                if msg["type"] == "error" and msg.get("stage") == "model_call":
                    break

    err_frames = [f for f in seen if f["type"] == "error"]
    assert any("ollama" in f.get("message", "").lower() for f in err_frames)

    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" for m in msgs)
    assert not any(m.__class__.__name__ == "AssistantMessage" for m in msgs)
