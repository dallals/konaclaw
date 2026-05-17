from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Awaitable, Callable

from kc_shared.store import (
    SharedFileNotFound,
    SharedPathOutOfScope,
    SharedStore,
)


_READ_CAP_BYTES = 32 * 1024
_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= _READ_CAP_BYTES:
        return text
    cut = encoded[:_READ_CAP_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n\n[truncated at 32 KB — call again with page_range to paginate]"


def _slice_by_page_range(markdown: str, page_range: str) -> str:
    """Returns the slice of `markdown` containing the requested page numbers.

    Mirrors kc_attachments.tools._slice_by_page_range — same `## Page N`
    boundary detection. Supports "1-3", "5", "10-" forms.
    """
    if "-" in page_range:
        lo_s, hi_s = page_range.split("-", 1)
        lo = int(lo_s) if lo_s else 1
        hi = int(hi_s) if hi_s else 10**9
    else:
        lo = hi = int(page_range)
    matches = list(_PAGE_HEADING_RE.finditer(markdown))
    if not matches:
        return markdown
    starts = {int(m.group(1)): m.start() for m in matches}
    end_of_doc = len(markdown)
    parts: list[str] = []
    for m in matches:
        page_n = int(m.group(1))
        if not (lo <= page_n <= hi):
            continue
        start = m.start()
        next_start = end_of_doc
        for n2, s2 in starts.items():
            if s2 > start and s2 < next_start:
                next_start = s2
        parts.append(markdown[start:next_start].rstrip())
    return "\n\n".join(parts)


def _parse_to_markdown(abspath: Path) -> tuple[str, str] | None:
    """Run the kc_attachments parser registry against a shared-folder file.

    Returns (markdown, mime) when a parser handled the file, None when the
    file type has no parser. Raises on parse failure.
    """
    from kc_attachments.sniff import (
        UnsupportedTypeError,
        dispatch_parser,
        sniff_mime,
    )
    try:
        mime = sniff_mime(abspath)
    except UnsupportedTypeError:
        return None
    try:
        parser = dispatch_parser(mime)
    except UnsupportedTypeError:
        return None
    result = parser.parse(abspath, {})
    return result.markdown, mime


def build_list_shared_files_tool(
    *, store: SharedStore, conversation_id: str,
) -> Callable[..., Awaitable[str]]:
    async def impl(folder: str = "originals") -> str:
        folder = (folder or "originals").strip()
        if folder not in (SharedStore.ORIGINALS, SharedStore.EDITS):
            return json.dumps({
                "error": "bad_folder",
                "message": f"folder must be 'originals' or 'kona-edits', got {folder!r}",
            })
        if folder == SharedStore.ORIGINALS:
            items = store.list_originals()
        else:
            items = store.list_edits(conversation_id)
        return json.dumps({
            "folder": folder,
            "root": str(store.root),
            "files": [
                {"path": i.relpath, "size_bytes": i.size_bytes, "modified_at": i.modified_at}
                for i in items
            ],
        })
    return impl


def build_read_shared_file_tool(
    *, store: SharedStore, conversation_id: str,
) -> Callable[..., Awaitable[str]]:
    async def impl(path: str = "", page_range: str | None = None) -> str:
        try:
            data, abspath = store.read_file(path, conversation_id=conversation_id)
        except SharedPathOutOfScope as e:
            return json.dumps({"error": "out_of_scope", "message": str(e)})
        except SharedFileNotFound as e:
            return json.dumps({"error": "not_found", "message": str(e)})

        # First, try the kc_attachments parser registry (handles pdf, docx,
        # xlsx, images via OCR). When a parser fires we return markdown,
        # mirroring read_attachment's text payload shape.
        try:
            parsed = _parse_to_markdown(abspath)
        except Exception as e:
            return json.dumps({
                "error": "parse_error",
                "message": f"{type(e).__name__}: {e}",
                "path": str(abspath),
            })
        if parsed is not None:
            markdown, mime = parsed
            if page_range and mime == "application/pdf":
                markdown = _slice_by_page_range(markdown, page_range)
            return json.dumps({
                "type": "text",
                "path": str(abspath),
                "mime": mime,
                "content": _truncate(markdown),
            })

        # No parser matched — fall back to inline utf-8 (txt, md, code, etc.)
        # or surface a sentinel for genuinely opaque binary.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps({
                "type": "binary",
                "path": str(abspath),
                "size_bytes": len(data),
                "hint": "non-utf8 file with no registered parser; cannot inline",
            })
        return json.dumps({"type": "text", "path": str(abspath), "content": _truncate(text)})
    return impl


def build_write_shared_file_tool(
    *, store: SharedStore, conversation_id: str,
) -> Callable[..., Awaitable[str]]:
    async def impl(filename: str = "", content: str = "") -> str:
        try:
            written = store.write_edit(
                conversation_id=conversation_id,
                filename=filename,
                content=(content or "").encode("utf-8"),
            )
        except SharedPathOutOfScope as e:
            return json.dumps({"error": "out_of_scope", "message": str(e)})
        return json.dumps({
            "type": "written",
            "path": str(written),
            "size_bytes": written.stat().st_size,
        })
    return impl
