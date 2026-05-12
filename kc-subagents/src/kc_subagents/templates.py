from __future__ import annotations
import re
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

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MEMORY_MODES = {"none", "read-only"}  # read-write rejected per spec §10.3

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

    name = raw["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise TemplateLoadError(
            f"{path}: name must be lowercase-kebab, ≤64 chars (got {name!r})"
        )
    if path.stem != name:
        raise TemplateLoadError(
            f"{path}: name {name!r} does not match filename stem {path.stem!r}"
        )

    mem = raw.get("memory") or {}
    if mem:
        mode = mem.get("mode", "none")
        if mode not in _MEMORY_MODES:
            if mode == "read-write":
                raise TemplateLoadError(
                    f"{path}: memory.read-write is not yet supported; see spec §15"
                )
            raise TemplateLoadError(f"{path}: memory.mode must be one of {_MEMORY_MODES}")
        scope = mem.get("scope")
        if scope is not None:
            if not isinstance(scope, str) or scope.startswith("/") or ".." in Path(scope).parts:
                raise TemplateLoadError(f"{path}: memory.scope must be a relative path under memory_root")

    timeout = int(raw.get("timeout_seconds", 300))
    if not (10 <= timeout <= 1800):
        raise TemplateLoadError(f"{path}: timeout_seconds must be in [10, 1800]")

    max_calls = int(raw.get("max_tool_calls", 50))
    if not (1 <= max_calls <= 500):
        raise TemplateLoadError(f"{path}: max_tool_calls must be in [1, 500]")

    return SubagentTemplate(
        name=name,
        model=raw["model"],
        system_prompt=raw["system_prompt"],
        description=raw.get("description", ""),
        version=raw.get("version", ""),
        model_options=raw.get("model_options") or {},
        tools=raw.get("tools") or {},
        mcp_servers=raw.get("mcp_servers") or [],
        mcp_action_filter=raw.get("mcp_action_filter") or {},
        memory=mem,
        shares=raw.get("shares") or [],
        permission_overrides=raw.get("permission_overrides") or {},
        timeout_seconds=timeout,
        max_tool_calls=max_calls,
        source_path=path,
    )
