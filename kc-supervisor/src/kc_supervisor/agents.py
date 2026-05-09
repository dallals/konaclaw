from __future__ import annotations
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from kc_core.config import load_agent_config
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.assembly import AssembledAgent, assemble_agent
from kc_supervisor.storage import Storage

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    PAUSED = "paused"
    DISABLED = "disabled"
    DEGRADED = "degraded"


@dataclass
class AgentRuntime:
    name: str
    model: str
    system_prompt: str
    yaml_path: Path
    status: AgentStatus = AgentStatus.IDLE
    last_error: Optional[str] = None
    # Set on successful assembly. None means assembly failed (status=DEGRADED).
    assembled: Optional[AssembledAgent] = None

    def set_status(self, s: AgentStatus) -> None:
        self.status = s

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "status": self.status.value,
            "last_error": self.last_error,
        }


class AgentRegistry:
    """Loads agents from YAML files and constructs an AssembledAgent per file.

    On assembly failure (bad YAML, missing share, etc.) the runtime is created with
    status=DEGRADED, last_error set, and assembled=None. The supervisor still boots.
    """

    def __init__(
        self, *,
        agents_dir: Path,
        shares: SharesRegistry,
        audit_storage: Storage,
        broker: ApprovalBroker,
        ollama_url: str,
        default_model: str,
        undo_db_path: Path,
        mcp_manager: "Optional[Any]" = None,
        mcp_install_store: "Optional[Any]" = None,
        memory_root: "Optional[Path]" = None,
        gmail_service: "Optional[Any]" = None,
        gcal_service: "Optional[Any]" = None,
        news_client: "Optional[Any]" = None,
        ollama_api_key: "Optional[str]" = None,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.shares = shares
        self.audit_storage = audit_storage
        self.broker = broker
        self.ollama_url = ollama_url
        self.ollama_api_key = ollama_api_key
        self.default_model = default_model
        self.undo_db_path = Path(undo_db_path)
        self.mcp_manager = mcp_manager
        self.mcp_install_store = mcp_install_store
        self.memory_root = Path(memory_root) if memory_root else None
        self.gmail_service = gmail_service
        self.gcal_service = gcal_service
        self.news_client = news_client
        self._by_name: dict[str, AgentRuntime] = {}

    def load_all(self) -> None:
        """Re-read every *.yaml in agents_dir. Replaces existing entries.

        Per-yaml failures (load_agent_config or assemble_agent raising) result in a
        DEGRADED runtime entry rather than aborting the whole load.

        Atomicity: builds the new dict in a local variable, then reassigns
        ``self._by_name`` in a single attribute write. Python's GIL guarantees the
        reassignment is atomic, so concurrent readers (e.g. ws_chat looking up an
        agent while POST /agents triggers a reload) see either the old or the new
        dict — never a partially-rebuilt one.
        """
        new_by_name: dict[str, AgentRuntime] = {}
        for p in sorted(self.agents_dir.glob("*.yaml")):
            try:
                cfg = load_agent_config(p, default_model=self.default_model)
            except Exception as e:
                stem = p.stem
                logger.warning("load_agent_config failed for %s: %s", p, e)
                new_by_name[stem] = AgentRuntime(
                    name=stem,
                    model="?",
                    system_prompt="",
                    yaml_path=p,
                    status=AgentStatus.DEGRADED,
                    last_error=f"load_agent_config: {e}",
                    assembled=None,
                )
                continue

            try:
                assembled = assemble_agent(
                    cfg=cfg,
                    shares=self.shares,
                    audit_storage=self.audit_storage,
                    broker=self.broker,
                    ollama_url=self.ollama_url,
                    default_model=self.default_model,
                    undo_db_path=self.undo_db_path,
                    resolve_agent=self._resolve_assembled,
                    mcp_manager=self.mcp_manager,
                    mcp_install_store=self.mcp_install_store,
                    on_mcp_install=self.load_all,
                    memory_root=self.memory_root,
                    gmail_service=self.gmail_service,
                    gcal_service=self.gcal_service,
                    news_client=self.news_client,
                    ollama_api_key=self.ollama_api_key,
                )
                new_by_name[cfg.name] = AgentRuntime(
                    name=cfg.name,
                    model=cfg.model,
                    system_prompt=cfg.system_prompt,
                    yaml_path=p,
                    assembled=assembled,
                )
            except Exception as e:
                logger.warning("assemble_agent failed for %s: %s", p, e)
                new_by_name[cfg.name] = AgentRuntime(
                    name=cfg.name,
                    model=cfg.model,
                    system_prompt=cfg.system_prompt,
                    yaml_path=p,
                    status=AgentStatus.DEGRADED,
                    last_error=f"assemble_agent: {e}",
                    assembled=None,
                )

        # Single atomic reassignment — concurrent readers see old or new, never partial.
        self._by_name = new_by_name

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def get(self, name: str) -> AgentRuntime:
        if name not in self._by_name:
            raise KeyError(f"unknown agent: {name}")
        return self._by_name[name]

    def disable(self, name: str) -> None:
        self.get(name).set_status(AgentStatus.DISABLED)

    def enable(self, name: str) -> None:
        self.get(name).set_status(AgentStatus.IDLE)

    def snapshot(self) -> list[dict[str, Any]]:
        return [rt.to_dict() for rt in self._by_name.values()]

    def _resolve_assembled(self, name: str):
        """Late-bound resolver passed to assemble_agent for the delegation tool.

        Returns (assembled_or_none, status) where status is one of:
          "ok"       — agent exists and is assembled
          "unknown"  — no agent with that name
          "degraded" — agent exists but failed to assemble
        Reads `self._by_name` lazily so it sees the latest registry state at
        delegation time (after a hot reload, after a sibling came online, etc.)
        """
        rt = self._by_name.get(name)
        if rt is None:
            return (None, "unknown")
        if rt.assembled is None:
            return (None, "degraded")
        return (rt.assembled, "ok")
