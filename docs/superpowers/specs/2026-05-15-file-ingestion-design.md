# File Ingestion Design

**Goal:** Let users drag-drop files (txt, PDF, Word, Excel, images) into the KonaClaw dashboard chat and have Kona-AI read them on demand via a tool.

**Status:** Brainstorm complete 2026-05-15. Awaiting plan.

**Scope:** Dashboard chat only. Telegram/iMessage attachment ingestion is a deferred follow-up phase that will reuse the same parser layer.

---

## Architecture

New package **`kc-attachments`** (sibling of `kc-web`, `kc-skills`, `kc-memory`) owning:
- Parsers behind a small `Parser` Protocol
- `AttachmentStore` for disk persistence + metadata
- `read_attachment` and `list_attachments` tools
- `attach_attachments_to_agent` wiring helper

Supervisor changes are limited to:
- A new HTTP route (`POST /attachments/upload`, `DELETE /attachments/<id>`)
- Sniffing + injection of the inline `[attached: ...]` line into the user message
- Eager-inline image content when the active model is vision-capable
- Tool registration during `assemble_agent`

Dashboard changes are limited to:
- Drop-zone overlay over the chat input
- Paperclip button
- Chip row above the input with per-attachment status
- Cmd+V paste-image handler

---

## Storage layout

```
~/KonaClaw/attachments/
├── index.sqlite                       # metadata index, keyed by conversation_id
└── <conversation_id>/
    └── <attachment_id>/
        ├── original.<ext>             # raw upload, untouched
        ├── parsed.md                  # extracted markdown (text-y formats; OCR for images)
        └── meta.json                  # filename, mime, size_bytes, parsed_at,
                                       #   page_count?, sheet_names?, parse_error?,
                                       #   truncated_at? (when parsed.md hit the cap)
```

**`index.sqlite` table:**

```sql
CREATE TABLE attachments (
    id              TEXT PRIMARY KEY,         -- 'att_' + 12 hex chars
    conversation_id TEXT NOT NULL,
    filename        TEXT NOT NULL,
    mime            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    parse_status    TEXT NOT NULL,            -- 'ok' | 'error'
    parse_error     TEXT,
    page_count      INTEGER,
    parsed_at       TEXT NOT NULL             -- ISO 8601
);
CREATE INDEX idx_attachments_conv ON attachments(conversation_id);
```

Per-conversation directories scope recall (Kona in conversation A cannot peek at attachments from conversation B) and make "delete conversation" a clean `rm -rf` of one directory.

---

## Parser pipeline

```python
class Parser(Protocol):
    def parse(self, source: Path, meta: dict) -> ParseResult: ...

@dataclass(frozen=True)
class ParseResult:
    markdown: str
    extra_meta: dict[str, Any]   # page_count, sheet_names, ocr_confidence, etc.
```

**Per-file-type implementations:**

| Type | Library | Strategy |
|---|---|---|
| `.txt`, `.md`, `.log` | stdlib | Read as UTF-8; fall back to latin-1 on `UnicodeDecodeError`. Cap raw capture at 1 MB. |
| `.pdf` | `pypdf` | Extract text per page, prefix each page with `## Page N`. Track `page_count`. |
| `.docx` | `python-docx` | Walk paragraphs and tables; emit markdown headings, lists, and pipe-tables. |
| `.xlsx` | `openpyxl` | One markdown table per sheet, separated by `## <sheet name>`. Cells with formulas show evaluated value. Empty trailing rows trimmed. |
| Image (`.png`, `.jpg`, `.jpeg`, `.heic`, `.webp`) | Pillow (validate, downscale if >4096 px) + `pytesseract` (OCR fallback) | At parse time: validate, downscale if needed, run OCR into `parsed.md`. At read time: vision routing decides image-bytes vs OCR markdown. |

**Why parse-on-upload:** parsing once and caching keeps `read_attachment` fast, lets the dashboard surface parse errors immediately (chip shows ⚠), and means the supervisor's hot path never blocks on parsing.

**Failure handling:** parser exception → `meta.parse_error` set, `parsed.md` is empty, `parse_status='error'`. Upload still succeeds. The chip surfaces the warning; Kona's `read_attachment` returns a clear error if called.

**Macro safety:** `.docx`/`.xlsx` are extracted with content-only readers — macros are never executed. `.xlsm`, `.xls`, `.pptx`, `.docm` are rejected at the sniff step (deferred for a future phase).

