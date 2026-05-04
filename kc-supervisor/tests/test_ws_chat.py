import pytest
from fastapi.testclient import TestClient
from kc_core.ollama_client import ChatResponse


@pytest.fixture
def fake_client_factory():
    """A minimal kc-core-compatible fake chat client."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeClient:
        responses: list[ChatResponse]
        calls: list = field(default_factory=list)
        model: str = "fake-model"

        def __post_init__(self):
            self._iter = iter(self.responses)

        async def chat(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            return next(self._iter)

    return FakeClient


def test_ws_user_message_round_trip(app, deps, fake_client_factory):
    """Happy path: send user message, receive assistant_complete, both persist."""
    from kc_core.agent import Agent as CoreAgent
    from kc_core.tools import ToolRegistry

    fake = fake_client_factory(responses=[
        ChatResponse(text="Hello back!", finish_reason="stop"),
    ])
    rt = deps.registry.get("alice")
    rt.core_agent = CoreAgent(
        name="alice",
        client=fake,
        system_prompt=rt.system_prompt,
        tools=ToolRegistry(),
    )

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

    types_seen = [e["type"] for e in seen]
    assert "agent_status" in types_seen  # thinking event before completion
    assert any(
        e["type"] == "assistant_complete" and "Hello back" in e["content"]
        for e in seen
    )

    # Persistence
    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" and m.content == "hi" for m in msgs)
    assert any(
        m.__class__.__name__ == "AssistantMessage" and "Hello back" in m.content
        for m in msgs
    )


def test_ws_unknown_conversation_id_emits_error_and_closes(app):
    """Connecting to a non-existent cid should send an error frame and close."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/99999") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "unknown conversation" in msg["message"]


def test_ws_agent_not_initialized_emits_error(app, deps):
    """If rt.core_agent is None (v1 default), the WS should send an error and close."""
    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        # core_agent is None by default in v1 — no monkey-patch
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "not initialized" in msg["message"]


def test_ws_unexpected_inbound_type_emits_error_then_continues(app, deps, fake_client_factory):
    """Inbound with type != user_message should produce an error event and the connection stays alive."""
    from kc_core.agent import Agent as CoreAgent
    from kc_core.tools import ToolRegistry

    fake = fake_client_factory(responses=[ChatResponse(text="ok", finish_reason="stop")])
    rt = deps.registry.get("alice")
    rt.core_agent = CoreAgent(
        name="alice", client=fake, system_prompt=rt.system_prompt, tools=ToolRegistry(),
    )

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "garbage"})
            err = ws.receive_json()
            assert err["type"] == "error"
            # connection still alive — send a valid message
            ws.send_json({"type": "user_message", "content": "hi"})
            seen = []
            while True:
                m = ws.receive_json()
                seen.append(m)
                if m["type"] == "assistant_complete":
                    break
            assert seen[-1]["content"] == "ok"
