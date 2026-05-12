from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from kc_subagents.templates import SubagentTemplate

@dataclass
class EphemeralAgentConfig:
    """Subset of fields the supervisor's assemble_agent consumes for an ephemeral run.

    Mapped one-to-one to AgentConfig at the supervisor seam (assembly.py reads
    these fields). Keeping a separate dataclass avoids a cross-package import of
    AgentConfig into kc-subagents and lets us add ephemeral-only fields later.
    """
    name: str
    model: str
    system_prompt: str
    tool_whitelist: list[str] = field(default_factory=list)
    tool_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    mcp_action_filter: dict[str, list[str]] = field(default_factory=dict)
    memory_mode: str = "none"
    memory_scope: str | None = None
    shares: list[str] = field(default_factory=list)
    permission_overrides: dict[str, str] = field(default_factory=dict)
    model_options: dict[str, Any] = field(default_factory=dict)

def template_to_agent_config(
    t: SubagentTemplate, *, instance_id: str, parent_agent: str
) -> EphemeralAgentConfig:
    return EphemeralAgentConfig(
        name=f"{parent_agent}/{instance_id}/{t.name}",
        model=t.model,
        system_prompt=t.system_prompt,
        tool_whitelist=list(t.tools.keys()),
        tool_config=dict(t.tools),
        mcp_servers=list(t.mcp_servers),
        mcp_action_filter=dict(t.mcp_action_filter),
        memory_mode=t.memory.get("mode", "none"),
        memory_scope=t.memory.get("scope"),
        shares=list(t.shares),
        permission_overrides=dict(t.permission_overrides),
        model_options=dict(t.model_options),
    )
