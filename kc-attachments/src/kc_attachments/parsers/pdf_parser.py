from __future__ import annotations
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from . import ParseResult, REGISTRY


class PdfParser:
    """Extracts text per page; emits `## Page N` headings."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        reader = PdfReader(str(source))
        parts: list[str] = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            parts.append(f"## Page {i}\n\n{text.strip()}")
        return ParseResult(
            markdown="\n\n".join(parts),
            extra_meta={"page_count": len(reader.pages)},
        )


REGISTRY["application/pdf"] = PdfParser()
