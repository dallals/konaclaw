from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class RoutingTable:
    default_agent: str
    routes: dict[str, dict[str, str]] = field(default_factory=dict)
    # routes[channel][chat_id] -> agent_name

    def route(self, channel: str, chat_id: str) -> str:
        return self.routes.get(channel, {}).get(chat_id, self.default_agent)

    def set_route(self, channel: str, chat_id: str, agent: str) -> None:
        self.routes.setdefault(channel, {})[chat_id] = agent

    def save_to_yaml(self, path: Path) -> None:
        Path(path).write_text(yaml.safe_dump({
            "default_agent": self.default_agent, "routes": self.routes,
        }))

    @classmethod
    def load_from_yaml(cls, path: Path) -> "RoutingTable":
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            default_agent=data.get("default_agent", "KonaClaw"),
            routes=data.get("routes", {}),
        )
