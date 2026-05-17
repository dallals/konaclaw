from __future__ import annotations

from kc_core.tools import Tool, ToolRegistry

from kc_shared.store import SharedStore
from kc_shared.tools import (
    build_list_shared_files_tool,
    build_read_shared_file_tool,
    build_write_shared_file_tool,
)


_LIST_PARAMS = {
    "type": "object",
    "properties": {
        "folder": {
            "type": "string",
            "enum": ["originals", "kona-edits"],
            "description": (
                "Which side of the shared folder to list. 'originals' is the "
                "user-managed read-only side; 'kona-edits' is your own writable "
                "folder for this conversation."
            ),
        },
    },
}

_LIST_DESCRIPTION = (
    "List files in the shared folder at ~/Desktop/KonaShared/. Returns relative "
    "paths, sizes, and modified-at. Use 'originals' for the user's documents and "
    "'kona-edits' for files you have written this conversation. Tier=SAFE."
)

_READ_PARAMS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Relative path under the shared folder. Examples: 'resume.pdf', "
                "'originals/resume.pdf', 'kona-edits/notes.md'. Bare filenames "
                "resolve under originals/. PDFs/docx/xlsx are parsed inline to "
                "markdown; plain text returns as-is; all responses are clamped "
                "to 32 KB."
            ),
        },
        "page_range": {
            "type": "string",
            "description": (
                "Optional. PDFs only. Examples: '1-3', '5', '10-'. Without this, "
                "the whole document is returned, clamped to 32 KB. Use page_range "
                "to paginate through large PDFs."
            ),
        },
    },
    "required": ["path"],
}

_READ_DESCRIPTION = (
    "Read a file from the shared folder. Handles PDFs (page-by-page text), "
    "Word (.docx), Excel (.xlsx), images (OCR), and plain text. Returns markdown "
    "clamped to 32 KB; use page_range for PDFs longer than that. Tier=SAFE."
)

_WRITE_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": (
                "Basename only — no slashes or '..'. Allowed: letters, digits, "
                "dot, underscore, hyphen, space. Example: 'budget-draft.md'."
            ),
        },
        "content": {
            "type": "string",
            "description": "UTF-8 text content. Replaces the file if it exists.",
        },
    },
    "required": ["filename", "content"],
}

_WRITE_DESCRIPTION = (
    "Write a text file into the current conversation's kona-edits folder. "
    "Folder is auto-created on first write. Use this to save edited versions "
    "of documents or new notes you produced for the user. Tier=MUTATING."
)


def attach_shared_to_agent(
    *,
    registry: ToolRegistry,
    store: SharedStore,
    conversation_id: str,
) -> None:
    """Wire the three shared-folder tools onto an agent's registry."""
    registry.register(Tool(
        name="list_shared_files",
        description=_LIST_DESCRIPTION,
        parameters=_LIST_PARAMS,
        impl=build_list_shared_files_tool(store=store, conversation_id=conversation_id),
    ))
    registry.register(Tool(
        name="read_shared_file",
        description=_READ_DESCRIPTION,
        parameters=_READ_PARAMS,
        impl=build_read_shared_file_tool(store=store, conversation_id=conversation_id),
    ))
    registry.register(Tool(
        name="write_shared_file",
        description=_WRITE_DESCRIPTION,
        parameters=_WRITE_PARAMS,
        impl=build_write_shared_file_tool(store=store, conversation_id=conversation_id),
    ))
