from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from kc_core.agent import Agent as CoreAgent
from kc_core.config import AgentConfig
from kc_core.ollama_client import OllamaClient
from kc_sandbox.shares import SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.tools import build_file_tools, DEFAULT_FILE_TOOL_TIERS
from kc_sandbox.permissions import PermissionEngine, Tier
from kc_supervisor.audit_tools import (
    RecordingUndoLog, AuditingToolRegistry, make_audit_aware_callback,
)
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.delegation import make_delegate_tool, ResolveAgent
from kc_supervisor.storage import Storage


@dataclass
class AssembledAgent:
    """A fully-wired agent: kc-core Agent + kc-sandbox primitives + supervisor audit hooks.

    Held by AgentRuntime. The kc-core Agent's `history` is reset before each turn from
    SQLite via ConversationManager — never carry per-turn state on this dataclass.
    """
    name: str
    system_prompt: str
    ollama_client: OllamaClient
    registry: AuditingToolRegistry
    engine: PermissionEngine
    journals: dict[str, Journal]
    undo_log: RecordingUndoLog
    core_agent: CoreAgent


def assemble_agent(
    *,
    cfg: AgentConfig,
    shares: SharesRegistry,
    audit_storage: Storage,
    broker: ApprovalBroker,
    ollama_url: str,
    default_model: str,
    undo_db_path: Path,
    permission_overrides: Optional[dict[str, Tier]] = None,
    resolve_agent: Optional[ResolveAgent] = None,
    delegation_depth_limit: int = 1,
) -> AssembledAgent:
    """Build an AssembledAgent from an AgentConfig + supervisor singletons.

    Steps:
      1. Per-share Journals (init on disk if missing — idempotent).
      2. RecordingUndoLog (single instance shared across this agent's tools).
      3. AuditingToolRegistry; register kc-sandbox file tools (each is wrapped to audit).
      4. PermissionEngine with overrides for this agent and the broker as approval callback.
      5. kc-core Agent with the audit-aware async permission_check.
    """
    # 1. Journals
    journals: dict[str, Journal] = {name: Journal(shares.get(name).path) for name in shares.names()}
    for j in journals.values():
        j.init()

    # 2. RecordingUndoLog (subclass that captures eids into a contextvar)
    undo_log = RecordingUndoLog(undo_db_path)
    undo_log.init()

    # 3. Tool set: build_file_tools returns raw kc-sandbox file.* tools.
    # We register them through AuditingToolRegistry so each gets wrapped with audit hooks.
    file_tools = build_file_tools(
        shares=shares,
        journals=journals,
        undo_log=undo_log,
        agent_name=cfg.name,
    )
    registry = AuditingToolRegistry(audit_storage=audit_storage, agent_name=cfg.name)
    for tool in file_tools.values():
        registry.register(tool)

    # Delegation tool — only registered when a resolver is supplied (so unit
    # tests that build a single agent in isolation aren't forced to stub one).
    tier_map = dict(DEFAULT_FILE_TOOL_TIERS)
    if resolve_agent is not None:
        delegate_tool = make_delegate_tool(
            resolve_agent,
            parent_name=cfg.name,
            depth_limit=delegation_depth_limit,
        )
        registry.register(delegate_tool)
        tier_map[delegate_tool.name] = Tier.SAFE

    # 4. PermissionEngine. broker.request_approval is async; the engine's
    # check_async detects coroutines via inspect.iscoroutine and awaits them.
    overrides_for_agent = {cfg.name: permission_overrides} if permission_overrides else {}
    engine = PermissionEngine(
        tier_map=tier_map,
        agent_overrides=overrides_for_agent,
        approval_callback=lambda agent, tool, args: broker.request_approval(
            agent=agent, tool=tool, arguments=args,
        ),
    )

    # 5. OllamaClient + kc-core Agent. cfg.model wins over default_model when present.
    model = cfg.model or default_model
    ollama_client = OllamaClient(base_url=ollama_url, model=model)

    core_agent = CoreAgent(
        name=cfg.name,
        client=ollama_client,
        system_prompt=cfg.system_prompt,
        tools=registry,
        permission_check=make_audit_aware_callback(engine, agent_name=cfg.name),
    )

    return AssembledAgent(
        name=cfg.name,
        system_prompt=cfg.system_prompt,
        ollama_client=ollama_client,
        registry=registry,
        engine=engine,
        journals=journals,
        undo_log=undo_log,
        core_agent=core_agent,
    )