---

## Vision capability detection

At supervisor boot:

1. For each Ollama model the supervisor will use, call Ollama's `/api/show`.
2. Read the response's `capabilities` array (or model metadata where applicable).
3. Cache a `{model_id: supports_vision: bool}` dict on the supervisor singleton.

`read_attachment` and the eager-inline path look up the **active model for the current agent** (Kona reads `KC_DEFAULT_MODEL`; other agents may have agent-specific overrides set in `assemble_agent`). If vision is supported → image bytes flow to the model; otherwise → OCR markdown from `parsed.md`.

Per the locked-in preference, there is **no separate `KC_VISION_MODEL` env var**. The vision strategy uses whatever model Kona is currently configured with. If the active model lacks vision, every image attachment automatically falls back to OCR markdown — the user gets a degraded but useful answer.

---

## Tool surface

Both tools register on Kona (and any agent calling `attach_attachments_to_agent`).

### `read_attachment` (tier=SAFE)

```python
read_attachment(
    attachment_id: str,
    page_range: str | None = None,   # PDFs only: "1-3", "5", "10-"
) -> str | dict
```

**Returns:**
- For text-y attachments: markdown string (clamped to 32 KB; over-cap responses include `[truncated at byte N — call again with page_range to paginate]`). PDFs honor `page_range`; for non-PDF text-y types that exceed 32 KB there is no native pagination handle, so the truncation marker is terminal and Kona must work from the visible slice. This is an acknowledged v1 limit — a follow-up phase can add byte-offset pagination if it becomes a pain point.
- For image attachments: a sentinel dict `{"type": "image", "path": "<abs path>", "ocr_markdown": "<fallback>"}`. The agent runtime (in `kc-core` tool-result handling) detects this sentinel before passing the tool result back to the model: if the active model supports vision, it substitutes the image content as a multipart user message turn; otherwise it substitutes the `ocr_markdown` string. The model itself never sees the sentinel.

**Errors:**
- `not_found` — id unknown or path missing on disk.
- `parse_error` — extraction failed; `meta.parse_error` returned in the message.
- `out_of_scope` — attachment belongs to a different conversation.
- `too_large` — raw image exceeds the per-attachment cap on a non-vision model.

### `list_attachments` (tier=SAFE)

```python
list_attachments() -> list[dict]
# [{id, filename, mime, size_bytes, page_count?, parsed_at, parse_status, parse_error?}]
```

Scoped to the current conversation. Lets Kona reference older uploads without the dashboard re-injecting chip text on every turn.

---

## Message-prefix injection

When the user message has attachments, the supervisor prepends one line per attachment **before the model sees it**:

```
[attached: weather.pdf (12 pages, 245 KB, id=att_a1b2c3)]
[attached: chart.png (image, id=att_d4e5f6)]

<user's typed message follows>
```

Kona sees these chips and decides whether to call `read_attachment`. The id is the handle for tool invocation.

For images on vision-capable models, the supervisor also injects the image content directly into the user turn alongside the text (eager inline). The chip line stays — it gives Kona a stable id for re-reads — but the model can "see" the image without calling the tool. Override: `KC_ATTACH_IMAGE_MODE=lazy` forces tool-only for images too.

---

## Dashboard UX

- **Drop overlay:** dragging files anywhere over the chat shows a translucent "Drop to attach" zone.
- **Paperclip button** in the input row for click-to-browse.
- **Cmd+V** with an image in the clipboard creates a chip automatically.
- **Chip row** above the input: filename + size + type icon + ✕ to remove. Shows a spinner while uploading/parsing, then locks to ✓ ready or ⚠ error.
- **Send disabled** until every chip is ready (or removed).
- **Past-message chips** are rendered inline in the message bubble (filename + type icon, clickable to download the original).

---

## Upload endpoint

`POST /attachments/upload?conversation_id=<id>` — multipart form, one file per request. `conversation_id` is the existing supervisor conversation handle (already minted by the dashboard when a chat thread is created — no new concept introduced).

Supervisor:
1. Verifies the conversation_id exists.
2. Sniffs mime by magic bytes (`python-magic` or `filetype` library). Browser-supplied type is advisory.
3. Rejects unknown / disallowed types and oversized files (default 25 MB; configurable).
4. Allocates `att_<12hex>`, creates the directory.
5. Writes `original.<ext>`.
6. Runs the parser pipeline → `parsed.md` + `meta.json`.
7. Inserts the index row.
8. Returns `{attachment_id, filename, mime, size_bytes, parse_status, parse_error?, snippet?}`.

