from __future__ import annotations
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from unittest.mock import MagicMock

from kc_subagents.templates import SubagentIndex
from kc_supervisor.http_routes import register_http_routes
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.storage import Storage
from kc_supervisor.conversations import ConversationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(
    subagent_templates_dir: Path | None,
    subagent_index,
    subagent_runner,
    storage=None,
    conversations=None,
):
    """Build a minimal FastAPI app with only the subagent routes mounted."""
    app = FastAPI()
    app.state.deps = SimpleNamespace(
        # Required by register_http_routes (health route accesses registry.names())
        started_at=0.0,
        registry=SimpleNamespace(names=lambda: [], snapshot=lambda: []),
        conversations=conversations,
        storage=storage,
        news_client=None,
        schedule_service=None,
        skill_index=None,
        home=None,
        approvals=ApprovalBroker(),
        todo_storage=None,
        # Subagent fields
        subagent_index=subagent_index,
        subagent_runner=subagent_runner,
        subagent_templates_dir=subagent_templates_dir,
        subagent_trace_buffer=None,
    )
    register_http_routes(app)
    return app


def _make_storage_app(tmp_path: Path):
    """Build an app + storage pair suitable for testing storage-backed routes."""
    s = Storage(db_path=tmp_path / "test.sqlite")
    s.init()
    cm = ConversationManager(s)
    mock_runner = MagicMock()
    app = _make_app(
        subagent_templates_dir=None,
        subagent_index=None,
        subagent_runner=mock_runner,
        storage=s,
        conversations=cm,
    )
    return TestClient(app, raise_server_exceptions=True), s, cm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def subagent_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    return d


@pytest.fixture
def client_with_subagents(subagent_dir):
    """TestClient whose Deps has a real SubagentIndex backed by a tmp dir,
    plus a MagicMock runner. Returns (TestClient, templates_dir, mock_runner)."""
    mock_runner = MagicMock()
    index = SubagentIndex(subagent_dir)
    app = _make_app(
        subagent_templates_dir=subagent_dir,
        subagent_index=index,
        subagent_runner=mock_runner,
    )
    return TestClient(app, raise_server_exceptions=True), subagent_dir, mock_runner


# ---------------------------------------------------------------------------
# Tests — template CRUD
# ---------------------------------------------------------------------------

def test_list_templates_empty(client_with_subagents):
    client, _, _ = client_with_subagents
    r = client.get("/subagent-templates")
    assert r.status_code == 200
    assert r.json() == []


def test_create_then_list_template(client_with_subagents):
    client, dir_, _ = client_with_subagents
    body = {"yaml": "name: web-researcher\nmodel: m\nsystem_prompt: research\n"}
    r = client.post("/subagent-templates", json=body)
    assert r.status_code == 201
    assert (dir_ / "web-researcher.yaml").exists()
    r2 = client.get("/subagent-templates")
    rows = r2.json()
    assert any(t["name"] == "web-researcher" for t in rows)


def test_get_template_returns_yaml_body(client_with_subagents):
    client, dir_, _ = client_with_subagents
    (dir_ / "coder.yaml").write_text("name: coder\nmodel: m\nsystem_prompt: x\n")
    r = client.get("/subagent-templates/coder")
    assert r.status_code == 200
    j = r.json()
    assert j["name"] == "coder"
    assert "system_prompt: x" in j["yaml"]


def test_patch_template_updates_disk(client_with_subagents):
    client, dir_, _ = client_with_subagents
    (dir_ / "coder.yaml").write_text("name: coder\nmodel: m1\nsystem_prompt: x\n")
    body = {"yaml": "name: coder\nmodel: m2\nsystem_prompt: x\n"}
    r = client.patch("/subagent-templates/coder", json=body)
    assert r.status_code == 200
    assert "model: m2" in (dir_ / "coder.yaml").read_text()


def test_delete_template_removes_file(client_with_subagents):
    client, dir_, _ = client_with_subagents
    (dir_ / "coder.yaml").write_text("name: coder\nmodel: m\nsystem_prompt: x\n")
    r = client.delete("/subagent-templates/coder")
    assert r.status_code == 204
    assert not (dir_ / "coder.yaml").exists()


def test_create_template_rejects_bad_yaml(client_with_subagents):
    client, _, _ = client_with_subagents
    r = client.post("/subagent-templates", json={"yaml": ":::nope:::"})
    assert r.status_code == 422


def test_create_template_conflict_when_exists(client_with_subagents):
    client, dir_, _ = client_with_subagents
    (dir_ / "x.yaml").write_text("name: x\nmodel: m\nsystem_prompt: y\n")
    body = {"yaml": "name: x\nmodel: m\nsystem_prompt: y\n"}
    r = client.post("/subagent-templates", json=body)
    assert r.status_code == 409


def test_get_unknown_template_returns_404(client_with_subagents):
    client, _, _ = client_with_subagents
    r = client.get("/subagent-templates/nope")
    assert r.status_code == 404


def test_patch_unknown_template_returns_404(client_with_subagents):
    client, _, _ = client_with_subagents
    r = client.patch("/subagent-templates/nope", json={"yaml": "name: nope\nmodel: m\nsystem_prompt: x\n"})
    assert r.status_code == 404


