from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing one attachment."""

    markdown: str
    extra_meta: dict[str, Any] = field(default_factory=dict)


class Parser(Protocol):
    """One method, called once at upload time."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult: ...


# Mime-prefix → Parser. Populated by per-type modules at import time.
REGISTRY: dict[str, Parser] = {}
