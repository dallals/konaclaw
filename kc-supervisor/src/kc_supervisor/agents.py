from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Any
from kc_core.agent import Agent as CoreAgent
from kc_core.config import load_agent_config


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
    core_agent: Optional[CoreAgent] = None  # built lazily on first use; v0.2 wiring

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
    """Loads and tracks the set of agents the supervisor hosts.

    Each YAML file in ``agents_dir`` becomes one ``AgentRuntime``. The
    ``shares_yaml`` and ``undo_db`` paths are accepted at construction so the
    registry can be wired up to kc-sandbox in v0.2 — today they're stored but
    unused (the registry only handles config + status tracking in v1).
    """

    def __init__(
        self, *,
        agents_dir: Path,
        shares_yaml: Path,
        undo_db: Path,
        default_model: str,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.shares_yaml = Path(shares_yaml)
        self.undo_db = Path(undo_db)
        self.default_model = default_model
        self._by_name: dict[str, AgentRuntime] = {}

    def load_all(self) -> None:
        """Re-read every ``*.yaml`` in agents_dir. Replaces existing entries."""
        self._by_name.clear()
        for p in sorted(self.agents_dir.glob("*.yaml")):
            cfg = load_agent_config(p, default_model=self.default_model)
            self._by_name[cfg.name] = AgentRuntime(
                name=cfg.name,
                model=cfg.model,
                system_prompt=cfg.system_prompt,
                yaml_path=p,
            )

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
        """List of `to_dict()` views, in insertion (sort) order."""
        return [rt.to_dict() for rt in self._by_name.values()]
