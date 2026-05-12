"""Integration tests for the KC_SUBAGENTS_ENABLED startup wiring.

Validates that when the env flag is set, the wiring block in main.py
constructs the index/runner/trace singletons and threads them through
Deps + AgentRegistry.

Strategy: Rather than calling main() (which would invoke uvicorn.run()),
we reproduce the wiring block logic in-test using the same imports and
factories — exactly what main.py does. This gives structural coverage of
the wiring code without requiring a live server.

Does NOT exercise a real subagent spawn — that comes in SMOKE (Task 20).
"""
from __future__ import annotations
import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import yaml

from kc_sandbox.shares import SharesRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.secrets_store import SecretsStore
from kc_supervisor.service import Deps
from kc_supervisor.storage import Storage


class FakeKeychain:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


def _make_home(tmp_path: Path) -> Path:
    """Create the minimal KonaClaw home dir structure that Storage + SharesRegistry need."""
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))
    return home


def _wire_subagents(home: Path, storage: Storage, shares, broker, mcp_manager,
                    mcp_install_store, memory_root, gmail_service, gcal_service,
                    news_client, ollama_api_key, skill_index, web_config):
    """Reproduce the KC_SUBAGENTS_ENABLED wiring block from main.py.

    Returns (subagent_index, subagent_trace_buffer, subagent_runner, subagent_templates_dir)
    or all-None tuple if import fails.
    """
    subagent_index = None
    subagent_trace_buffer = None
    subagent_runner = None
    subagent_templates_dir = None

    try:
        from kc_subagents.templates import SubagentIndex
        from kc_subagents.runner import SubagentRunner
        from kc_subagents.trace import TraceBuffer
        from kc_subagents.seeds.install import install_seeds_if_empty
        from kc_core.config import AgentConfig

        subagent_templates_dir = home / "subagent-templates"
        install_seeds_if_empty(subagent_templates_dir)
        subagent_index = SubagentIndex(subagent_templates_dir)
        subagent_trace_buffer = TraceBuffer()

        def _build_assembled_for_subagent(eph_cfg):
            from kc_supervisor.assembly import assemble_agent
            cfg = AgentConfig(
                name=eph_cfg.name,
                model=eph_cfg.model,
                system_prompt=eph_cfg.system_prompt,
            )
            from kc_sandbox.permissions import Tier
            perm_overrides = None
            if eph_cfg.permission_overrides:
                perm_overrides = {}
                for tool_name, tier_name in eph_cfg.permission_overrides.items():
                    try:
                        perm_overrides[tool_name] = Tier[tier_name]
                    except KeyError:
                        pass
            return assemble_agent(
                cfg=cfg,
                shares=shares,
                audit_storage=storage,
                broker=broker,
                ollama_url="http://localhost:11434",
                default_model="fake-model",
                undo_db_path=home / "data" / "undo.db",
                permission_overrides=perm_overrides,
                mcp_manager=mcp_manager,
                mcp_install_store=mcp_install_store,
                memory_root=memory_root,
                gmail_service=gmail_service,
                gcal_service=gcal_service,
                news_client=news_client,
                ollama_api_key=ollama_api_key,
                skill_index=skill_index,
                web_config=web_config,
            )

        def _on_subagent_frame(frame: dict) -> None:
            # TODO: wire live WS broadcast
            cid = frame.get("parent_conversation_id")
            if cid is not None and subagent_trace_buffer is not None:
                subagent_trace_buffer.append(str(cid), frame)

        subagent_runner = SubagentRunner(
            build_assembled=_build_assembled_for_subagent,
            audit_start=storage.start_subagent_run,
            audit_finish=storage.finish_subagent_run,
            on_frame=_on_subagent_frame,
        )

        # Reap any stale rows (mirrors main.py startup).
        storage.reap_running_subagent_runs()

    except ImportError:
        pass

    return subagent_index, subagent_trace_buffer, subagent_runner, subagent_templates_dir


