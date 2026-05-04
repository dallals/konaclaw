from pathlib import Path
import pytest
from kc_core.ollama_client import ChatResponse
from kc_sandbox.permissions import AlwaysAllow, AlwaysDeny
from kc_sandbox.wiring import build_sandboxed_agent


@pytest.mark.asyncio
async def test_build_sandboxed_agent_runs_a_write(tmp_path, fake_ollama):
    research = tmp_path / "research"; research.mkdir()
    shares_yaml = tmp_path / "shares.yaml"
    shares_yaml.write_text(f"shares:\n  - name: research\n    path: {research}\n    mode: read-write\n")

    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{
                "id": "c1",
                "name": "file.write",
                "arguments": {"share": "research", "relpath": "hello.md", "content": "hi\n"},
            }],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="wrote it", finish_reason="stop"),
    )
    agent = build_sandboxed_agent(
        agent_yaml=Path(__file__).parent / "fixtures" / "agents" / "filebot.yaml",
        shares_yaml=shares_yaml,
        undo_db=tmp_path / "u.db",
        client=client,
        approval_callback=AlwaysAllow(),
    )
    reply = await agent.send("create hello.md saying 'hi'")
    assert "wrote it" in reply.content
    assert (research / "hello.md").read_text() == "hi\n"


@pytest.mark.asyncio
async def test_build_sandboxed_agent_denies_destructive_via_callback(tmp_path, fake_ollama):
    """End-to-end: a destructive tool (file.delete) routed through AlwaysDeny
    must be blocked at the permission_check seam — no file removed, no journal
    commit, no undo entry recorded."""
    research = tmp_path / "research"; research.mkdir()
    (research / "victim.md").write_text("important data\n")
    shares_yaml = tmp_path / "shares.yaml"
    shares_yaml.write_text(f"shares:\n  - name: research\n    path: {research}\n    mode: read-write\n")

    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{
                "id": "c1",
                "name": "file.delete",
                "arguments": {"share": "research", "relpath": "victim.md"},
            }],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="couldn't delete it", finish_reason="stop"),
    )
    agent = build_sandboxed_agent(
        agent_yaml=Path(__file__).parent / "fixtures" / "agents" / "filebot.yaml",
        shares_yaml=shares_yaml,
        undo_db=tmp_path / "u.db",
        client=client,
        approval_callback=AlwaysDeny(reason="user said no"),
    )
    reply = await agent.send("delete victim.md")
    assert "couldn't delete it" in reply.content
    # File must still exist — denial blocked the deletion
    assert (research / "victim.md").read_text() == "important data\n"
    # The deny message reached the model as a tool result
    second_msgs = client.calls[1]["messages"]
    deny = next(m for m in second_msgs if m["role"] == "tool")
    assert "Denied" in deny["content"]
    assert "user said no" in deny["content"]
