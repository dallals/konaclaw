from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from kc_skills import SkillIndex


def _seed_skill(root: Path, name: str = "hello") -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: D\n---\n\nLoad this skill body.\n"
    )


def test_slash_command_persists_original_text_passes_loaded_to_agent(deps, app, tmp_path):
    """When the user sends /hello with an instruction, the persisted message
    is the user's original text but send_stream sees the loaded skill body."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed_skill(skills_root)
    deps.skill_index = SkillIndex(skills_root)

    cid = deps.conversations.get_or_create(
        channel="dashboard", chat_id="c1", agent="alice",
    )

    rt = deps.registry.get("alice")

    async def fake_stream(content):
        from kc_core.stream_frames import Complete
        from kc_core.messages import AssistantMessage
        fake_stream.captured.append(content)
        yield Complete(reply=AssistantMessage(content="ok"))

    fake_stream.captured = []
    rt.assembled.core_agent.send_stream = fake_stream  # type: ignore

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "/hello set up SSH"})
            seen_complete = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg.get("type") == "assistant_complete":
                    seen_complete = True
                    break
            assert seen_complete

    msgs = deps.conversations.list_messages(cid)
    user_msgs = [m for m in msgs if type(m).__name__ == "UserMessage"]
    assert any(m.content == "/hello set up SSH" for m in user_msgs)

    assert fake_stream.captured, "send_stream was never called"
    captured_input = fake_stream.captured[0]
    assert "[Skill activation: hello]" in captured_input
    assert "Load this skill body." in captured_input
    assert "The user's instruction is: set up SSH" in captured_input


def test_unknown_slash_command_passes_through(deps, app, tmp_path):
    """An unknown /command falls through as plain text — both persisted
    AND sent to send_stream verbatim."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    deps.skill_index = SkillIndex(skills_root)

    cid = deps.conversations.get_or_create(
        channel="dashboard", chat_id="c1", agent="alice",
    )
    rt = deps.registry.get("alice")

    async def fake_stream(content):
        from kc_core.stream_frames import Complete
        from kc_core.messages import AssistantMessage
        fake_stream.captured.append(content)
        yield Complete(reply=AssistantMessage(content="ok"))

    fake_stream.captured = []
    rt.assembled.core_agent.send_stream = fake_stream  # type: ignore

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "/nope foo"})
            for _ in range(20):
                msg = ws.receive_json()
                if msg.get("type") == "assistant_complete":
                    break

    assert fake_stream.captured == ["/nope foo"]
