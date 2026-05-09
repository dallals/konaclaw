from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
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

    `base_system_prompt` holds the YAML's system_prompt verbatim. The CoreAgent's
    `system_prompt` may have a memory prefix prepended; ws_routes refreshes it
    per-turn from `memory_reader.format_prefix(name)` so updates from prior turns
    are visible. When memory_reader is None (no memory wired), the CoreAgent's
    system_prompt equals base_system_prompt.
    """
    name: str
    system_prompt: str
    ollama_client: OllamaClient
    registry: AuditingToolRegistry
    engine: PermissionEngine
    journals: dict[str, Journal]
    undo_log: RecordingUndoLog
    core_agent: CoreAgent
    base_system_prompt: str = ""
    memory_reader: Optional[Any] = None
    memory_journal: Optional[Journal] = None


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
    mcp_manager: Optional[Any] = None,
    mcp_install_store: Optional[Any] = None,
    on_mcp_install: Optional[Callable[[], None]] = None,
    memory_root: Optional[Path] = None,
    gmail_service: Optional[Any] = None,
    gcal_service: Optional[Any] = None,
    news_client: Optional[Any] = None,
    ollama_api_key: Optional[str] = None,
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

    # MCP integration — only when the supervisor has wired up an MCPManager.
    # Lazy-imports kc_mcp so kc-supervisor doesn't get a hard dep on it; the
    # presence of mcp_manager is the signal that kc_mcp is importable.
    if mcp_manager is not None:
        for mcp_tool in mcp_manager.all_tools():
            registry.register(mcp_tool)
            # Zapier MCP tools are user-authorized server-side at mcp.zapier.com
            # (per-app OAuth + Zapier's own approval gate), so re-prompting in
            # KonaClaw is redundant. Treat them as MUTATING (audited, no
            # approval popup) rather than DESTRUCTIVE.
            if mcp_tool.name.startswith("mcp.zapier."):
                tier_map[mcp_tool.name] = Tier.MUTATING
            else:
                tier_map[mcp_tool.name] = Tier.DESTRUCTIVE
        # Zapier meta-tool — only when the MCP manager has a "zapier" server
        # registered. The Zapier MCP tools themselves are already registered
        # above as DESTRUCTIVE via the manager.all_tools() loop. The meta-tool
        # is SAFE because it only searches existing tool names/descriptions.
        if "zapier" in mcp_manager.names():
            try:
                from kc_zapier.meta_tool import build_find_or_install_zap_tool
                zap_tool = build_find_or_install_zap_tool(manager=mcp_manager)
                registry.register(zap_tool)
                tier_map[zap_tool.name] = Tier.SAFE
            except ImportError:
                pass
        if mcp_install_store is not None:
            from kc_mcp.meta_tool import build_install_mcp_server_tool

            def _on_install_complete() -> None:
                # Reload the registry so every agent picks up the new MCP
                # tools on its next turn (the current agent's tool registry
                # is a snapshot from this assemble_agent call).
                if on_mcp_install is not None:
                    on_mcp_install()

            class _CallbackHandleFactory:
                """Wraps the real MCPServerHandle so we can fire on_install_complete
                AFTER MCPManager.register_handle returns successfully."""
                def __init__(self):
                    from kc_mcp.handle import MCPServerHandle as RealHandle
                    self._real = RealHandle

                def __call__(self, **kw):
                    return self._real(**kw)

            install_tool = build_install_mcp_server_tool(
                manager=mcp_manager,
                store=mcp_install_store,
                broker=broker,
                agent_name=cfg.name,
                handle_factory=_CallbackHandleFactory(),
            )
            # Wrap impl so we trigger the registry reload after a successful install.
            original_impl = install_tool.impl

            async def install_with_reload(**kwargs):
                result = await original_impl(**kwargs)
                if isinstance(result, str) and result.lower().startswith("installed"):
                    _on_install_complete()
                return result

            from kc_core.tools import Tool
            install_tool = Tool(
                name=install_tool.name,
                description=install_tool.description,
                parameters=install_tool.parameters,
                impl=install_with_reload,
            )
            registry.register(install_tool)
            tier_map[install_tool.name] = Tier.DESTRUCTIVE

    # Memory integration — only when memory_root is supplied. Lazy-imports
    # kc_memory so kc-supervisor doesn't take a hard dep on it; the presence
    # of memory_root is the signal that kc_memory is importable.
    memory_reader: Optional[Any] = None
    memory_journal: Optional[Journal] = None
    if memory_root is not None:
        from kc_memory.store import MemoryStore as _MemStore
        from kc_memory.reader import MemoryReader as _MemReader
        from kc_memory.tools import build_memory_tools as _build_mem_tools
        from kc_memory.tools import DEFAULT_MEMORY_TOOL_TIERS as _MEM_TIERS

        mem_store = _MemStore(memory_root)
        mem_store.init()
        memory_journal = Journal(memory_root)
        memory_journal.init()
        memory_reader = _MemReader(store=mem_store)
        for mt in _build_mem_tools(
            store=mem_store,
            journal=memory_journal,
            undo_log=undo_log,
            agent_name=cfg.name,
        ).values():
            registry.register(mt)
        tier_map.update(_MEM_TIERS)

    # Google tool-providers (Gmail + Calendar) — only when the supervisor has
    # built credentialed service objects. Lazy-imports kc_connectors so
    # kc-supervisor doesn't take a hard dep; the presence of a service object
    # is the signal that kc_connectors is importable AND credentials exist.
    if gmail_service is not None or gcal_service is not None:
        from kc_connectors.gmail_adapter import build_gmail_tools
        from kc_connectors.gcal_adapter import build_gcal_tools

        google_tier_map: dict[str, Tier] = {
            "gmail.search":      Tier.SAFE,
            "gmail.read_thread": Tier.SAFE,
            "gmail.draft":       Tier.MUTATING,
            "gmail.send":        Tier.DESTRUCTIVE,
            "gcal.list_events":  Tier.SAFE,
            "gcal.create_event": Tier.DESTRUCTIVE,
            "gcal.update_event": Tier.DESTRUCTIVE,
            "gcal.delete_event": Tier.DESTRUCTIVE,
        }

        if gmail_service is not None:
            for tool in build_gmail_tools(service=gmail_service).values():
                registry.register(tool)
                tier_map[tool.name] = google_tier_map[tool.name]
        if gcal_service is not None:
            for tool in build_gcal_tools(service=gcal_service).values():
                registry.register(tool)
                tier_map[tool.name] = google_tier_map[tool.name]

    # News tool-provider — registered only when supervisor.main built a NewsClient
    # from the `newsapi_api_key` secret. Both tools are SAFE (read-only).
    if news_client is not None:
        from kc_connectors.news_adapter import build_news_tools
        for tool in build_news_tools(client=news_client).values():
            registry.register(tool)
            tier_map[tool.name] = Tier.SAFE

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
    ollama_client = OllamaClient(base_url=ollama_url, model=model, api_key=ollama_api_key)

    # If memory is wired, prepend the formatted prefix to the system prompt.
    # ws_routes.py refreshes this per-turn so updates are visible across turns
    # within the same supervisor process.
    effective_system_prompt = cfg.system_prompt
    if memory_reader is not None:
        prefix = memory_reader.format_prefix(agent=cfg.name)
        if prefix:
            effective_system_prompt = prefix + cfg.system_prompt

    core_agent = CoreAgent(
        name=cfg.name,
        client=ollama_client,
        system_prompt=effective_system_prompt,
        tools=registry,
        permission_check=make_audit_aware_callback(engine, agent_name=cfg.name, storage=audit_storage),
    )

    return AssembledAgent(
        name=cfg.name,
        system_prompt=effective_system_prompt,
        ollama_client=ollama_client,
        registry=registry,
        engine=engine,
        journals=journals,
        undo_log=undo_log,
        core_agent=core_agent,
        base_system_prompt=cfg.system_prompt,
        memory_reader=memory_reader,
        memory_journal=memory_journal,
    )
