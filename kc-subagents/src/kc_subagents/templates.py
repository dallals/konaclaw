from __future__ import annotations
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ALLOWED_KEYS = {
    "name", "description", "version",
    "model", "model_options",
    "system_prompt",
    "tools",
    "mcp_servers", "mcp_action_filter",
    "memory",
    "shares",
    "permission_overrides",
    "timeout_seconds", "max_tool_calls",
}

@dataclass
class SubagentTemplate:
    name: str
    model: str
    system_prompt: str
    description: str = ""
    version: str = ""
    model_options: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    mcp_action_filter: dict[str, list[str]] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    shares: list[str] = field(default_factory=list)
    permission_overrides: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300
    max_tool_calls: int = 50
    source_path: Path | None = None


class TemplateLoadError(ValueError):
    """Raised when a template YAML is malformed."""


def load_template_file(path: Path) -> SubagentTemplate:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise TemplateLoadError(f"yaml parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise TemplateLoadError(f"{path}: top-level must be a mapping")
    unknown = set(raw.keys()) - _ALLOWED_KEYS
    if unknown:
        raise TemplateLoadError(f"{path}: unknown keys {sorted(unknown)}")
    for required in ("name", "model", "system_prompt"):
        if required not in raw:
            raise TemplateLoadError(f"{path}: missing required key {required!r}")
    return SubagentTemplate(
        name=raw["name"],
        model=raw["model"],
        system_prompt=raw["system_prompt"],
        description=raw.get("description", ""),
        version=raw.get("version", ""),
        model_options=raw.get("model_options") or {},
        tools=raw.get("tools") or {},
        mcp_servers=raw.get("mcp_servers") or [],
        mcp_action_filter=raw.get("mcp_action_filter") or {},
        memory=raw.get("memory") or {},
        shares=raw.get("shares") or [],
        permission_overrides=raw.get("permission_overrides") or {},
        timeout_seconds=int(raw.get("timeout_seconds", 300)),
        max_tool_calls=int(raw.get("max_tool_calls", 50)),
        source_path=path,
    )