`snippet` is the first ~200 chars of `parsed.md` — purely cosmetic, shown in the chip as a hover tooltip.

`DELETE /attachments/<id>` — deletes the row + the on-disk directory. Only the conversation owner (no cross-conversation deletes).

---

## Limits & lifecycle

| Knob | Default | Env override |
|---|---|---|
| Max bytes per file | 25 MB | `KC_ATTACH_MAX_BYTES` |
| Max files per message | 10 | `KC_ATTACH_MAX_FILES` |
| Max bytes per conversation | 500 MB | `KC_ATTACH_MAX_PER_CONV` |
| `parsed.md` cap | 1 MB | `KC_ATTACH_PARSED_CAP` |
| `read_attachment` response cap | 32 KB | `KC_ATTACH_TOOL_RESP_CAP` |
| Image dimension cap | 4096×4096 | `KC_ATTACH_IMAGE_MAX_DIM` |
| Image mode | `eager` | `KC_ATTACH_IMAGE_MODE` (`eager` or `lazy`) |
| Retention | 90 days | `KC_ATTACH_RETENTION_DAYS` |

**Lifecycle:**
- Attachments live for the conversation's lifetime.
- Deleting a conversation in the dashboard `rm -rf`'s the attachment directory.
- Background GC sweep (existing supervisor maintenance task hook) evicts attachments from conversations older than the retention threshold; the conversation row is preserved with an `(attachments expired)` marker.
- Manual per-chip ✕ on past messages → `DELETE /attachments/<id>`.

---

## Scope discipline

**In v1:**
- Dashboard upload UI
- Supervisor parse pipeline (5 file types)
- `read_attachment` + `list_attachments` tools
- Eager inline for images on vision-capable models, OCR fallback otherwise
- Sqlite index
- Env-driven limits, retention, GC

**Out — deferred to follow-up phases:**
- Telegram/iMessage attachment ingestion (separate phase using this parser layer)
- Cross-conversation attachment library / search
- Editing or annotating uploaded files
- Kona generating files back to the user (PDF/Word/Excel output)
- Macro-enabled formats (`.xlsm`, `.docm`, `.pptx`)

---

## Testing strategy

**`kc-attachments` unit tests (~25):**
- One parser test per file type using a tiny fixture checked into the repo (1-page PDF, 2-paragraph Word, 2-sheet Excel, small PNG, 100-byte txt).
- `AttachmentStore` round-trip: save → list → read → delete.
- Meta error states: parser raises → `parse_status=error`, `parse_error` populated.
- Limits: oversize file rejected, oversize parsed text truncated with marker.
- OCR fallback path (mock pytesseract; verify markdown shape).
- Vision-capability cache: hit / miss / unknown-model paths.

**`kc-supervisor` integration tests:**
- Upload endpoint happy + error paths (oversize, bad mime, unknown conversation).
- `read_attachment` tool: happy path per type, `out_of_scope` cross-conversation guard, pagination.
- Image-mode toggle (`eager` vs `lazy`).

**`kc-dashboard` component tests (Vitest):**
- Drop-zone enter/leave/drop.
- Chip state machine (uploading → ready / error / removed).
- Send-button disabled while uploading.
- Paste-from-clipboard creates a chip.

**SMOKE gates (manual, ~6):** drop each of the 5 file types in a Kona chat and ask a question that requires reading it; verify chip rendering + audit log entry for `read_attachment` + Kona's response references the content. Gate 6 covers the OCR-fallback path (force a non-vision model).

---

## Risks & open questions

- **Vision capability detection across Ollama versions:** Ollama's `/api/show` capability shape has changed between versions. We need a defensive parser + a clear log line when capability is unknown (treat as "no vision" — safe default).
- **`python-magic` vs `filetype`:** `python-magic` is more thorough but needs a libmagic system dep. `filetype` is pure Python but fingerprints fewer formats. Defaulting to `filetype` for portability; revisit if false-negatives appear.
- **Image downscaling threshold:** 4096×4096 is a heuristic; if vision models start refusing big images we may need to tighten. Monitorable via the parse-error rate.
