from __future__ import annotations
from pathlib import Path
from typing import Any

from . import ParseResult, REGISTRY


_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


class TextParser:
    """Plain-text parser. Tries UTF-8 first, falls back to latin-1."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        raw = source.read_bytes()
        truncated_at: int | None = None
        if len(raw) > _MAX_BYTES:
            raw = raw[:_MAX_BYTES]
            truncated_at = _MAX_BYTES
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        extra: dict[str, Any] = {}
        if truncated_at is not None:
            extra["truncated_at"] = truncated_at
        return ParseResult(markdown=text, extra_meta=extra)


# Register for the three text-y mime prefixes.
REGISTRY["text/plain"] = TextParser()
REGISTRY["text/markdown"] = TextParser()
REGISTRY["text/x-log"] = TextParser()
