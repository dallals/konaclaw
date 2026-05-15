# File Ingestion — SMOKE Gates

**Spec:** `2026-05-15-file-ingestion-design.md`
**Plan:** `2026-05-15-file-ingestion.md`
**Owner:** Sammy

Restart KonaClawDashboard after the implementation merges. Then walk each gate in a fresh Kona chat conversation.

## Prerequisites

- Install Tesseract for Gate 6 OCR fallback: `brew install tesseract`.
- Have small fixtures of each file type available somewhere on disk to drag in.
- Verify the supervisor came up cleanly after restart: look for `Uvicorn running on http://127.0.0.1:8765` and no `RuntimeError` in the dashboard launcher output.

---

## Gate 1 — Text file (.txt)

**Action:** Drag a small text file into the dashboard chat input. Send the message "What's in the file I attached?"
**Expected:** Chip appears, transitions to ✓ ready. Kona's response references the file's content. Audit log shows one `read_attachment` call with `decision=tier`.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 2 — PDF (.pdf)

**Action:** Drag a multi-page PDF. Ask: "Use read_attachment to summarize page 2 only."
**Expected:** Kona calls `read_attachment` with `page_range="2"`; the response references only page 2 content.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 3 — Word (.docx)

**Action:** Drag a .docx with at least one heading and one table. Ask "What sections are in this document?"
**Expected:** Kona reads the file via `read_attachment` and lists the section headings.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 4 — Excel (.xlsx)

**Action:** Drag a multi-sheet .xlsx. Ask "What sheets are in this workbook?"
**Expected:** Kona's response lists every sheet name from `list_attachments` or `read_attachment` output.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 5 — Image with vision

**Action:** Drag a screenshot containing visible text. Ask "What does the image show?"
**Expected:** On a vision-capable model (qwen3.6:35b, gemma4:31b if vision-capable, gemma3:4b), Kona's response describes the visual content. Eager-inline means no `read_attachment` call needed for current-turn images — the image is in the user turn directly.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 6 — OCR fallback (CONDITIONAL — only if Tesseract is installed)

**Action:** Switch Kona-AI to a non-vision model (or force `vision_for_active_model=False`). Repeat Gate 5 with the same screenshot.
**Expected:** Response references the OCR-extracted text (less accurate than vision but readable). Audit log may show `read_attachment` returning `type=text` with the OCR markdown.
**Status:** [ ] PASS / [ ] FAIL / [ ] SKIPPED (no Tesseract)
**Notes:**

## Gate 7 — Drop overlay UX

**Action:** Drag a file over the chat without dropping it.
**Expected:** Translucent "Drop to attach" overlay appears. Releasing the drag outside the chat area cancels (no upload). Dropping triggers upload.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 8 — Paperclip + paste

**Action:** Click the paperclip button to browse, select a file, send the message. Then copy an image to clipboard, paste into the chat (Cmd+V), send.
**Expected:** Both paths produce chips that transition to ✓ ready and reach Kona.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

---

## Closeout

- Date: ___
- Final commit: ___
- All gates PASS / N PASS, M SKIPPED: ___
- Defects observed: ___
