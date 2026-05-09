from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


@dataclass
class MessageEnvelope:
    channel: str            # "telegram", "imessage", "gmail" (inbound is rare here)
    chat_id: str            # connector-scoped chat identifier
    sender_id: str          # connector-scoped sender identifier
    content: str
    attachments: list[Path] = field(default_factory=list)  # paths in the inbox share
    metadata: dict[str, Any] = field(default_factory=dict)


# Inbound callback is async — supervisor.handle_inbound starts an agent turn.
InboundCallback = Callable[["MessageEnvelope"], Awaitable[None]]


class Connector(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def start(self, supervisor) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, chat_id: str, content: str, attachments: Optional[list[Path]] = None) -> None: ...

    capabilities: set[str] = set()  # e.g., {"send", "react", "edit"}


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Connector] = {}

    def register(self, c: Connector) -> None:
        if c.name in self._by_name:
            raise ValueError(f"connector {c.name!r} already registered")
        self._by_name[c.name] = c

    def unregister(self, name: str) -> None:
        """Remove a connector by name. No-op if absent (idempotent), so
        hot-restart paths can safely call this before re-registering."""
        self._by_name.pop(name, None)

    def get(self, name: str) -> Connector:
        return self._by_name[name]

    def all(self) -> list[Connector]:
        return list(self._by_name.values())
