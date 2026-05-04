from pathlib import Path
import pytest
from kc_core.ollama_client import ChatResponse
from kc_sandbox.permissions import AlwaysAllow
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
