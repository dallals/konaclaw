from __future__ import annotations
import json
import re
from typing import Any, Awaitable, Callable

from .store import AttachmentStore, AttachmentNotFound


_TOOL_RESP_CAP_BYTES = 32 * 1024


_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)


def _truncate(markdown: str) -> str:
    if len(markdown.encode("utf-8")) <= _TOOL_RESP_CAP_BYTES:
        return markdown
    cut = markdown.encode("utf-8")[:_TOOL_RESP_CAP_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n\n[truncated at 32 KB — call again with page_range to paginate]"


def _slice_by_page_range(markdown: str, page_range: str) -> str:
    """Returns the slice of `markdown` containing the requested page numbers.

    Page boundaries are detected by `## Page N` headings (emitted by PdfParser).
    Supports "1-3", "5", "10-" forms.
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


def build_read_attachment_tool(
    *,
    store: AttachmentStore,
    conversation_id: str,
    vision_for_active_model: bool,
) -> Callable[[dict[str, Any]], Awaitable[str]]:
    """Returns the `read_attachment` tool implementation, scoped to one conversation."""

    async def impl(args: dict[str, Any]) -> str:
        att_id = args.get("attachment_id", "")
        page_range = args.get("page_range")
        try:
            rec = store.get(att_id)
        except AttachmentNotFound:
            return json.dumps({"error": "not_found", "attachment_id": att_id})
        if rec.conversation_id != conversation_id:
            return json.dumps({"error": "out_of_scope", "attachment_id": att_id})
        if rec.parse_status != "ok":
            return json.dumps({
                "error": "parse_error",
                "message": rec.parse_error or "unknown parse error",
                "attachment_id": att_id,
            })

        # Image branch — sentinel for vision-capable, OCR markdown otherwise.
        if rec.mime.startswith("image/"):
            ocr_md = store.read_parsed(att_id) or ""
            if vision_for_active_model:
                original = store.original_path(att_id)
                return json.dumps({
                    "type": "image",
                    "path": str(original),
                    "ocr_markdown": ocr_md,
                })
            return json.dumps({"type": "text", "markdown": _truncate(ocr_md)})

        # Text-y branch (unchanged).
        markdown = store.read_parsed(att_id)
        if page_range and rec.mime == "application/pdf":
            markdown = _slice_by_page_range(markdown, page_range)
        markdown = _truncate(markdown)
        return json.dumps({"type": "text", "markdown": markdown})

    return impl


def build_list_attachments_tool(
    *,
    store: AttachmentStore,
    conversation_id: str,
) -> Callable[[dict[str, Any]], Awaitable[str]]:
    """Returns the `list_attachments` tool implementation, scoped to one conversation."""

    async def impl(_args: dict[str, Any]) -> str:
        records = store.list_for_conversation(conversation_id)
        out = [
            {
                "id": r.id,
                "filename": r.filename,
                "mime": r.mime,
                "size_bytes": r.size_bytes,
                "page_count": r.page_count,
                "parsed_at": r.parsed_at,
                "parse_status": r.parse_status,
                "parse_error": r.parse_error,
            }
            for r in records
        ]
        return json.dumps(out)

    return impl
