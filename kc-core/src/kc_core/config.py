from __future__ import annotations
import warnings
from dataclasses import dataclass
from pathlib import Path
import yaml

_KC_CORE_FIELDS = {"name", "model", "system_prompt"}
_LATER_SUBPROJECT_FIELDS = {"shares", "tools", "permission_overrides", "spawn_policy"}


@dataclass
class AgentConfig:
    name: str
    model: str
    system_prompt: str


def load_agent_config(path: Path | str, default_model: str | None = None) -> AgentConfig:
    p = Path(path)
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top-level must be a mapping")

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValueError(f"{p}: 'name' is required and must be a string")

    system_prompt = data.get("system_prompt")
    if not isinstance(system_prompt, str):
        raise ValueError(f"{p}: 'system_prompt' is required and must be a string")

    model = data.get("model") or default_model
    if not isinstance(model, str):
        raise ValueError(f"{p}: 'model' is required (none given and no default_model)")

    unknown = set(data.keys()) - _KC_CORE_FIELDS - _LATER_SUBPROJECT_FIELDS
    for k in sorted(unknown):
        warnings.warn(f"{p}: unknown config key {k!r} (ignored)", stacklevel=2)

    return AgentConfig(name=name, model=model, system_prompt=system_prompt)
