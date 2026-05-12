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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(
    subagent_templates_dir: Path | None,
    subagent_index,
    subagent_runner,
):
    """Build a minimal FastAPI app with only the subagent routes mounted."""
    app = FastAPI()
    app.state.deps = SimpleNamespace(
        # Required by register_http_routes (health route accesses registry.names())
        started_at=0.0,
        registry=SimpleNamespace(names=lambda: [], snapshot=lambda: []),
        conversations=None,
        storage=None,
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
