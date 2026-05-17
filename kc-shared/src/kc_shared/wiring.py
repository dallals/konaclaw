from __future__ import annotations

from kc_core.tools import Tool, ToolRegistry

from kc_shared.recall import RecallIndex
from kc_shared.store import SharedStore
from kc_shared.tools import (
    build_index_doc_tool,
    build_list_recalled_tool,
    build_list_shared_files_tool,
    build_read_shared_file_tool,
    build_recall_doc_tool,
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


_RECALL_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": (
                "Filename you previously indexed via index_doc — typically the "
                "same path you'd pass to read_shared_file (e.g. 'CVSept2025.pdf')."
            ),
        },
    },
    "required": ["filename"],
}

_RECALL_DESCRIPTION = (
    "Return your saved notes about a document — summary, key points, when you "
    "last read it. Use this BEFORE read_shared_file when the user asks about a "
    "doc you may have already studied: if found, you can answer from notes "
    "without re-reading. Returns `stale: true` if the file changed since you "
    "took notes — re-read in that case. Tier=SAFE."
)

_INDEX_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "The filename you just read (matches read_shared_file's path).",
        },
        "summary": {
            "type": "string",
            "description": (
                "One-paragraph summary in your own words. Aim for 2-5 sentences "
                "capturing what the doc is and why the user would ask about it."
            ),
        },
        "key_points": {
            "anyOf": [
                {"type": "array", "items": {"type": "string"}},
                {"type": "string"},
            ],
            "description": (
                "Bullet-list of facts worth remembering across chats. "
                "Names, dates, numbers, decisions. Either a JSON array of "
                "strings or a newline-separated string."
            ),
        },
    },
    "required": ["filename", "summary"],
}

_INDEX_DESCRIPTION = (
    "Save notes about a document you just read so future-you can recall them "
    "without re-reading. Call this after read_shared_file when the doc is "
    "something the user is likely to ask about again (resume, CV, recurring "
    "invoice, reference notes). Skip for one-off reads. Tier=SAFE."
)

_LIST_RECALLED_PARAMS = {"type": "object", "properties": {}}

_LIST_RECALLED_DESCRIPTION = (
    "List documents you have notes on, with their summaries and last-read date. "
    "Use this when you're not sure whether you've seen a doc before, or to "
    "browse what's in your long-term doc memory. Tier=SAFE."
)


def attach_shared_to_agent(
    *,
    registry: ToolRegistry,
    store: SharedStore,
    conversation_id: str,
    recall_index: RecallIndex | None = None,
) -> None:
    """Wire the shared-folder tools onto an agent's registry.

    When `recall_index` is supplied, three additional tools are exposed:
    `recall_doc`, `index_doc`, `list_recalled` — Kona's persistent
    doc-notes layer that lets her answer about previously-read docs in
    future chats without re-loading them into context.
    """
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
    if recall_index is not None:
        registry.register(Tool(
            name="recall_doc",
            description=_RECALL_DESCRIPTION,
            parameters=_RECALL_PARAMS,
            impl=build_recall_doc_tool(store=store, index=recall_index),
        ))
        registry.register(Tool(
            name="index_doc",
            description=_INDEX_DESCRIPTION,
            parameters=_INDEX_PARAMS,
            impl=build_index_doc_tool(store=store, index=recall_index),
        ))
        registry.register(Tool(
            name="list_recalled",
            description=_LIST_RECALLED_DESCRIPTION,
            parameters=_LIST_RECALLED_PARAMS,
            impl=build_list_recalled_tool(index=recall_index),
        ))
