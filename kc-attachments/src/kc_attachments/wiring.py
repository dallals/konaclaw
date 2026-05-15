from __future__ import annotations

from kc_core.tools import Tool, ToolRegistry

from .store import AttachmentStore
from .tools import build_read_attachment_tool, build_list_attachments_tool


_READ_PARAMS = {
    "type": "object",
    "properties": {
        "attachment_id": {
            "type": "string",
            "description": (
                "Attachment id from the [attached: ...] line in the user message "
                "or from list_attachments. REQUIRED, format 'att_<12hex>'."
            ),
        },
        "page_range": {
            "type": "string",
            "description": (
                "Optional. PDFs only. Examples: '1-3', '5', '10-'. Without this, "
                "the response is the whole document clamped to 32 KB."
            ),
        },
    },
    "required": ["attachment_id"],
}

_READ_DESCRIPTION = (
    "Read an attached file by id. Returns markdown for text-y types (txt, PDF, "
    "Word, Excel); for images, returns the image content directly when the "
    "backend supports vision, otherwise OCR markdown. Tier=SAFE, no approval."
)

_LIST_PARAMS = {"type": "object", "properties": {}}

_LIST_DESCRIPTION = (
    "List attachments in the current conversation. Returns id, filename, mime, "
    "size_bytes, page_count, parse_status. Tier=SAFE."
)


def attach_attachments_to_agent(
    *,
    registry: ToolRegistry,
    store: AttachmentStore,
    conversation_id: str,
    vision_for_active_model: bool,
) -> None:
    """Wires read_attachment + list_attachments onto an agent's tool registry."""
    registry.register(Tool(
        name="read_attachment",
        description=_READ_DESCRIPTION,
        parameters=_READ_PARAMS,
        impl=build_read_attachment_tool(
            store=store,
            conversation_id=conversation_id,
            vision_for_active_model=vision_for_active_model,
        ),
    ))
    registry.register(Tool(
        name="list_attachments",
        description=_LIST_DESCRIPTION,
        parameters=_LIST_PARAMS,
        impl=build_list_attachments_tool(
            store=store,
            conversation_id=conversation_id,
        ),
    ))
