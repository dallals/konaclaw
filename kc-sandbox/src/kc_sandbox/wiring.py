from __future__ import annotations
from pathlib import Path
from typing import Any
from kc_core.agent import Agent
from kc_core.config import load_agent_config
from kc_core.tools import ToolRegistry
from kc_sandbox.shares import SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog
from kc_sandbox.tools import build_file_tools, DEFAULT_FILE_TOOL_TIERS
from kc_sandbox.permissions import PermissionEngine, ApprovalCallback


def build_sandboxed_agent(
    *,
    agent_yaml: Path,
    shares_yaml: Path,
    undo_db: Path,
    client: Any,
    approval_callback: ApprovalCallback,
    default_model: str | None = None,
) -> Agent:
    cfg = load_agent_config(agent_yaml, default_model=default_model or "gemma3:4b")
    shares = SharesRegistry.from_yaml(shares_yaml)

    # Init a journal for every share + a single undo log
    journals = {name: Journal(shares.get(name).path) for name in shares.names()}
    for j in journals.values():
        j.init()

    log = UndoLog(undo_db); log.init()

    # Build tool set + register
    file_tools = build_file_tools(shares=shares, journals=journals, undo_log=log, agent_name=cfg.name)
    registry = ToolRegistry()
    for t in file_tools.values():
        registry.register(t)

    # Permission engine with default tier map; agent-config overrides ignored in v1
    engine = PermissionEngine(
        tier_map=dict(DEFAULT_FILE_TOOL_TIERS),
        agent_overrides={},
        approval_callback=approval_callback,
    )

    return Agent(
        name=cfg.name,
        client=client,
        system_prompt=cfg.system_prompt,
        tools=registry,
        permission_check=engine.to_agent_callback(cfg.name),
    )