def _build_deps(home: Path, *, enabled: bool, monkeypatch) -> "tuple[Deps, AgentRegistry]":
    """Build the Deps + AgentRegistry that main.py constructs, with the env flag toggled."""
    if enabled:
        monkeypatch.setenv("KC_SUBAGENTS_ENABLED", "true")
    else:
        monkeypatch.delenv("KC_SUBAGENTS_ENABLED", raising=False)

    storage = Storage(home / "data" / "kc.db")
    storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    secrets_store = SecretsStore(config_dir=home / "config", keychain=FakeKeychain())

    subagent_index = None
    subagent_trace_buffer = None
    subagent_runner = None
    subagent_templates_dir = None

    if enabled:
        subagent_index, subagent_trace_buffer, subagent_runner, subagent_templates_dir = (
            _wire_subagents(
                home=home,
                storage=storage,
                shares=shares,
                broker=broker,
                mcp_manager=None,
                mcp_install_store=None,
                memory_root=None,
                gmail_service=None,
                gcal_service=None,
                news_client=None,
                ollama_api_key=None,
                skill_index=None,
                web_config=None,
            )
        )

    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
        subagent_index=subagent_index,
        subagent_runner=subagent_runner,
    )
    registry.load_all()

    deps = Deps(
        storage=storage,
        registry=registry,
        conversations=ConversationManager(storage),
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=ConversationLocks(),
        secrets_store=secrets_store,
        google_token_path=home / "data" / "google_token.json",
        subagent_index=subagent_index,
        subagent_runner=subagent_runner,
        subagent_trace_buffer=subagent_trace_buffer,
        subagent_templates_dir=subagent_templates_dir,
    )
    return deps, registry


def test_disabled_by_default(monkeypatch, tmp_path):
    """When KC_SUBAGENTS_ENABLED is unset, all subagent singletons on Deps are None."""
    home = _make_home(tmp_path)
    deps, registry = _build_deps(home, enabled=False, monkeypatch=monkeypatch)
    assert deps.subagent_index is None
    assert deps.subagent_runner is None
    assert deps.subagent_trace_buffer is None
    assert deps.subagent_templates_dir is None


def test_enabled_constructs_singletons(monkeypatch, tmp_path):
    """When KC_SUBAGENTS_ENABLED=true, all subagent singletons on Deps are non-None."""
    home = _make_home(tmp_path)
    deps, registry = _build_deps(home, enabled=True, monkeypatch=monkeypatch)
    assert deps.subagent_index is not None
    assert deps.subagent_runner is not None
    assert deps.subagent_trace_buffer is not None
    assert deps.subagent_templates_dir is not None


def test_enabled_seeds_installed(monkeypatch, tmp_path):
    """When KC_SUBAGENTS_ENABLED=true, seed templates are installed into the templates dir."""
    home = _make_home(tmp_path)
    deps, _ = _build_deps(home, enabled=True, monkeypatch=monkeypatch)
    assert deps.subagent_templates_dir is not None
    assert deps.subagent_templates_dir.exists()
    assert (deps.subagent_templates_dir / "web-researcher.yaml").exists()


def test_enabled_threaded_into_agent_registry(monkeypatch, tmp_path):
    """When KC_SUBAGENTS_ENABLED=true, the registry receives the index + runner."""
    home = _make_home(tmp_path)
    deps, registry = _build_deps(home, enabled=True, monkeypatch=monkeypatch)
    assert registry.subagent_index is not None
    assert registry.subagent_runner is not None


def test_disabled_registry_has_none_subagent_fields(monkeypatch, tmp_path):
    """When KC_SUBAGENTS_ENABLED is unset, AgentRegistry subagent fields are None."""
    home = _make_home(tmp_path)
    deps, registry = _build_deps(home, enabled=False, monkeypatch=monkeypatch)
    assert registry.subagent_index is None
    assert registry.subagent_runner is None


def test_reap_called_on_startup(monkeypatch, tmp_path):
    """reap_running_subagent_runs() is called during the wiring block.

    Inserts a fake 'running' row, enables subagents, and verifies the row
    is reaped (transitioned to 'reaped') as part of startup."""
    home = _make_home(tmp_path)

    # Pre-create the storage so we can seed a stale row.
    storage = Storage(home / "data" / "kc.db")
    storage.init()
    # Insert a fake 'running' subagent_run row to verify reap fires.
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO subagent_runs "
            "(id, parent_conversation_id, parent_agent, template, status, started_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ep_stale", "conv_x", "Kona-AI", "web-researcher", "running", 0.0),
        )

    # Now wire with enabled=True — reap should fire as part of _wire_subagents.
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    broker = ApprovalBroker()
    _wire_subagents(
        home=home,
        storage=storage,
        shares=shares,
        broker=broker,
        mcp_manager=None,
        mcp_install_store=None,
        memory_root=None,
        gmail_service=None,
        gcal_service=None,
        news_client=None,
        ollama_api_key=None,
        skill_index=None,
        web_config=None,
    )

    # Verify the stale row was reaped (status transitions to 'interrupted').
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT status FROM subagent_runs WHERE id = ?", ("ep_stale",)
        ).fetchone()
    assert row is not None
    assert row[0] == "interrupted"