def test_delete_unknown_template_returns_404(client_with_subagents):
    client, _, _ = client_with_subagents
    r = client.delete("/subagent-templates/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests — active runs + stop
# ---------------------------------------------------------------------------

def test_active_subagents_returns_runner_state(client_with_subagents):
    client, _, mock_runner = client_with_subagents
    mock_runner.active.return_value = [
        {"subagent_id": "ep_a", "template": "x", "label": None,
         "parent_conversation_id": "c", "tool_calls_used": 2}
    ]
    r = client.get("/subagents/active")
    assert r.status_code == 200
    assert r.json() == mock_runner.active.return_value


def test_stop_subagent_calls_runner(client_with_subagents):
    client, _, mock_runner = client_with_subagents
    mock_runner.stop.return_value = True
    r = client.post("/subagents/ep_abc/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": True}
    mock_runner.stop.assert_called_once_with("ep_abc")


def test_stop_subagent_not_found_returns_false(client_with_subagents):
    client, _, mock_runner = client_with_subagents
    mock_runner.stop.return_value = False
    r = client.post("/subagents/ep_gone/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": False}


# ---------------------------------------------------------------------------
# Tests — GET /conversations/{cid}/subagent-runs
# ---------------------------------------------------------------------------

def test_list_subagent_runs_for_unknown_conv_returns_empty(tmp_path):
    client, s, cm = _make_storage_app(tmp_path)
    r = client.get("/conversations/99999/subagent-runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_list_subagent_runs_with_data(tmp_path):
    client, s, cm = _make_storage_app(tmp_path)
    # Create a conversation
    cid = cm.start(agent="Kona-AI", channel="dashboard")

    # Insert a subagent run for this conversation
    s.start_subagent_run(
        id="ep_aabbcc",
        parent_conversation_id=str(cid),
        parent_agent="Kona-AI",
        template="web-researcher",
        label="test run",
        task_preview="do something",
        context_keys=["key1"],
    )
    s.finish_subagent_run(
        id="ep_aabbcc",
        status="ok",
        duration_ms=1200,
        tool_calls_used=2,
        reply_chars=30,
        error_message=None,
        reply_text="The answer is 42.",
    )

    # Insert some audit rows referencing this subagent run
    s.append_audit(
        agent="Kona-AI/ep_aabbcc/web-researcher",
        tool="web_fetch",
        args_json='{"url":"http://example.com"}',
        decision="tier",
        result="<html>...</html>",
        undoable=False,
        subagent_id="ep_aabbcc",
        subagent_template="web-researcher",
        parent_agent="Kona-AI",
    )
    s.append_audit(
        agent="Kona-AI/ep_aabbcc/web-researcher",
        tool="read_file",
        args_json='{"path":"/tmp/x"}',
        decision="denied",
        result=None,
        undoable=False,
        subagent_id="ep_aabbcc",
        subagent_template="web-researcher",
        parent_agent="Kona-AI",
    )

    r = client.get(f"/conversations/{cid}/subagent-runs")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["id"] == "ep_aabbcc"
    assert run["template"] == "web-researcher"
    assert run["status"] == "ok"
    assert run["reply_text"] == "The answer is 42."
    assert len(run["tools"]) == 2
    assert run["tools"][0]["tool"] == "web_fetch"
    assert run["tools"][1]["tool"] == "read_file"


def test_list_subagent_runs_does_not_leak_other_conversations(tmp_path):
    client, s, cm = _make_storage_app(tmp_path)
    cid1 = cm.start(agent="Kona-AI", channel="dashboard")
    cid2 = cm.start(agent="Kona-AI", channel="dashboard")

    # Run for cid2 only
    s.start_subagent_run(
        id="ep_other",
        parent_conversation_id=str(cid2),
        parent_agent="Kona-AI",
        template="coder",
        label=None,
        task_preview=None,
        context_keys=None,
    )
    s.finish_subagent_run(
        id="ep_other", status="ok", duration_ms=100,
        tool_calls_used=0, reply_chars=5, error_message=None,
        reply_text="done",
    )

    r = client.get(f"/conversations/{cid1}/subagent-runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_messages_now_include_ts(tmp_path):
    client, s, cm = _make_storage_app(tmp_path)
    cid = cm.start(agent="Kona-AI", channel="dashboard")
    from kc_core.messages import UserMessage, AssistantMessage
    cm.append(cid, UserMessage(content="hello"))
    cm.append(cid, AssistantMessage(content="world"))

    r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 2
    for m in msgs:
        assert "ts" in m, f"missing ts in message: {m}"
        assert isinstance(m["ts"], float)


# ---------------------------------------------------------------------------
# Tests — subagents disabled (index=None)
# ---------------------------------------------------------------------------

def test_routes_503_when_subagents_disabled(tmp_path):
    """When deps.subagent_index is None, mutating routes return 503;
    read-only list routes return empty lists."""
    app = _make_app(
        subagent_templates_dir=None,
        subagent_index=None,
        subagent_runner=None,
    )
    client = TestClient(app, raise_server_exceptions=True)

    # Read-only routes return empty lists (not 503)
    r = client.get("/subagent-templates")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get("/subagents/active")
    assert r.status_code == 200
    assert r.json() == []

    # Mutating / single-resource routes return 503
    r = client.get("/subagent-templates/foo")
    assert r.status_code == 503

    r = client.post("/subagent-templates", json={"yaml": "name: x\nmodel: m\nsystem_prompt: y\n"})
    assert r.status_code == 503

    r = client.patch("/subagent-templates/foo", json={"yaml": "name: foo\nmodel: m\nsystem_prompt: y\n"})
    assert r.status_code == 503

    r = client.delete("/subagent-templates/foo")
    assert r.status_code == 503

    r = client.post("/subagents/ep_abc/stop")
    assert r.status_code == 503
