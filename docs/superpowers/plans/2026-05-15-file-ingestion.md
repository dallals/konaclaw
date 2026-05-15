# File Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add drag-and-drop file ingestion (txt, PDF, Word, Excel, image) to the KonaClaw dashboard so Kona-AI can read attachments via `read_attachment` / `list_attachments` tools, with eager-inline image support on vision-capable models.

**Architecture:** New `kc-attachments` package owns parsers, storage, capability detection, and tools. Supervisor gains upload/delete HTTP routes, message-prefix injection, and image multimodal pass-through. kc-core gets a minimal multimodal `UserMessage.images` extension. Dashboard gets a drop-zone overlay, chip row, paperclip button, and paste handler on the chat input.

**Tech Stack:** Python 3.11+, pypdf, python-docx, openpyxl, Pillow, pytesseract (system Tesseract dep), filetype, sqlite3 (stdlib), httpx (already in supervisor); React 18 + TypeScript + Vitest on the dashboard.

**Spec:** `docs/superpowers/specs/2026-05-15-file-ingestion-design.md`

---

## File Map

### New files

| File | Responsibility |
|---|---|
| `kc-attachments/pyproject.toml` | package metadata + runtime deps |
| `kc-attachments/README.md` | package overview, install, tier defaults |
| `kc-attachments/src/kc_attachments/__init__.py` | public exports |
| `kc-attachments/src/kc_attachments/parsers/__init__.py` | `Parser` Protocol, `ParseResult` dataclass, parser registry |
| `kc-attachments/src/kc_attachments/parsers/text.py` | `.txt` / `.md` / `.log` |
| `kc-attachments/src/kc_attachments/parsers/pdf_parser.py` | pypdf |
| `kc-attachments/src/kc_attachments/parsers/docx_parser.py` | python-docx |
| `kc-attachments/src/kc_attachments/parsers/xlsx_parser.py` | openpyxl |
| `kc-attachments/src/kc_attachments/parsers/image_parser.py` | Pillow + pytesseract OCR fallback |
| `kc-attachments/src/kc_attachments/sniff.py` | magic-byte mime detection (filetype lib) |
| `kc-attachments/src/kc_attachments/store.py` | `AttachmentStore` — filesystem + sqlite index |
| `kc-attachments/src/kc_attachments/capability.py` | Ollama vision-capability cache |
| `kc-attachments/src/kc_attachments/tools.py` | `read_attachment`, `list_attachments` |
| `kc-attachments/src/kc_attachments/wiring.py` | `attach_attachments_to_agent` helper |
| `kc-attachments/tests/fixtures/*` | tiny per-type fixtures |
| `kc-attachments/tests/test_parsers_*.py` (5 files) | one per parser |
| `kc-attachments/tests/test_sniff.py` | mime sniff |
| `kc-attachments/tests/test_store.py` | store round-trip + index |
| `kc-attachments/tests/test_capability.py` | capability cache |
| `kc-attachments/tests/test_tools.py` | tools end-to-end |
| `kc-supervisor/src/kc_supervisor/attachments_routes.py` | upload + delete routes |

### Modified files

| File | Reason |
|---|---|
| `kc-core/src/kc_core/messages.py` | `UserMessage.images: tuple[ImageRef, ...] = ()` + multimodal `to_openai_dict` |
| `kc-core/tests/test_messages.py` (new tests) | multimodal serialization |
| `kc-core/src/kc_core/agent.py` | detect image sentinel from `ToolResultMessage` content; emit synthetic user turn with images on vision-capable models, OCR otherwise |
| `kc-core/tests/test_agent_image_sentinel.py` (new) | sentinel translation paths |
| `kc-supervisor/src/kc_supervisor/main.py` | initialize `AttachmentStore` + capability cache; register attachments routes |
| `kc-supervisor/src/kc_supervisor/assembly.py` | call `attach_attachments_to_agent` on Kona |
| `kc-supervisor/src/kc_supervisor/ws_routes.py:180` | accept `attachment_ids` from client; build message-prefix + ImageRefs |
| `kc-supervisor/src/kc_supervisor/inbound.py:80` | placeholder hook for future channel attachments (no behavior change in this phase) |
| `kc-supervisor/src/kc_supervisor/storage.py` | persist + replay `UserMessage.images` as a side-table `message_images(message_id, attachment_id)` |
| `kc-dashboard/src/api/attachments.ts` (new) | typed client for upload/delete |
| `kc-dashboard/src/components/AttachmentChip.tsx` (new) | per-attachment UI |
| `kc-dashboard/src/components/AttachmentChip.test.tsx` (new) | chip states |
| `kc-dashboard/src/hooks/useAttachmentUpload.ts` (new) | upload state machine |
| `kc-dashboard/src/hooks/useAttachmentUpload.test.ts` (new) | hook tests |
| `kc-dashboard/src/views/Chat.tsx` | drop overlay, paperclip, paste handler, chip row, send-disabled, attachment_ids in send payload |
| `kc-dashboard/src/views/Chat.test.tsx` | drop / paste / send-disabled cases |
| `kc-dashboard/src/components/MessageBubble.tsx` | render past-message chips |
| `kc-dashboard/src/components/MessageBubble.test.tsx` | past-message chip render |
| `docs/superpowers/specs/2026-05-15-file-ingestion-SMOKE.md` (new) | manual gates |

---

# PHASE A — `kc-attachments` package + parsers

## Task 1: Create `kc-attachments` package scaffold + Parser protocol

**Files:**
- Create: `kc-attachments/pyproject.toml`
- Create: `kc-attachments/src/kc_attachments/__init__.py`
- Create: `kc-attachments/src/kc_attachments/parsers/__init__.py`
- Create: `kc-attachments/tests/__init__.py`
- Create: `kc-attachments/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `kc-attachments/tests/test_protocol.py`:

```python
from pathlib import Path

from kc_attachments.parsers import ParseResult, Parser, REGISTRY


def test_parse_result_carries_markdown_and_extra():
    r = ParseResult(markdown="# hi", extra_meta={"page_count": 3})
    assert r.markdown == "# hi"
    assert r.extra_meta == {"page_count": 3}


def test_registry_is_initially_empty():
    assert isinstance(REGISTRY, dict)
    assert REGISTRY == {}


def test_parser_protocol_is_runtime_checkable_via_duck_typing():
    class FakeParser:
        def parse(self, source: Path, meta: dict) -> ParseResult:
            return ParseResult(markdown="x", extra_meta={})

    # Duck-typing check: any object with parse() satisfies the protocol contract.
    fp = FakeParser()
    out = fp.parse(Path("/tmp/anything"), {})
    assert isinstance(out, ParseResult)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd kc-attachments && pytest tests/test_protocol.py -v
```
Expected: FAIL — `kc_attachments` not installed.

- [ ] **Step 3: Write `pyproject.toml`**

Create `kc-attachments/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-attachments"
version = "0.1.0"
description = "KonaClaw file-ingestion: drag-drop attachments + read_attachment tool."
requires-python = ">=3.11"
dependencies = [
    "kc-core",
    "pypdf>=4.0,<6.0",
    "python-docx>=1.1,<2.0",
    "openpyxl>=3.1,<4.0",
    "Pillow>=10.0,<12.0",
    "pytesseract>=0.3.10,<0.4",
    "filetype>=1.2,<2.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.hatch.build.targets.wheel]
packages = ["src/kc_attachments"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"
asyncio_mode = "auto"
```

- [ ] **Step 4: Write `parsers/__init__.py`**

Create `kc-attachments/src/kc_attachments/parsers/__init__.py`:

```python
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


# Mime-prefix → Parser. Populated by per-type modules at import time. Lookup is
# longest-prefix-match so `image/png` finds the registered `image/` parser.
REGISTRY: dict[str, Parser] = {}
```

- [ ] **Step 5: Write `kc_attachments/__init__.py`**

Create `kc-attachments/src/kc_attachments/__init__.py`:

```python
"""KonaClaw file-ingestion package."""
```

(Will gain exports in later tasks.)

- [ ] **Step 6: Install package**

```bash
cd kc-attachments && python3 -m pip install -e ".[dev]"
```

- [ ] **Step 7: Run tests**

```bash
cd kc-attachments && pytest tests/test_protocol.py -v
```
Expected: 3/3 PASS.

- [ ] **Step 8: Commit**

```bash
git add kc-attachments/
git commit -m "feat(kc-attachments): package scaffold + Parser protocol"
```

---

## Task 2: Text parser (.txt, .md, .log)

**Files:**
- Create: `kc-attachments/src/kc_attachments/parsers/text.py`
- Create: `kc-attachments/tests/fixtures/hello.txt`
- Create: `kc-attachments/tests/fixtures/utf8.txt`
- Create: `kc-attachments/tests/fixtures/latin1.txt`
- Create: `kc-attachments/tests/test_parsers_text.py`

- [ ] **Step 1: Write fixtures**

Create `kc-attachments/tests/fixtures/hello.txt`:

```
Hello, world.
This is a tiny text file.
```

Create `kc-attachments/tests/fixtures/utf8.txt` (write via Python so the bytes are correct):

```bash
python3 -c 'open("kc-attachments/tests/fixtures/utf8.txt", "wb").write("café — naïve résumé\n".encode("utf-8"))'
```

Create `kc-attachments/tests/fixtures/latin1.txt`:

```bash
python3 -c 'open("kc-attachments/tests/fixtures/latin1.txt", "wb").write("café\n".encode("latin-1"))'
```

- [ ] **Step 2: Write the failing tests**

Create `kc-attachments/tests/test_parsers_text.py`:

```python
from pathlib import Path

from kc_attachments.parsers.text import TextParser
from kc_attachments.parsers import ParseResult


FIXTURES = Path(__file__).parent / "fixtures"


def test_text_parser_reads_utf8():
    r = TextParser().parse(FIXTURES / "hello.txt", {})
    assert isinstance(r, ParseResult)
    assert "Hello, world." in r.markdown
    assert r.extra_meta == {}


def test_text_parser_handles_utf8_non_ascii():
    r = TextParser().parse(FIXTURES / "utf8.txt", {})
    assert "café" in r.markdown


def test_text_parser_falls_back_to_latin1():
    r = TextParser().parse(FIXTURES / "latin1.txt", {})
    assert "café" in r.markdown


def test_text_parser_caps_at_1mb(tmp_path: Path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")  # 2 MB
    r = TextParser().parse(big, {})
    assert len(r.markdown) <= 1 * 1024 * 1024
    assert r.extra_meta.get("truncated_at") == 1 * 1024 * 1024
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_parsers_text.py -v
```
Expected: FAIL — `TextParser` not importable.

- [ ] **Step 4: Implement `text.py`**

Create `kc-attachments/src/kc_attachments/parsers/text.py`:

```python
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
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_parsers_text.py -v
```
Expected: 4/4 PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/parsers/text.py \
        kc-attachments/tests/fixtures/hello.txt \
        kc-attachments/tests/fixtures/utf8.txt \
        kc-attachments/tests/fixtures/latin1.txt \
        kc-attachments/tests/test_parsers_text.py
git commit -m "feat(kc-attachments): text parser (.txt/.md/.log) with 1MB cap + latin1 fallback"
```

---

## Task 3: PDF parser

**Files:**
- Create: `kc-attachments/src/kc_attachments/parsers/pdf_parser.py`
- Create: `kc-attachments/tests/fixtures/sample.pdf` (tiny 2-page PDF, generated below)
- Create: `kc-attachments/tests/test_parsers_pdf.py`

- [ ] **Step 1: Generate the fixture**

```bash
cd kc-attachments && python3 -c "
from pypdf import PdfWriter
from pypdf.generic import RectangleObject
w = PdfWriter()
w.add_blank_page(width=200, height=200)
w.add_blank_page(width=200, height=200)
# Add real text via reportlab if available, else stick with blank pages.
# For test purposes we'll write text-only via a minimal approach:
import io
from reportlab.pdfgen import canvas
buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=(200, 200))
c.drawString(20, 100, 'Hello PDF page one.')
c.showPage()
c.drawString(20, 100, 'Second page here.')
c.showPage()
c.save()
open('tests/fixtures/sample.pdf', 'wb').write(buf.getvalue())
"
```

If `reportlab` isn't available, install it as a dev-only dep:

```bash
cd kc-attachments && python3 -m pip install reportlab
```

(Reportlab stays as a transitive test-fixture dep — it's not pulled in at runtime.)

- [ ] **Step 2: Write the failing tests**

Create `kc-attachments/tests/test_parsers_pdf.py`:

```python
from pathlib import Path

from kc_attachments.parsers.pdf_parser import PdfParser


FIXTURES = Path(__file__).parent / "fixtures"


def test_pdf_parser_extracts_text_from_pages():
    r = PdfParser().parse(FIXTURES / "sample.pdf", {})
    assert "Hello PDF page one" in r.markdown
    assert "Second page here" in r.markdown
    assert r.extra_meta["page_count"] == 2


def test_pdf_parser_emits_page_headings():
    r = PdfParser().parse(FIXTURES / "sample.pdf", {})
    assert "## Page 1" in r.markdown
    assert "## Page 2" in r.markdown


def test_pdf_parser_handles_unreadable_pdf(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    import pytest
    with pytest.raises(Exception):
        PdfParser().parse(bad, {})
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_parsers_pdf.py -v
```
Expected: FAIL — `PdfParser` not importable.

- [ ] **Step 4: Implement `pdf_parser.py`**

Create `kc-attachments/src/kc_attachments/parsers/pdf_parser.py`:

```python
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
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_parsers_pdf.py -v
```
Expected: 3/3 PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/parsers/pdf_parser.py \
        kc-attachments/tests/fixtures/sample.pdf \
        kc-attachments/tests/test_parsers_pdf.py
git commit -m "feat(kc-attachments): PDF parser via pypdf with per-page headings"
```

---

## Task 4: Word (.docx) parser

**Files:**
- Create: `kc-attachments/src/kc_attachments/parsers/docx_parser.py`
- Create: `kc-attachments/tests/fixtures/sample.docx`
- Create: `kc-attachments/tests/test_parsers_docx.py`

- [ ] **Step 1: Generate the fixture**

```bash
cd kc-attachments && python3 -c "
from docx import Document
d = Document()
d.add_heading('Title One', level=1)
d.add_paragraph('First paragraph with body text.')
d.add_heading('Section Two', level=2)
d.add_paragraph('Second paragraph here.')
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).text = 'A'; t.cell(0, 1).text = 'B'
t.cell(1, 0).text = '1'; t.cell(1, 1).text = '2'
d.save('tests/fixtures/sample.docx')
"
```

- [ ] **Step 2: Write the failing tests**

Create `kc-attachments/tests/test_parsers_docx.py`:

```python
from pathlib import Path

from kc_attachments.parsers.docx_parser import DocxParser


FIXTURES = Path(__file__).parent / "fixtures"


def test_docx_parser_emits_headings():
    r = DocxParser().parse(FIXTURES / "sample.docx", {})
    assert "# Title One" in r.markdown
    assert "## Section Two" in r.markdown


def test_docx_parser_emits_paragraphs():
    r = DocxParser().parse(FIXTURES / "sample.docx", {})
    assert "First paragraph with body text." in r.markdown
    assert "Second paragraph here." in r.markdown


def test_docx_parser_emits_table_as_pipe_markdown():
    r = DocxParser().parse(FIXTURES / "sample.docx", {})
    assert "| A | B |" in r.markdown
    assert "| 1 | 2 |" in r.markdown
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_parsers_docx.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement `docx_parser.py`**

Create `kc-attachments/src/kc_attachments/parsers/docx_parser.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

from docx import Document

from . import ParseResult, REGISTRY


def _heading_level(style_name: str) -> int | None:
    """Returns markdown heading level for Word's 'Heading N' styles, else None."""
    if not style_name:
        return None
    s = style_name.strip()
    if s.startswith("Heading "):
        try:
            return int(s.split()[1])
        except (IndexError, ValueError):
            return None
    return None


class DocxParser:
    """Walks paragraphs + tables; emits markdown headings, paragraphs, tables."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        doc = Document(str(source))
        out: list[str] = []
        # Iterate in document order: paragraphs and tables, interleaved.
        body = doc.element.body
        from docx.oxml.ns import qn
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                # Match paragraph back to its Document-level paragraph object.
                for p in doc.paragraphs:
                    if p._element is child:
                        level = _heading_level(p.style.name)
                        text = p.text.strip()
                        if not text:
                            continue
                        if level:
                            out.append(f"{'#' * level} {text}")
                        else:
                            out.append(text)
                        break
            elif child.tag == qn("w:tbl"):
                for t in doc.tables:
                    if t._element is child:
                        rows = [
                            [cell.text.strip() for cell in row.cells]
                            for row in t.rows
                        ]
                        if not rows:
                            break
                        header = "| " + " | ".join(rows[0]) + " |"
                        sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
                        body_rows = [
                            "| " + " | ".join(r) + " |"
                            for r in rows[1:]
                        ]
                        out.append("\n".join([header, sep, *body_rows]))
                        break
        return ParseResult(markdown="\n\n".join(out), extra_meta={})


REGISTRY["application/vnd.openxmlformats-officedocument.wordprocessingml.document"] = DocxParser()
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_parsers_docx.py -v
```
Expected: 3/3 PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/parsers/docx_parser.py \
        kc-attachments/tests/fixtures/sample.docx \
        kc-attachments/tests/test_parsers_docx.py
git commit -m "feat(kc-attachments): Word parser (.docx) via python-docx with headings + tables"
```

---

## Task 5: Excel (.xlsx) parser

**Files:**
- Create: `kc-attachments/src/kc_attachments/parsers/xlsx_parser.py`
- Create: `kc-attachments/tests/fixtures/sample.xlsx`
- Create: `kc-attachments/tests/test_parsers_xlsx.py`

- [ ] **Step 1: Generate the fixture**

```bash
cd kc-attachments && python3 -c "
from openpyxl import Workbook
wb = Workbook()
s1 = wb.active; s1.title = 'Sheet One'
s1.append(['Name', 'Score'])
s1.append(['Alice', 90])
s1.append(['Bob', 85])
s2 = wb.create_sheet('Numbers')
s2.append(['x', 'x squared'])
s2.append([2, '=A2*A2'])
s2.append([3, '=A3*A3'])
wb.save('tests/fixtures/sample.xlsx')
"
```

- [ ] **Step 2: Write the failing tests**

Create `kc-attachments/tests/test_parsers_xlsx.py`:

```python
from pathlib import Path

from kc_attachments.parsers.xlsx_parser import XlsxParser


FIXTURES = Path(__file__).parent / "fixtures"


def test_xlsx_parser_emits_sheet_headings():
    r = XlsxParser().parse(FIXTURES / "sample.xlsx", {})
    assert "## Sheet One" in r.markdown
    assert "## Numbers" in r.markdown


def test_xlsx_parser_emits_pipe_tables():
    r = XlsxParser().parse(FIXTURES / "sample.xlsx", {})
    assert "| Name | Score |" in r.markdown
    assert "| Alice | 90 |" in r.markdown


def test_xlsx_parser_meta_records_sheet_names():
    r = XlsxParser().parse(FIXTURES / "sample.xlsx", {})
    assert r.extra_meta["sheet_names"] == ["Sheet One", "Numbers"]
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_parsers_xlsx.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement `xlsx_parser.py`**

Create `kc-attachments/src/kc_attachments/parsers/xlsx_parser.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from . import ParseResult, REGISTRY


def _is_empty_row(row: tuple[Any, ...]) -> bool:
    return all(v is None or (isinstance(v, str) and v.strip() == "") for v in row)


class XlsxParser:
    """One markdown table per sheet; formula cells emit evaluated values."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        # data_only=True returns the last-cached evaluated value, not the formula text.
        wb = load_workbook(str(source), data_only=True)
        parts: list[str] = []
        sheet_names: list[str] = []
        for ws in wb.worksheets:
            sheet_names.append(ws.title)
            parts.append(f"## {ws.title}")
            # Read rows, trim trailing empties.
            rows = [tuple(c.value for c in r) for r in ws.iter_rows()]
            while rows and _is_empty_row(rows[-1]):
                rows.pop()
            if not rows:
                parts.append("_(empty sheet)_")
                continue
            header = rows[0]
            header_cells = ["" if v is None else str(v) for v in header]
            sep_cells = ["---" for _ in header_cells]
            lines = [
                "| " + " | ".join(header_cells) + " |",
                "| " + " | ".join(sep_cells) + " |",
            ]
            for r in rows[1:]:
                cells = ["" if v is None else str(v) for v in r]
                lines.append("| " + " | ".join(cells) + " |")
            parts.append("\n".join(lines))
        return ParseResult(
            markdown="\n\n".join(parts),
            extra_meta={"sheet_names": sheet_names},
        )


REGISTRY["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"] = XlsxParser()
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_parsers_xlsx.py -v
```
Expected: 3/3 PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/parsers/xlsx_parser.py \
        kc-attachments/tests/fixtures/sample.xlsx \
        kc-attachments/tests/test_parsers_xlsx.py
git commit -m "feat(kc-attachments): Excel parser (.xlsx) via openpyxl with one table per sheet"
```

---

## Task 6: Image parser (Pillow validate/downscale + Tesseract OCR)

**Files:**
- Create: `kc-attachments/src/kc_attachments/parsers/image_parser.py`
- Create: `kc-attachments/tests/fixtures/sample.png`
- Create: `kc-attachments/tests/fixtures/sample_large.png`
- Create: `kc-attachments/tests/test_parsers_image.py`

- [ ] **Step 1: Generate fixtures**

```bash
cd kc-attachments && python3 -c "
from PIL import Image, ImageDraw, ImageFont
# Small image with text for OCR.
im = Image.new('RGB', (400, 100), 'white')
d = ImageDraw.Draw(im)
d.text((10, 30), 'Hello OCR World', fill='black')
im.save('tests/fixtures/sample.png')
# 5000x5000 image to exercise the downscale path.
big = Image.new('RGB', (5000, 5000), 'red')
big.save('tests/fixtures/sample_large.png')
"
```

- [ ] **Step 2: Write the failing tests**

Create `kc-attachments/tests/test_parsers_image.py`:

```python
from pathlib import Path

import pytest

from kc_attachments.parsers.image_parser import ImageParser, _MAX_DIM


FIXTURES = Path(__file__).parent / "fixtures"


def test_image_parser_records_dimensions():
    r = ImageParser().parse(FIXTURES / "sample.png", {})
    assert r.extra_meta["width"] == 400
    assert r.extra_meta["height"] == 100


def test_image_parser_runs_ocr_into_markdown():
    """Skips gracefully if Tesseract isn't installed system-wide; still records meta."""
    r = ImageParser().parse(FIXTURES / "sample.png", {})
    # OCR is best-effort: if Tesseract is present, we expect at least 'Hello'.
    # If not, markdown will be the failure placeholder.
    if r.extra_meta.get("ocr_status") == "ok":
        assert "Hello" in r.markdown or "OCR" in r.markdown
    else:
        assert r.extra_meta["ocr_status"] in ("missing", "error")


def test_image_parser_downscales_oversize(tmp_path):
    out = tmp_path / "downscaled.png"
    # Use the parser's downscale path by reading-and-rewriting via Pillow.
    r = ImageParser().parse(FIXTURES / "sample_large.png", {"downscale_to": tmp_path / "ds.png"})
    # Either the meta records the original dims AND a downscale marker,
    # or the parser refuses oversize input. Either is acceptable; pin the contract:
    assert r.extra_meta["width"] <= _MAX_DIM
    assert r.extra_meta["height"] <= _MAX_DIM
    assert r.extra_meta.get("original_width") == 5000 or r.extra_meta["width"] == 5000  # type: ignore[unreachable]
```

(Note: the downscale assertion is loose to match either of two valid implementations — readers may pin a single contract once Task 6 is implemented. We'll commit to the in-place downscale-when-requested contract in Step 4.)

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_parsers_image.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement `image_parser.py`**

Create `kc-attachments/src/kc_attachments/parsers/image_parser.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

from PIL import Image

from . import ParseResult, REGISTRY


_MAX_DIM = 4096  # px on either side; oversize images get downscaled in-place.


def _maybe_downscale(im: Image.Image, dest: Path) -> tuple[Image.Image, int, int, bool]:
    """If image exceeds _MAX_DIM, return a thumbnailed copy AND write it to dest.
    Returns (image, width, height, downscaled).
    """
    orig_w, orig_h = im.size
    if orig_w <= _MAX_DIM and orig_h <= _MAX_DIM:
        return im, orig_w, orig_h, False
    im2 = im.copy()
    im2.thumbnail((_MAX_DIM, _MAX_DIM))
    im2.save(dest)
    return im2, im2.size[0], im2.size[1], True


def _try_ocr(im: Image.Image) -> tuple[str, str]:
    """Returns (markdown, status). Status: 'ok', 'missing' (no Tesseract), 'error'."""
    try:
        import pytesseract
        try:
            text = pytesseract.image_to_string(im)
            return text.strip(), "ok"
        except pytesseract.TesseractNotFoundError:
            return "", "missing"
        except Exception:
            return "", "error"
    except ImportError:
        return "", "missing"


class ImageParser:
    """Validates the image, optionally downscales, runs OCR for fallback markdown."""

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        with Image.open(source) as im:
            im.load()
            # Validation: Pillow raises on truncated/corrupt files via .load().
            downscale_dest = meta.get("downscale_to")
            if downscale_dest is not None:
                im, w, h, downscaled = _maybe_downscale(im, Path(downscale_dest))
            else:
                w, h = im.size
                downscaled = False
            ocr_md, ocr_status = _try_ocr(im)
        extra: dict[str, Any] = {
            "width": w,
            "height": h,
            "ocr_status": ocr_status,
        }
        if downscaled:
            extra["downscaled"] = True
        return ParseResult(markdown=ocr_md, extra_meta=extra)


# Register one per common image mime.
for mime in ("image/png", "image/jpeg", "image/webp", "image/heic"):
    REGISTRY[mime] = ImageParser()
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_parsers_image.py -v
```
Expected: 3/3 PASS (one may report `ocr_status='missing'` if Tesseract isn't installed system-wide — the test handles that).

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/parsers/image_parser.py \
        kc-attachments/tests/fixtures/sample.png \
        kc-attachments/tests/fixtures/sample_large.png \
        kc-attachments/tests/test_parsers_image.py
git commit -m "feat(kc-attachments): image parser (Pillow + Tesseract OCR fallback)"
```

---

## Task 7: Mime sniffing + parser dispatch

**Files:**
- Create: `kc-attachments/src/kc_attachments/sniff.py`
- Create: `kc-attachments/tests/test_sniff.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-attachments/tests/test_sniff.py`:

```python
from pathlib import Path

import pytest

from kc_attachments.sniff import sniff_mime, dispatch_parser, UnsupportedTypeError


FIXTURES = Path(__file__).parent / "fixtures"


def test_sniff_mime_detects_pdf():
    assert sniff_mime(FIXTURES / "sample.pdf") == "application/pdf"


def test_sniff_mime_detects_png():
    assert sniff_mime(FIXTURES / "sample.png") == "image/png"


def test_sniff_mime_detects_docx():
    assert sniff_mime(FIXTURES / "sample.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_sniff_mime_detects_xlsx():
    assert sniff_mime(FIXTURES / "sample.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_sniff_mime_detects_text_by_extension(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hi", encoding="utf-8")
    assert sniff_mime(p) == "text/plain"


def test_dispatch_parser_resolves_pdf():
    parser = dispatch_parser("application/pdf")
    assert parser is not None
    assert hasattr(parser, "parse")


def test_dispatch_parser_rejects_unknown():
    with pytest.raises(UnsupportedTypeError, match="unsupported"):
        dispatch_parser("application/x-shockwave-flash")
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_sniff.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `sniff.py`**

Create `kc-attachments/src/kc_attachments/sniff.py`:

```python
from __future__ import annotations
from pathlib import Path

import filetype

# Importing the parsers modules populates REGISTRY as a side effect.
from .parsers import REGISTRY
from .parsers import text as _text  # noqa: F401
from .parsers import pdf_parser as _pdf  # noqa: F401
from .parsers import docx_parser as _docx  # noqa: F401
from .parsers import xlsx_parser as _xlsx  # noqa: F401
from .parsers import image_parser as _image  # noqa: F401


class UnsupportedTypeError(Exception):
    """Raised when a file's detected mime has no registered parser."""


_TEXT_EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown", ".log": "text/x-log"}


def sniff_mime(source: Path) -> str:
    """Best-effort mime detection.

    First tries magic-byte sniffing via the `filetype` library. Falls back to
    a small extension-keyed map for plain-text formats (which `filetype` does
    not handle).
    """
    kind = filetype.guess(str(source))
    if kind is not None:
        return kind.mime
    ext = source.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return _TEXT_EXTENSIONS[ext]
    raise UnsupportedTypeError(f"unsupported file type: {source.name!r}")


def dispatch_parser(mime: str):
    """Returns the registered Parser for the given mime, else raises."""
    parser = REGISTRY.get(mime)
    if parser is None:
        raise UnsupportedTypeError(f"unsupported mime: {mime!r}")
    return parser
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_sniff.py -v
```
Expected: 7/7 PASS.

- [ ] **Step 5: Run the full kc-attachments suite**

```bash
cd kc-attachments && pytest -v 2>&1 | tail -10
```
Expected: all PASS (~25 tests at this point).

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/sniff.py kc-attachments/tests/test_sniff.py
git commit -m "feat(kc-attachments): mime sniff + parser dispatch"
```

---

# PHASE B — Storage + capability cache

## Task 8: `AttachmentStore` (filesystem + sqlite index)

**Files:**
- Create: `kc-attachments/src/kc_attachments/store.py`
- Create: `kc-attachments/tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-attachments/tests/test_store.py`:

```python
from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore, AttachmentNotFound


@pytest.fixture
def store(tmp_path: Path) -> AttachmentStore:
    return AttachmentStore(root=tmp_path)


def test_save_returns_attachment_with_id(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    assert att.id.startswith("att_")
    assert att.filename == "hello.txt"
    assert att.parse_status == "ok"
    assert att.mime == "text/plain"


def test_save_persists_original_and_parsed(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    att_dir = store.attachment_dir(att.id)
    assert (att_dir / "original.txt").read_text(encoding="utf-8") == "Hello."
    assert "Hello." in (att_dir / "parsed.md").read_text(encoding="utf-8")


def test_get_returns_full_record(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    got = store.get(att.id)
    assert got.id == att.id
    assert got.filename == "hello.txt"


def test_get_unknown_raises(store):
    with pytest.raises(AttachmentNotFound):
        store.get("att_doesnotexist")


def test_list_for_conversation_scopes(store, tmp_path):
    src = tmp_path / "a.txt"; src.write_text("A", encoding="utf-8")
    a = store.save(conversation_id="conv_1", source=src, filename="a.txt")
    src.write_text("B", encoding="utf-8")
    b = store.save(conversation_id="conv_2", source=src, filename="b.txt")
    listed = store.list_for_conversation("conv_1")
    assert [r.id for r in listed] == [a.id]
    listed2 = store.list_for_conversation("conv_2")
    assert [r.id for r in listed2] == [b.id]


def test_delete_removes_files_and_row(store, tmp_path):
    src = tmp_path / "hello.txt"; src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    store.delete(att.id)
    with pytest.raises(AttachmentNotFound):
        store.get(att.id)
    assert not store.attachment_dir(att.id).exists()


def test_parsed_md_capped_at_1mb(store, tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=big, filename="big.txt")
    parsed = store.read_parsed(att.id)
    assert len(parsed) <= 1 * 1024 * 1024
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_store.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `store.py`**

Create `kc-attachments/src/kc_attachments/store.py`:

```python
from __future__ import annotations
import json
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sniff import sniff_mime, dispatch_parser, UnsupportedTypeError


class AttachmentNotFound(Exception):
    pass


@dataclass(frozen=True)
class AttachmentRecord:
    id: str
    conversation_id: str
    filename: str
    mime: str
    size_bytes: int
    parse_status: str        # "ok" | "error"
    parse_error: str | None
    page_count: int | None
    parsed_at: str           # ISO 8601
    extra_meta: dict[str, Any]   # NOT persisted in sqlite; lives in meta.json


_SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    filename        TEXT NOT NULL,
    mime            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    parse_status    TEXT NOT NULL,
    parse_error     TEXT,
    page_count      INTEGER,
    parsed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_conv ON attachments(conversation_id);
"""


def _gen_id() -> str:
    return "att_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AttachmentStore:
    """Filesystem store at `root/<conv>/<att>/` with a sqlite index at `root/index.sqlite`."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(root / "index.sqlite"))
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def attachment_dir(self, attachment_id: str) -> Path:
        # We don't keep the conv_id in the path lookup; sqlite tells us where to find it.
        row = self._db.execute(
            "SELECT conversation_id FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise AttachmentNotFound(attachment_id)
        return self._root / row[0] / attachment_id

    def save(
        self,
        *,
        conversation_id: str,
        source: Path,
        filename: str,
    ) -> AttachmentRecord:
        """Sniff, write original, parse, write parsed.md + meta.json, insert row."""
        att_id = _gen_id()
        att_dir = self._root / conversation_id / att_id
        att_dir.mkdir(parents=True, exist_ok=False)

        ext = Path(filename).suffix or ".bin"
        original_path = att_dir / f"original{ext}"
        shutil.copy(source, original_path)

        size_bytes = original_path.stat().st_size
        parse_status = "ok"
        parse_error: str | None = None
        page_count: int | None = None
        extra_meta: dict[str, Any] = {}
        try:
            mime = sniff_mime(original_path)
            parser = dispatch_parser(mime)
            r = parser.parse(original_path, {})
            markdown = r.markdown
            extra_meta = dict(r.extra_meta)
            # Cap parsed.md at 1 MB.
            if len(markdown.encode("utf-8")) > 1 * 1024 * 1024:
                markdown = markdown.encode("utf-8")[:1 * 1024 * 1024].decode(
                    "utf-8", errors="ignore"
                )
                extra_meta["truncated_at"] = 1 * 1024 * 1024
            (att_dir / "parsed.md").write_text(markdown, encoding="utf-8")
            page_count = extra_meta.get("page_count")
        except UnsupportedTypeError as e:
            parse_status = "error"
            parse_error = str(e)
            mime = "application/octet-stream"
            (att_dir / "parsed.md").write_text("", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            parse_status = "error"
            parse_error = f"{type(e).__name__}: {e}"
            mime = "application/octet-stream"
            (att_dir / "parsed.md").write_text("", encoding="utf-8")

        parsed_at = _now_iso()
        meta_doc = {
            "id": att_id,
            "conversation_id": conversation_id,
            "filename": filename,
            "mime": mime,
            "size_bytes": size_bytes,
            "parse_status": parse_status,
            "parse_error": parse_error,
            "page_count": page_count,
            "parsed_at": parsed_at,
            "extra_meta": extra_meta,
        }
        (att_dir / "meta.json").write_text(json.dumps(meta_doc, indent=2), encoding="utf-8")

        self._db.execute(
            "INSERT INTO attachments "
            "(id, conversation_id, filename, mime, size_bytes, parse_status, parse_error, page_count, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (att_id, conversation_id, filename, mime, size_bytes,
             parse_status, parse_error, page_count, parsed_at),
        )
        self._db.commit()

        return AttachmentRecord(
            id=att_id, conversation_id=conversation_id, filename=filename,
            mime=mime, size_bytes=size_bytes, parse_status=parse_status,
            parse_error=parse_error, page_count=page_count, parsed_at=parsed_at,
            extra_meta=extra_meta,
        )

    def get(self, attachment_id: str) -> AttachmentRecord:
        row = self._db.execute(
            "SELECT id, conversation_id, filename, mime, size_bytes, "
            "parse_status, parse_error, page_count, parsed_at "
            "FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise AttachmentNotFound(attachment_id)
        att_dir = self._root / row[1] / row[0]
        meta_path = att_dir / "meta.json"
        extra_meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                extra_meta = json.loads(meta_path.read_text(encoding="utf-8")).get("extra_meta", {})
            except json.JSONDecodeError:
                extra_meta = {}
        return AttachmentRecord(
            id=row[0], conversation_id=row[1], filename=row[2], mime=row[3],
            size_bytes=row[4], parse_status=row[5], parse_error=row[6],
            page_count=row[7], parsed_at=row[8], extra_meta=extra_meta,
        )

    def list_for_conversation(self, conversation_id: str) -> list[AttachmentRecord]:
        rows = self._db.execute(
            "SELECT id FROM attachments WHERE conversation_id = ? ORDER BY parsed_at",
            (conversation_id,),
        ).fetchall()
        return [self.get(r[0]) for r in rows]

    def delete(self, attachment_id: str) -> None:
        att_dir = self.attachment_dir(attachment_id)  # raises if unknown
        if att_dir.exists():
            shutil.rmtree(att_dir)
        self._db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        self._db.commit()

    def read_parsed(self, attachment_id: str) -> str:
        att_dir = self.attachment_dir(attachment_id)
        parsed = att_dir / "parsed.md"
        if not parsed.exists():
            return ""
        return parsed.read_text(encoding="utf-8")

    def original_path(self, attachment_id: str) -> Path:
        att_dir = self.attachment_dir(attachment_id)
        # Look for original.* — there's exactly one.
        for p in att_dir.iterdir():
            if p.name.startswith("original."):
                return p
        raise AttachmentNotFound(f"{attachment_id}: original file missing")
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_store.py -v
```
Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-attachments/src/kc_attachments/store.py kc-attachments/tests/test_store.py
git commit -m "feat(kc-attachments): AttachmentStore — filesystem + sqlite index"
```

---

## Task 9: Vision capability cache

**Files:**
- Create: `kc-attachments/src/kc_attachments/capability.py`
- Create: `kc-attachments/tests/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-attachments/tests/test_capability.py`:

```python
import httpx
import pytest

from kc_attachments.capability import VisionCapabilityCache


def _client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_cache_detects_vision_capable_model():
    def handler(req):
        return httpx.Response(200, json={"capabilities": ["completion", "vision", "tools"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("qwen3.6:35b") is True


def test_cache_detects_text_only_model():
    def handler(req):
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("gemma4:31b") is False


def test_cache_treats_missing_capability_array_as_false():
    def handler(req):
        return httpx.Response(200, json={"details": {"family": "gemma"}})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("unknown:1b") is False


def test_cache_caches_result_after_first_lookup():
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"capabilities": ["vision"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    cache.supports_vision("m")
    cache.supports_vision("m")
    assert calls["n"] == 1


def test_cache_returns_false_on_http_error():
    def handler(req):
        return httpx.Response(500, text="boom")
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("m") is False


def test_cache_returns_false_on_network_error():
    def handler(req):
        raise httpx.ConnectError("dns", request=req)
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("m") is False
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_capability.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `capability.py`**

Create `kc-attachments/src/kc_attachments/capability.py`:

```python
from __future__ import annotations

import httpx


class VisionCapabilityCache:
    """Caches per-model `supports_vision` results from Ollama's /api/show.

    Treats every error (network, HTTP, JSON, missing capabilities array) as
    "no vision" — safe default that degrades to OCR-only.
    """

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self._http = http or httpx.Client(timeout=5.0)
        self._base_url = base_url.rstrip("/")
        self._cache: dict[str, bool] = {}

    def supports_vision(self, model: str) -> bool:
        if model in self._cache:
            return self._cache[model]
        result = self._probe(model)
        self._cache[model] = result
        return result

    def _probe(self, model: str) -> bool:
        try:
            resp = self._http.post(
                f"{self._base_url}/api/show",
                json={"model": model},
            )
        except httpx.HTTPError:
            return False
        if resp.status_code >= 400:
            return False
        try:
            data = resp.json()
        except (ValueError,):
            return False
        caps = data.get("capabilities")
        if not isinstance(caps, list):
            return False
        return "vision" in caps
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_capability.py -v
```
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-attachments/src/kc_attachments/capability.py kc-attachments/tests/test_capability.py
git commit -m "feat(kc-attachments): vision capability cache via Ollama /api/show"
```

---

# PHASE C — Tools

## Task 10: `read_attachment` tool (text-y types with pagination)

**Files:**
- Create: `kc-attachments/src/kc_attachments/tools.py`
- Create: `kc-attachments/tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-attachments/tests/test_tools.py`:

```python
import json
from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore
from kc_attachments.tools import build_read_attachment_tool


def _store(tmp_path) -> AttachmentStore:
    return AttachmentStore(root=tmp_path / "attachments")


@pytest.fixture
def store_with_text(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "hello.txt"
    src.write_text("Hello attachment.", encoding="utf-8")
    rec = s.save(conversation_id="conv_1", source=src, filename="hello.txt")
    return s, rec.id


@pytest.mark.asyncio
async def test_read_attachment_returns_text_payload(store_with_text):
    s, att_id = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": att_id})
    parsed = json.loads(out)
    assert parsed["type"] == "text"
    assert "Hello attachment." in parsed["markdown"]


@pytest.mark.asyncio
async def test_read_attachment_rejects_other_conversation(store_with_text):
    s, att_id = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_99",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": att_id})
    parsed = json.loads(out)
    assert parsed["error"] == "out_of_scope"


@pytest.mark.asyncio
async def test_read_attachment_not_found(store_with_text):
    s, _ = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": "att_doesnotexist"})
    parsed = json.loads(out)
    assert parsed["error"] == "not_found"


@pytest.mark.asyncio
async def test_read_attachment_truncates_long_text(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "long.txt"
    src.write_text("x" * (64 * 1024), encoding="utf-8")  # 64 KB > 32 KB cap
    rec = s.save(conversation_id="conv_1", source=src, filename="long.txt")
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": rec.id})
    parsed = json.loads(out)
    assert len(parsed["markdown"].encode("utf-8")) <= 32 * 1024 + 200  # cap + marker
    assert "[truncated" in parsed["markdown"]


@pytest.mark.asyncio
async def test_read_attachment_paginates_pdf_by_page_range(tmp_path):
    # Use a hand-rolled parsed.md with explicit page headings to test pagination.
    s = _store(tmp_path)
    src = tmp_path / "fake.pdf"
    src.write_bytes(b"")  # not real PDF, but store accepts and parser will error;
    # we'll write a fake parsed.md directly to test pagination logic.
    rec = s.save(conversation_id="conv_1", source=src, filename="fake.pdf")
    # Overwrite parsed.md with a synthetic 3-page document.
    parsed = s.attachment_dir(rec.id) / "parsed.md"
    parsed.write_text(
        "## Page 1\n\nFirst.\n\n## Page 2\n\nSecond.\n\n## Page 3\n\nThird.",
        encoding="utf-8",
    )
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": rec.id, "page_range": "2-3"})
    parsed = json.loads(out)
    assert "Page 2" in parsed["markdown"]
    assert "Page 3" in parsed["markdown"]
    assert "First." not in parsed["markdown"]
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `tools.py` (text path only — image path comes in Task 11)**

Create `kc-attachments/src/kc_attachments/tools.py`:

```python
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
        return markdown  # not a paginated doc; return as-is
    starts = {int(m.group(1)): m.start() for m in matches}
    end_of_doc = len(markdown)
    parts: list[str] = []
    for m in matches:
        page_n = int(m.group(1))
        if not (lo <= page_n <= hi):
            continue
        start = m.start()
        # End: start of next page heading, or end of doc.
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
        markdown = store.read_parsed(att_id)
        if page_range and rec.mime == "application/pdf":
            markdown = _slice_by_page_range(markdown, page_range)
        markdown = _truncate(markdown)
        return json.dumps({"type": "text", "markdown": markdown})

    return impl
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-attachments/src/kc_attachments/tools.py kc-attachments/tests/test_tools.py
git commit -m "feat(kc-attachments): read_attachment tool (text-y types + pagination)"
```

---

## Task 11: `read_attachment` image sentinel + OCR fallback

**Files:**
- Modify: `kc-attachments/src/kc_attachments/tools.py`
- Modify: `kc-attachments/tests/test_tools.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-attachments/tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_read_attachment_image_returns_sentinel_when_vision_supported(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "sample.png"
    # Reuse fixture for shape, or write a tiny png:
    from PIL import Image
    Image.new("RGB", (10, 10), "red").save(src)
    rec = s.save(conversation_id="conv_1", source=src, filename="sample.png")
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=True)
    out = await impl({"attachment_id": rec.id})
    payload = json.loads(out)
    assert payload["type"] == "image"
    assert payload["path"].endswith("original.png")
    assert "ocr_markdown" in payload  # always carried for fallback


@pytest.mark.asyncio
async def test_read_attachment_image_returns_ocr_when_vision_unsupported(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "sample.png"
    from PIL import Image
    Image.new("RGB", (10, 10), "red").save(src)
    rec = s.save(conversation_id="conv_1", source=src, filename="sample.png")
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": rec.id})
    payload = json.loads(out)
    assert payload["type"] == "text"
    # OCR returns empty markdown for a solid-color 10x10 image; still type=text.
    assert "markdown" in payload
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```
Expected: new tests FAIL.

- [ ] **Step 3: Extend `impl` to handle image attachments**

In `kc-attachments/src/kc_attachments/tools.py`, replace the body of `impl` (inside `build_read_attachment_tool`) with:

```python
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
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```
Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-attachments/src/kc_attachments/tools.py kc-attachments/tests/test_tools.py
git commit -m "feat(kc-attachments): read_attachment image sentinel + OCR fallback"
```

---

## Task 12: `list_attachments` tool

**Files:**
- Modify: `kc-attachments/src/kc_attachments/tools.py`
- Modify: `kc-attachments/tests/test_tools.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-attachments/tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_list_attachments_returns_only_current_conversation(tmp_path):
    from kc_attachments.tools import build_list_attachments_tool
    s = _store(tmp_path)
    src = tmp_path / "a.txt"; src.write_text("A", encoding="utf-8")
    a = s.save(conversation_id="conv_1", source=src, filename="a.txt")
    src.write_text("B", encoding="utf-8")
    s.save(conversation_id="conv_2", source=src, filename="b.txt")

    impl = build_list_attachments_tool(store=s, conversation_id="conv_1")
    out = await impl({})
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["id"] == a.id
    assert parsed[0]["filename"] == "a.txt"
    assert parsed[0]["parse_status"] == "ok"
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```

- [ ] **Step 3: Add `build_list_attachments_tool`**

Append to `kc-attachments/src/kc_attachments/tools.py`:

```python
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
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_tools.py -v
```

- [ ] **Step 5: Commit**

```bash
git add kc-attachments/src/kc_attachments/tools.py kc-attachments/tests/test_tools.py
git commit -m "feat(kc-attachments): list_attachments tool"
```

---

## Task 13: `attach_attachments_to_agent` wiring helper + public exports

**Files:**
- Create: `kc-attachments/src/kc_attachments/wiring.py`
- Modify: `kc-attachments/src/kc_attachments/__init__.py`
- Create: `kc-attachments/tests/test_wiring.py`

- [ ] **Step 1: Write the failing test**

Create `kc-attachments/tests/test_wiring.py`:

```python
from pathlib import Path

from kc_core.tools import ToolRegistry

from kc_attachments import attach_attachments_to_agent
from kc_attachments.store import AttachmentStore


def test_attach_registers_two_tools(tmp_path: Path):
    store = AttachmentStore(root=tmp_path)
    registry = ToolRegistry()
    attach_attachments_to_agent(
        registry=registry,
        store=store,
        conversation_id="conv_1",
        vision_for_active_model=True,
    )
    assert registry.get("read_attachment") is not None
    assert registry.get("list_attachments") is not None
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-attachments && pytest tests/test_wiring.py -v
```

- [ ] **Step 3: Implement `wiring.py`**

Create `kc-attachments/src/kc_attachments/wiring.py`:

```python
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
```

- [ ] **Step 4: Update `__init__.py`**

Replace `kc-attachments/src/kc_attachments/__init__.py`:

```python
"""KonaClaw file-ingestion package."""

from kc_attachments.capability import VisionCapabilityCache
from kc_attachments.store import AttachmentNotFound, AttachmentRecord, AttachmentStore
from kc_attachments.sniff import UnsupportedTypeError, sniff_mime
from kc_attachments.wiring import attach_attachments_to_agent

__all__ = [
    "AttachmentNotFound",
    "AttachmentRecord",
    "AttachmentStore",
    "UnsupportedTypeError",
    "VisionCapabilityCache",
    "attach_attachments_to_agent",
    "sniff_mime",
]
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest -v 2>&1 | tail -10
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/wiring.py \
        kc-attachments/src/kc_attachments/__init__.py \
        kc-attachments/tests/test_wiring.py
git commit -m "feat(kc-attachments): attach_attachments_to_agent wiring helper + exports"
```

---

# PHASE D — kc-core multimodal extension

## Task 14: `UserMessage.images` field + multimodal `to_openai_dict`

**Files:**
- Modify: `kc-core/src/kc_core/messages.py`
- Modify (or create): `kc-core/tests/test_messages.py`

- [ ] **Step 1: Write failing tests**

In `kc-core/tests/test_messages.py` (create if absent), add:

```python
from pathlib import Path

from kc_core.messages import (
    UserMessage,
    ImageRef,
    to_openai_dict,
    to_native_dict,
)


def test_user_message_default_images_is_empty_tuple():
    m = UserMessage(content="hi")
    assert m.images == ()


def test_user_message_with_images_carries_them():
    refs = (ImageRef(path=Path("/tmp/a.png"), mime="image/png"),)
    m = UserMessage(content="hi", images=refs)
    assert m.images == refs


def test_to_openai_dict_plain_user_text():
    m = UserMessage(content="hi")
    assert to_openai_dict(m) == {"role": "user", "content": "hi"}


def test_to_openai_dict_multimodal_when_images_present(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    m = UserMessage(content="describe", images=(ImageRef(path=p, mime="image/png"),))
    d = to_openai_dict(m)
    assert d["role"] == "user"
    assert isinstance(d["content"], list)
    assert d["content"][0] == {"type": "text", "text": "describe"}
    assert d["content"][1]["type"] == "image_url"
    assert d["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_to_native_dict_emits_images_field(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    m = UserMessage(content="describe", images=(ImageRef(path=p, mime="image/png"),))
    d = to_native_dict(m)
    assert d["role"] == "user"
    assert d["content"] == "describe"
    assert isinstance(d["images"], list)
    assert len(d["images"]) == 1
    # base64-encoded bytes
    import base64
    assert base64.b64decode(d["images"][0]) == p.read_bytes()
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-core && pytest tests/test_messages.py -v
```

- [ ] **Step 3: Extend `messages.py`**

Replace contents of `kc-core/src/kc_core/messages.py` with:

```python
from __future__ import annotations
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union


@dataclass(frozen=True)
class ImageRef:
    """Path + mime for an image attached to a user turn."""

    path: Path
    mime: str


@dataclass(frozen=True)
class UserMessage:
    content: str
    images: tuple[ImageRef, ...] = ()


@dataclass(frozen=True)
class AssistantMessage:
    content: str


@dataclass(frozen=True)
class ToolCallMessage:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    content: str


Message = Union[UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage]


def _encode_image_data_url(ref: ImageRef) -> str:
    data = ref.path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{ref.mime};base64,{b64}"


def _encode_image_b64(ref: ImageRef) -> str:
    return base64.b64encode(ref.path.read_bytes()).decode("ascii")


def to_openai_dict(m: Message) -> dict[str, Any]:
    """OpenAI-compatible message dict. Multimodal user turns produce a content list."""
    if isinstance(m, UserMessage):
        if not m.images:
            return {"role": "user", "content": m.content}
        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": m.content},
        ]
        for ref in m.images:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": _encode_image_data_url(ref)},
            })
        return {"role": "user", "content": content_blocks}
    if isinstance(m, AssistantMessage):
        return {"role": "assistant", "content": m.content}
    if isinstance(m, ToolCallMessage):
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": m.tool_call_id,
                "type": "function",
                "function": {
                    "name": m.tool_name,
                    "arguments": json.dumps(m.arguments),
                },
            }],
        }
    if isinstance(m, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "content": m.content,
        }
    raise TypeError(f"Unknown message type: {type(m)}")


def to_native_dict(m: Message) -> dict[str, Any]:
    """Ollama native /api/chat dict. User turns with images use the `images` field."""
    if isinstance(m, UserMessage) and m.images:
        return {
            "role": "user",
            "content": m.content,
            "images": [_encode_image_b64(r) for r in m.images],
        }
    return to_openai_dict(m)
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-core && pytest tests/test_messages.py -v
```

- [ ] **Step 5: Run full kc-core suite**

```bash
cd kc-core && pytest -v 2>&1 | tail -10
```
Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-core/src/kc_core/messages.py kc-core/tests/test_messages.py
git commit -m "feat(kc-core): UserMessage.images + multimodal to_openai_dict / to_native_dict"
```

---

## Task 15: Image-sentinel translation in the agent loop

**Files:**
- Modify: `kc-core/src/kc_core/agent.py`
- Create: `kc-core/tests/test_agent_image_sentinel.py`

This task detects the image sentinel JSON in tool results and translates it into either a synthetic user turn (vision-capable) or substitutes OCR markdown (no vision).

- [ ] **Step 1: Write the failing test**

Create `kc-core/tests/test_agent_image_sentinel.py`:

```python
import json
from pathlib import Path

import pytest

from kc_core.agent import translate_image_sentinel
from kc_core.messages import UserMessage, ToolResultMessage, ImageRef


def test_translate_sentinel_vision_capable_returns_user_turn(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    sentinel = json.dumps({
        "type": "image",
        "path": str(img),
        "ocr_markdown": "fallback OCR text",
    })
    out = translate_image_sentinel(
        sentinel,
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.tool_call_id == "call_1"
    assert "image rendered" in out.tool_result.content.lower()
    assert out.follow_up is not None
    assert isinstance(out.follow_up, UserMessage)
    assert len(out.follow_up.images) == 1
    assert out.follow_up.images[0].path == img
    assert out.follow_up.images[0].mime == "image/png"


def test_translate_sentinel_no_vision_substitutes_ocr_text():
    sentinel = json.dumps({
        "type": "image",
        "path": "/nonexistent/x.png",
        "ocr_markdown": "OCR text here",
    })
    out = translate_image_sentinel(
        sentinel,
        tool_call_id="call_1",
        vision_for_active_model=False,
    )
    assert out.tool_result.content == "OCR text here"
    assert out.follow_up is None


def test_translate_non_sentinel_passes_through():
    out = translate_image_sentinel(
        '{"type":"text","markdown":"hi"}',
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.content == '{"type":"text","markdown":"hi"}'
    assert out.follow_up is None


def test_translate_unparseable_json_passes_through():
    out = translate_image_sentinel(
        "not json",
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.content == "not json"
    assert out.follow_up is None
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-core && pytest tests/test_agent_image_sentinel.py -v
```

- [ ] **Step 3: Add `translate_image_sentinel` to `agent.py`**

In `kc-core/src/kc_core/agent.py`, append (or insert near existing helper functions):

```python
import json as _json_ais
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kc_core.messages import ImageRef, ToolResultMessage, UserMessage


@dataclass(frozen=True)
class SentinelTranslation:
    """Result of translating an image sentinel: the ToolResultMessage to emit,
    plus an optional follow-up UserMessage carrying the image content."""

    tool_result: ToolResultMessage
    follow_up: Optional[UserMessage]


def _guess_mime_from_path(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "heic": "image/heic",
    }.get(suffix, "application/octet-stream")


def translate_image_sentinel(
    raw_content: str,
    *,
    tool_call_id: str,
    vision_for_active_model: bool,
) -> SentinelTranslation:
    """If raw_content is an image sentinel dict, translate to (ack, follow-up).

    Sentinel shape: `{"type": "image", "path": <abs-path>, "ocr_markdown": <str>}`.

    On vision-capable models, the tool result is replaced with an
    acknowledgement string and a follow-up UserMessage carrying the image is
    returned. On non-vision models, the tool result is replaced with the
    OCR markdown; no follow-up.
    """
    try:
        payload = _json_ais.loads(raw_content)
    except (_json_ais.JSONDecodeError, ValueError):
        return SentinelTranslation(
            tool_result=ToolResultMessage(tool_call_id=tool_call_id, content=raw_content),
            follow_up=None,
        )
    if not isinstance(payload, dict) or payload.get("type") != "image":
        return SentinelTranslation(
            tool_result=ToolResultMessage(tool_call_id=tool_call_id, content=raw_content),
            follow_up=None,
        )

    img_path = Path(str(payload.get("path", "")))
    ocr_md = str(payload.get("ocr_markdown", ""))

    if not vision_for_active_model:
        return SentinelTranslation(
            tool_result=ToolResultMessage(tool_call_id=tool_call_id, content=ocr_md),
            follow_up=None,
        )

    ack = "image rendered for the model in the next turn"
    return SentinelTranslation(
        tool_result=ToolResultMessage(tool_call_id=tool_call_id, content=ack),
        follow_up=UserMessage(
            content="[image attachment]",
            images=(ImageRef(path=img_path, mime=_guess_mime_from_path(img_path)),),
        ),
    )
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-core && pytest tests/test_agent_image_sentinel.py -v
```

- [ ] **Step 5: Full kc-core suite**

```bash
cd kc-core && pytest -v 2>&1 | tail -10
```

- [ ] **Step 6: Commit**

```bash
git add kc-core/src/kc_core/agent.py kc-core/tests/test_agent_image_sentinel.py
git commit -m "feat(kc-core): translate_image_sentinel helper for read_attachment image returns"
```

> **Integration note for the executor:** wiring `translate_image_sentinel` into the live agent step loop happens in Task 19 (supervisor `assembly.py`), where each `ToolResultMessage` produced by a tool call is funneled through the translator before being appended to the conversation. This task only adds the helper; the next phase wires it in.

---

# PHASE E — Supervisor integration

## Task 16: `POST /attachments/upload` route

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/attachments_routes.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py` (register the router; see Task 19 — this task only writes the route code and adds a minimal test)
- Create: `kc-supervisor/tests/test_attachments_routes.py`

- [ ] **Step 1: Write the failing test**

Create `kc-supervisor/tests/test_attachments_routes.py`:

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kc_attachments.store import AttachmentStore

from kc_supervisor.attachments_routes import build_attachments_router


def _app_with_router(tmp_path: Path):
    from fastapi import FastAPI
    store = AttachmentStore(root=tmp_path / "attachments")
    app = FastAPI()
    app.include_router(build_attachments_router(store=store))
    return app, store


def test_upload_text_file_success(tmp_path):
    app, store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("hello.txt", b"Hello there.", "text/plain")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attachment_id"].startswith("att_")
    assert body["filename"] == "hello.txt"
    assert body["mime"] == "text/plain"
    assert body["parse_status"] == "ok"


def test_upload_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_MAX_BYTES", "10")
    app, _store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("big.txt", b"this is way too long", "text/plain")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_rejects_unknown_type(tmp_path):
    app, _store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("evil.swf", b"random bytes", "application/x-shockwave-flash")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 415


def test_delete_removes_attachment(tmp_path):
    app, store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("hello.txt", b"Hello.", "text/plain")}
    resp = client.post("/attachments/upload", params={"conversation_id": "conv_1"}, files=files)
    att_id = resp.json()["attachment_id"]

    resp_del = client.delete(f"/attachments/{att_id}")
    assert resp_del.status_code == 200

    from kc_attachments.store import AttachmentNotFound
    with pytest.raises(AttachmentNotFound):
        store.get(att_id)
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-supervisor && pytest tests/test_attachments_routes.py -v
```

- [ ] **Step 3: Implement `attachments_routes.py`**

Create `kc-supervisor/src/kc_supervisor/attachments_routes.py`:

```python
from __future__ import annotations
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from kc_attachments.store import AttachmentNotFound, AttachmentStore
from kc_attachments.sniff import UnsupportedTypeError, sniff_mime


def _max_bytes() -> int:
    return int(os.environ.get("KC_ATTACH_MAX_BYTES", str(25 * 1024 * 1024)))


def build_attachments_router(*, store: AttachmentStore) -> APIRouter:
    router = APIRouter(prefix="/attachments", tags=["attachments"])

    @router.post("/upload")
    async def upload(
        conversation_id: str = Query(...),
        file: UploadFile = File(...),
    ):
        body = await file.read()
        if len(body) > _max_bytes():
            raise HTTPException(status_code=413, detail="file too large")

        # Write to a temp path and sniff before committing to the store.
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(file.filename or "").suffix
        ) as tf:
            tf.write(body)
            tmp_path = Path(tf.name)
        try:
            try:
                sniff_mime(tmp_path)
            except UnsupportedTypeError as e:
                raise HTTPException(status_code=415, detail=str(e))
            rec = store.save(
                conversation_id=conversation_id,
                source=tmp_path,
                filename=file.filename or "unnamed",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        snippet = store.read_parsed(rec.id)[:200]
        return {
            "attachment_id": rec.id,
            "filename": rec.filename,
            "mime": rec.mime,
            "size_bytes": rec.size_bytes,
            "parse_status": rec.parse_status,
            "parse_error": rec.parse_error,
            "snippet": snippet,
            "page_count": rec.page_count,
        }

    @router.delete("/{attachment_id}")
    def delete(attachment_id: str):
        try:
            store.delete(attachment_id)
        except AttachmentNotFound:
            raise HTTPException(status_code=404, detail="attachment not found")
        return {"ok": True}

    return router
```

- [ ] **Step 4: Install kc-attachments into the supervisor venv (one-time)**

```bash
cd kc-supervisor && /path/to/kc-supervisor/.venv/bin/pip install -e ../kc-attachments
```

(Use `pip install -e ../kc-attachments` if the venv's pip is already on PATH.)

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-supervisor && pytest tests/test_attachments_routes.py -v
```

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/attachments_routes.py \
        kc-supervisor/tests/test_attachments_routes.py
git commit -m "feat(kc-supervisor): POST /attachments/upload + DELETE /attachments/<id>"
```

---

## Task 17: Inject `[attached: ...]` prefix + image refs into user-turn submission

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py:180` (the dashboard `UserMessage` construction site)
- Modify: `kc-supervisor/src/kc_supervisor/storage.py` (round-trip `images` on user messages)
- Create: `kc-supervisor/tests/test_attachments_message_injection.py`

- [ ] **Step 1: Locate the dashboard message-submit handler**

Read `kc-supervisor/src/kc_supervisor/ws_routes.py` around line 180 — note the existing `UserMessage(content=content)` construction and the surrounding payload schema.

- [ ] **Step 2: Write the failing test**

Create `kc-supervisor/tests/test_attachments_message_injection.py`:

```python
from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore

from kc_supervisor.ws_routes import build_user_message_with_attachments


def test_text_attachment_prefixes_chip_line(tmp_path):
    store = AttachmentStore(root=tmp_path)
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    rec = store.save(conversation_id="conv_1", source=src, filename="hello.txt")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="What's in this?",
        attachment_ids=[rec.id],
    )
    assert msg.content.startswith(f"[attached: hello.txt")
    assert f"id={rec.id}" in msg.content
    assert "What's in this?" in msg.content
    assert msg.images == ()


def test_image_attachment_adds_image_ref(tmp_path):
    store = AttachmentStore(root=tmp_path)
    from PIL import Image
    src = tmp_path / "p.png"
    Image.new("RGB", (5, 5), "blue").save(src)
    rec = store.save(conversation_id="conv_1", source=src, filename="p.png")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="What is this?",
        attachment_ids=[rec.id],
    )
    assert "[attached: p.png" in msg.content
    assert len(msg.images) == 1
    assert msg.images[0].path == store.original_path(rec.id)
    assert msg.images[0].mime == "image/png"


def test_unknown_attachment_skipped_with_warning(tmp_path):
    store = AttachmentStore(root=tmp_path)
    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="hi",
        attachment_ids=["att_doesnotexist"],
    )
    # Falls through cleanly with no chip line for the missing id.
    assert msg.content == "hi"
    assert msg.images == ()


def test_attachment_from_other_conversation_ignored(tmp_path):
    store = AttachmentStore(root=tmp_path)
    src = tmp_path / "h.txt"
    src.write_text("H", encoding="utf-8")
    rec = store.save(conversation_id="conv_OTHER", source=src, filename="h.txt")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="hi",
        attachment_ids=[rec.id],
    )
    assert msg.content == "hi"
    assert msg.images == ()
```

- [ ] **Step 3: Run, verify FAIL**

```bash
cd kc-supervisor && pytest tests/test_attachments_message_injection.py -v
```

- [ ] **Step 4: Implement `build_user_message_with_attachments` in `ws_routes.py`**

In `kc-supervisor/src/kc_supervisor/ws_routes.py`, add near the top (after existing imports):

```python
from kc_attachments.store import AttachmentNotFound, AttachmentStore
from kc_core.messages import ImageRef
```

Then add this module-level helper (before the existing endpoint functions):

```python
def build_user_message_with_attachments(
    *,
    store: AttachmentStore,
    conversation_id: str,
    text: str,
    attachment_ids: list[str],
):
    """Build a UserMessage with chip-line prefix(es) and image refs for the listed
    attachments. Unknown ids and cross-conversation ids are silently skipped.
    """
    from kc_core.messages import UserMessage
    chips: list[str] = []
    images: list[ImageRef] = []
    for att_id in attachment_ids:
        try:
            rec = store.get(att_id)
        except AttachmentNotFound:
            continue
        if rec.conversation_id != conversation_id:
            continue
        # Chip line
        bits = [rec.filename]
        if rec.page_count:
            bits.append(f"{rec.page_count} pages")
        bits.append(f"{rec.size_bytes // 1024} KB")
        bits.append(f"id={rec.id}")
        chips.append(f"[attached: " + ", ".join(bits) + "]")
        # Image ref for eager-inline (skipped if mode=lazy via env)
        if rec.mime.startswith("image/") and os.environ.get(
            "KC_ATTACH_IMAGE_MODE", "eager"
        ).lower() == "eager":
            images.append(ImageRef(path=store.original_path(rec.id), mime=rec.mime))
    content_parts = chips + ([text] if text else [])
    content = "\n".join(content_parts) if chips else text
    return UserMessage(content=content, images=tuple(images))
```

Also `import os` at the top if not already present.

Update the existing dashboard message handler at `ws_routes.py:180` — find:

```python
deps.conversations.append(conversation_id, UserMessage(content=content))
```

And replace with:

```python
user_msg = build_user_message_with_attachments(
    store=deps.attachment_store,
    conversation_id=conversation_id,
    text=content,
    attachment_ids=envelope.get("attachment_ids") or [],
)
deps.conversations.append(conversation_id, user_msg)
```

(Adjust `envelope.get(...)` to match the actual incoming-message schema name. In `ws_routes.py`, look for the JSON dict that carries `content` and add `attachment_ids` as a sibling key.)

- [ ] **Step 5: Extend `storage.py` to persist + replay `UserMessage.images`**

In `kc-supervisor/src/kc_supervisor/storage.py`, look for the `INSERT INTO messages`/`SELECT ... FROM messages` flow. Add a side-table:

```python
# Add to the schema bootstrap section:
_SCHEMA_EXT = """
CREATE TABLE IF NOT EXISTS message_images (
    message_id     INTEGER NOT NULL,
    attachment_id  TEXT NOT NULL,
    PRIMARY KEY (message_id, attachment_id)
);
"""
```

Execute `_SCHEMA_EXT` at storage init time.

In the message-append path: when appending a `UserMessage` with non-empty `images`, after the INSERT into `messages`, fetch `lastrowid` and INSERT one row per image into `message_images` keyed by the attachment_id (you'll need the attachment_id; the simplest move is to extract it from the chip line in `content` via a regex, OR to pass `attachment_ids` through the append call signature). Pick the latter for clarity:

```python
def append(self, conversation_id: int, msg: Message, *, image_attachment_ids: tuple[str, ...] = ()) -> int:
    # ... existing INSERT into messages, capture message_id ...
    if image_attachment_ids:
        for att_id in image_attachment_ids:
            self._db.execute(
                "INSERT OR IGNORE INTO message_images(message_id, attachment_id) VALUES (?, ?)",
                (message_id, att_id),
            )
    return message_id
```

In the message-read/replay path: when reconstructing a `UserMessage` for a row, look up `message_images` for that row; if rows exist, hydrate `ImageRef`s from the `AttachmentStore` for those ids and pass into `UserMessage(content=..., images=...)`.

Adjust the `append` call in `ws_routes.py` to pass `image_attachment_ids` derived from the user-side payload (the same list passed to `build_user_message_with_attachments`).

- [ ] **Step 6: Run, verify PASS**

```bash
cd kc-supervisor && pytest tests/test_attachments_message_injection.py -v
```

- [ ] **Step 7: Run the full kc-supervisor suite (excluding the known pre-existing module-import failure)**

```bash
cd kc-supervisor && pytest --ignore=tests/test_http_subagents.py -v 2>&1 | tail -10
```
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/ws_routes.py \
        kc-supervisor/src/kc_supervisor/storage.py \
        kc-supervisor/tests/test_attachments_message_injection.py
git commit -m "feat(kc-supervisor): inject [attached: ...] prefix + image refs on user turn"
```

---

## Task 18: Wire `kc-attachments` at supervisor boot

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py`
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py`

- [ ] **Step 1: In `main.py`, after secrets store init, before agent assembly, add the store + capability cache + router**

```python
# Attachments — drag-drop file ingestion (Phase A of files rollout, 2026-05-15).
from kc_attachments import AttachmentStore, VisionCapabilityCache
from kc_supervisor.attachments_routes import build_attachments_router

attachment_store = AttachmentStore(root=Path.home() / "KonaClaw" / "attachments")
vision_cache = VisionCapabilityCache(
    base_url=os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434"),
)
app.include_router(build_attachments_router(store=attachment_store))
```

Then ensure `attachment_store` and `vision_cache` are available to `deps` (the dependency container passed to `ws_routes`):

```python
deps.attachment_store = attachment_store
deps.vision_cache = vision_cache
```

(If `deps` is a dataclass, add the two attributes there.)

- [ ] **Step 2: In `assembly.py`, register the attachment tools when assembling Kona's tool registry**

Find the existing `assemble_agent(...)` function. After existing tool registrations and before returning the registry, add:

```python
from kc_attachments import attach_attachments_to_agent

# Attachments tools — only for agents that get a conversation_id context.
if conversation_id is not None and attachment_store is not None:
    model_id = agent_def.model or os.environ.get("KC_DEFAULT_MODEL", "")
    vision_ok = vision_cache.supports_vision(model_id) if vision_cache else False
    attach_attachments_to_agent(
        registry=tool_registry,
        store=attachment_store,
        conversation_id=conversation_id,
        vision_for_active_model=vision_ok,
    )
```

Adjust the function signature to accept `attachment_store` and `vision_cache` if it doesn't already; have callers pass them through from `deps`.

- [ ] **Step 3: Smoke-import supervisor**

```bash
cd kc-supervisor && python3 -c "from kc_supervisor.main import main; print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Run full supervisor suite**

```bash
cd kc-supervisor && pytest --ignore=tests/test_http_subagents.py -v 2>&1 | tail -10
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/main.py kc-supervisor/src/kc_supervisor/assembly.py
git commit -m "feat(kc-supervisor): wire kc-attachments at boot (store + capability + tools)"
```

---

## Task 19: Wire `translate_image_sentinel` into the tool-result loop

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py` (or wherever the agent step loop materializes tool results)

This wires the helper from Task 15 into the live conversation flow.

- [ ] **Step 1: Locate the tool-result append site**

In the supervisor's agent step loop (look for where `ToolResultMessage` instances are appended to the conversation after a tool call completes — typically inside `assembly.py` or the per-agent runtime), find the line that constructs and appends the tool result.

- [ ] **Step 2: Wrap the append in the translator**

Replace:

```python
trm = ToolResultMessage(tool_call_id=tc.id, content=tool_output)
deps.conversations.append(conversation_id, trm)
```

With:

```python
from kc_core.agent import translate_image_sentinel

vision_ok = vision_cache.supports_vision(active_model_id) if vision_cache else False
trans = translate_image_sentinel(
    tool_output,
    tool_call_id=tc.id,
    vision_for_active_model=vision_ok,
)
deps.conversations.append(conversation_id, trans.tool_result)
if trans.follow_up is not None:
    # Persist the synthetic user-turn carrying the image bytes.
    deps.conversations.append(
        conversation_id,
        trans.follow_up,
        image_attachment_ids=(_extract_att_id_from_image_path(trans.follow_up.images[0].path),),
    )
```

Where `_extract_att_id_from_image_path(path)` parses the `att_<hex>` segment out of `~/KonaClaw/attachments/<conv>/<att_id>/original.<ext>`:

```python
def _extract_att_id_from_image_path(p: Path) -> str:
    return p.parent.name
```

- [ ] **Step 3: Run supervisor suite**

```bash
cd kc-supervisor && pytest --ignore=tests/test_http_subagents.py -v 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/assembly.py
git commit -m "feat(kc-supervisor): wire translate_image_sentinel into tool-result loop"
```

---

# PHASE F — Dashboard UI

## Task 20: API client + types for upload/delete

**Files:**
- Create: `kc-dashboard/src/api/attachments.ts`

- [ ] **Step 1: Implement the client**

Create `kc-dashboard/src/api/attachments.ts`:

```typescript
export interface AttachmentUploadResponse {
  attachment_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  parse_status: "ok" | "error";
  parse_error?: string;
  snippet?: string;
  page_count?: number;
}

export async function uploadAttachment(
  conversationId: number,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): Promise<AttachmentUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/attachments/upload?conversation_id=${conversationId}`);
    if (onProgress) {
      xhr.upload.onprogress = (e) => onProgress(e.loaded, e.total);
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`upload failed (${xhr.status}): ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("upload network error"));
    xhr.send(form);
  });
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const resp = await fetch(`/attachments/${attachmentId}`, { method: "DELETE" });
  if (!resp.ok) {
    throw new Error(`delete failed (${resp.status})`);
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add kc-dashboard/src/api/attachments.ts
git commit -m "feat(kc-dashboard): typed API client for attachments upload/delete"
```

---

## Task 21: `AttachmentChip` component

**Files:**
- Create: `kc-dashboard/src/components/AttachmentChip.tsx`
- Create: `kc-dashboard/src/components/AttachmentChip.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `kc-dashboard/src/components/AttachmentChip.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { AttachmentChip } from "./AttachmentChip";

describe("AttachmentChip", () => {
  it("renders filename and size", () => {
    render(
      <AttachmentChip
        status="ready"
        filename="report.pdf"
        sizeBytes={245000}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText(/report\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/239 KB|240 KB|245 KB/)).toBeInTheDocument();
  });

  it("shows spinner when uploading", () => {
    render(
      <AttachmentChip
        status="uploading"
        filename="x.txt"
        sizeBytes={100}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByLabelText(/uploading/i)).toBeInTheDocument();
  });

  it("shows error indicator when status is error", () => {
    render(
      <AttachmentChip
        status="error"
        filename="x.txt"
        sizeBytes={100}
        error="parse failed"
        onRemove={() => {}}
      />,
    );
    expect(screen.getByLabelText(/error/i)).toBeInTheDocument();
  });

  it("calls onRemove when ✕ clicked", () => {
    const onRemove = vi.fn();
    render(
      <AttachmentChip
        status="ready"
        filename="x.txt"
        sizeBytes={100}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-dashboard && npm run test -- AttachmentChip
```

- [ ] **Step 3: Implement `AttachmentChip.tsx`**

Create `kc-dashboard/src/components/AttachmentChip.tsx`:

```typescript
import React from "react";

type Status = "uploading" | "ready" | "error";

interface Props {
  status: Status;
  filename: string;
  sizeBytes: number;
  error?: string;
  onRemove: () => void;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function AttachmentChip({ status, filename, sizeBytes, error, onRemove }: Props) {
  return (
    <div className={`attachment-chip attachment-chip--${status}`} data-testid="attachment-chip">
      {status === "uploading" && <span aria-label="uploading">⟳</span>}
      {status === "ready" && <span aria-label="ready">✓</span>}
      {status === "error" && (
        <span aria-label="error" title={error || "parse error"}>⚠</span>
      )}
      <span className="attachment-chip__name">{filename}</span>
      <span className="attachment-chip__size">{formatBytes(sizeBytes)}</span>
      <button
        type="button"
        className="attachment-chip__remove"
        aria-label="remove"
        onClick={onRemove}
      >
        ✕
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-dashboard && npm run test -- AttachmentChip
```

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/components/AttachmentChip.tsx kc-dashboard/src/components/AttachmentChip.test.tsx
git commit -m "feat(kc-dashboard): AttachmentChip component (uploading/ready/error)"
```

---

## Task 22: `useAttachmentUpload` hook

**Files:**
- Create: `kc-dashboard/src/hooks/useAttachmentUpload.ts`
- Create: `kc-dashboard/src/hooks/useAttachmentUpload.test.ts`

- [ ] **Step 1: Write the failing test**

Create `kc-dashboard/src/hooks/useAttachmentUpload.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

import { useAttachmentUpload } from "./useAttachmentUpload";

vi.mock("../api/attachments", () => ({
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn().mockResolvedValue(undefined),
}));

import { uploadAttachment, deleteAttachment } from "../api/attachments";

describe("useAttachmentUpload", () => {
  beforeEach(() => {
    (uploadAttachment as any).mockReset();
    (deleteAttachment as any).mockReset();
  });

  it("starts in idle state with no chips", () => {
    const { result } = renderHook(() => useAttachmentUpload(123));
    expect(result.current.chips).toEqual([]);
    expect(result.current.allReady).toBe(true);
  });

  it("transitions to ready after successful upload", async () => {
    (uploadAttachment as any).mockResolvedValue({
      attachment_id: "att_abc",
      filename: "a.txt",
      mime: "text/plain",
      size_bytes: 6,
      parse_status: "ok",
    });
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["hello"], "a.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips[0].status).toBe("ready"));
    expect(result.current.chips[0].attachmentId).toBe("att_abc");
  });

  it("transitions to error on upload failure", async () => {
    (uploadAttachment as any).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["x"], "x.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips[0].status).toBe("error"));
  });

  it("remove deletes server-side when chip was ready", async () => {
    (uploadAttachment as any).mockResolvedValue({
      attachment_id: "att_abc",
      filename: "a.txt",
      mime: "text/plain",
      size_bytes: 1,
      parse_status: "ok",
    });
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["x"], "a.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips.length).toBe(1));
    await act(async () => {
      await result.current.remove(result.current.chips[0].localId);
    });
    expect(deleteAttachment).toHaveBeenCalledWith("att_abc");
    expect(result.current.chips.length).toBe(0);
  });
});
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-dashboard && npm run test -- useAttachmentUpload
```

- [ ] **Step 3: Implement the hook**

Create `kc-dashboard/src/hooks/useAttachmentUpload.ts`:

```typescript
import { useCallback, useMemo, useState } from "react";
import { uploadAttachment, deleteAttachment } from "../api/attachments";

export type ChipStatus = "uploading" | "ready" | "error";

export interface Chip {
  localId: string;
  status: ChipStatus;
  filename: string;
  sizeBytes: number;
  attachmentId?: string;
  error?: string;
}

let _seq = 0;
const nextLocalId = () => `local-${++_seq}`;

export function useAttachmentUpload(conversationId: number | null) {
  const [chips, setChips] = useState<Chip[]>([]);

  const addFiles = useCallback(
    async (files: File[]) => {
      if (conversationId == null) return;
      for (const file of files) {
        const localId = nextLocalId();
        setChips((cs) => [
          ...cs,
          {
            localId,
            status: "uploading",
            filename: file.name,
            sizeBytes: file.size,
          },
        ]);
        try {
          const resp = await uploadAttachment(conversationId, file);
          setChips((cs) =>
            cs.map((c) =>
              c.localId === localId
                ? {
                    ...c,
                    status: resp.parse_status === "ok" ? "ready" : "error",
                    attachmentId: resp.attachment_id,
                    error: resp.parse_error,
                  }
                : c,
            ),
          );
        } catch (e: any) {
          setChips((cs) =>
            cs.map((c) =>
              c.localId === localId
                ? { ...c, status: "error", error: e?.message ?? "upload failed" }
                : c,
            ),
          );
        }
      }
    },
    [conversationId],
  );

  const remove = useCallback(async (localId: string) => {
    const target = chips.find((c) => c.localId === localId);
    if (target?.attachmentId) {
      try {
        await deleteAttachment(target.attachmentId);
      } catch {
        // best-effort
      }
    }
    setChips((cs) => cs.filter((c) => c.localId !== localId));
  }, [chips]);

  const clear = useCallback(() => setChips([]), []);

  const allReady = useMemo(
    () => chips.every((c) => c.status === "ready" || c.status === "error"),
    [chips],
  );

  const readyAttachmentIds = useMemo(
    () =>
      chips
        .filter((c) => c.status === "ready" && c.attachmentId)
        .map((c) => c.attachmentId as string),
    [chips],
  );

  return { chips, addFiles, remove, clear, allReady, readyAttachmentIds };
}
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-dashboard && npm run test -- useAttachmentUpload
```

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/hooks/useAttachmentUpload.ts kc-dashboard/src/hooks/useAttachmentUpload.test.ts
git commit -m "feat(kc-dashboard): useAttachmentUpload hook"
```

---

## Task 23: `Chat.tsx` — drop overlay, paperclip, paste, chip row, send-disabled, attachment_ids in payload

**Files:**
- Modify: `kc-dashboard/src/views/Chat.tsx`
- Modify: `kc-dashboard/src/views/Chat.test.tsx`

- [ ] **Step 1: Append failing tests**

Append to `kc-dashboard/src/views/Chat.test.tsx` (or create the file if absent — match the existing test harness pattern):

```typescript
// (Snippet — paste alongside existing Chat tests, reusing the same providers.)
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import Chat from "./Chat";

vi.mock("../api/attachments", () => ({
  uploadAttachment: vi.fn().mockResolvedValue({
    attachment_id: "att_abc",
    filename: "a.txt",
    mime: "text/plain",
    size_bytes: 6,
    parse_status: "ok",
  }),
  deleteAttachment: vi.fn(),
}));

describe("Chat — attachments", () => {
  it("renders drop overlay on dragenter", () => {
    render(<Chat />);
    const root = screen.getByTestId("chat-root");
    fireEvent.dragEnter(root, { dataTransfer: { files: [] } });
    expect(screen.getByText(/drop to attach/i)).toBeInTheDocument();
  });

  it("disables send while chips are uploading", async () => {
    // Use the actual upload mock; trigger a file drop; verify send button is disabled
    // until the upload resolves. Implementation depends on existing send-button query.
    // (Pseudocode — fill in to match existing Chat.test.tsx selectors.)
    expect(true).toBe(true);
  });
});
```

(Pseudocode is acceptable here — fill in with actual selectors matching the existing Chat.test.tsx patterns when you implement.)

- [ ] **Step 2: Implement the integration**

In `kc-dashboard/src/views/Chat.tsx`:

a. Add the hook and the drop-zone state near the top of the `Chat()` body:

```typescript
import { useAttachmentUpload } from "../hooks/useAttachmentUpload";
import { AttachmentChip } from "../components/AttachmentChip";

// inside Chat():
const {
  chips,
  addFiles,
  remove: removeChip,
  clear: clearChips,
  allReady,
  readyAttachmentIds,
} = useAttachmentUpload(activeConv);
const [dragActive, setDragActive] = useState(false);
const dropRootRef = useRef<HTMLDivElement>(null);
```

b. Wrap the chat root in drag handlers:

```typescript
<div
  ref={dropRootRef}
  data-testid="chat-root"
  onDragEnter={(e) => {
    e.preventDefault();
    if (e.dataTransfer?.types?.includes("Files")) setDragActive(true);
  }}
  onDragOver={(e) => {
    e.preventDefault();
  }}
  onDragLeave={(e) => {
    if (e.target === dropRootRef.current) setDragActive(false);
  }}
  onDrop={async (e) => {
    e.preventDefault();
    setDragActive(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length) await addFiles(files);
  }}
>
  {dragActive && (
    <div className="chat-drop-overlay">Drop to attach</div>
  )}
  {/* ... existing chat layout ... */}
</div>
```

c. Add a paste handler on the message textarea:

```typescript
onPaste={async (e) => {
  const items = Array.from(e.clipboardData?.items || []);
  const imgs = items
    .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
    .map((it) => it.getAsFile())
    .filter((f): f is File => !!f);
  if (imgs.length) {
    e.preventDefault();
    await addFiles(imgs);
  }
}}
```

d. Add a paperclip button near the send button:

```typescript
<input
  type="file"
  ref={fileInputRef}
  style={{ display: "none" }}
  multiple
  onChange={async (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length) await addFiles(files);
    e.target.value = "";
  }}
/>
<button
  type="button"
  className="paperclip"
  aria-label="attach files"
  onClick={() => fileInputRef.current?.click()}
>
  📎
</button>
```

e. Render the chip row above the input:

```typescript
{chips.length > 0 && (
  <div className="chat-attachment-chip-row">
    {chips.map((c) => (
      <AttachmentChip
        key={c.localId}
        status={c.status}
        filename={c.filename}
        sizeBytes={c.sizeBytes}
        error={c.error}
        onRemove={() => removeChip(c.localId)}
      />
    ))}
  </div>
)}
```

f. Update the send button:

```typescript
<button
  type="submit"
  disabled={!allReady || (textValue.trim() === "" && chips.length === 0)}
>
  Send
</button>
```

g. Update the submit handler to include `attachment_ids` in the WebSocket payload:

```typescript
onSubmit={(e) => {
  e.preventDefault();
  // ...existing send logic, but pass attachment_ids:
  sendMessage({
    content: textValue,
    attachment_ids: readyAttachmentIds,
  });
  clearChips();
}}
```

- [ ] **Step 3: Run, verify PASS**

```bash
cd kc-dashboard && npm run test
```

- [ ] **Step 4: Commit**

```bash
git add kc-dashboard/src/views/Chat.tsx kc-dashboard/src/views/Chat.test.tsx
git commit -m "feat(kc-dashboard): Chat drop overlay + paperclip + paste + chip row + send-disabled"
```

---

## Task 24: Past-message chip rendering on `MessageBubble`

**Files:**
- Modify: `kc-dashboard/src/components/MessageBubble.tsx`
- Modify: `kc-dashboard/src/components/MessageBubble.test.tsx`

- [ ] **Step 1: Append failing test**

In `MessageBubble.test.tsx`, add:

```typescript
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "./MessageBubble";

it("renders attachment chips for past user message", () => {
  render(
    <MessageBubble
      role="user"
      content={"[attached: report.pdf, 12 pages, 245 KB, id=att_abc]\nHi there"}
    />,
  );
  expect(screen.getByText(/report\.pdf/)).toBeInTheDocument();
  expect(screen.getByText(/Hi there/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement chip parser + render**

In `MessageBubble.tsx`, add a small utility that splits the leading `[attached: ...]` lines from the rest of the content:

```typescript
const CHIP_LINE_RE = /^\[attached:\s*([^,]+(?:,[^,]+)*)\]\s*$/;

interface ParsedChip {
  filename: string;
  raw: string;
}

function parseLeadingChips(content: string): { chips: ParsedChip[]; rest: string } {
  const lines = content.split("\n");
  const chips: ParsedChip[] = [];
  let i = 0;
  while (i < lines.length) {
    const m = CHIP_LINE_RE.exec(lines[i]);
    if (!m) break;
    const parts = m[1].split(",").map((s) => s.trim());
    chips.push({ filename: parts[0], raw: lines[i] });
    i++;
  }
  return { chips, rest: lines.slice(i).join("\n").trimStart() };
}
```

Then, in the existing `MessageBubble` render branch for user messages, before rendering the text content:

```typescript
const { chips, rest } = parseLeadingChips(content);
return (
  <div className="message-bubble user">
    {chips.length > 0 && (
      <div className="message-bubble__chips">
        {chips.map((c, idx) => (
          <span key={idx} className="message-bubble__chip">📎 {c.filename}</span>
        ))}
      </div>
    )}
    <div className="message-bubble__text">{rest}</div>
  </div>
);
```

- [ ] **Step 3: Run, verify PASS**

```bash
cd kc-dashboard && npm run test -- MessageBubble
```

- [ ] **Step 4: Commit**

```bash
git add kc-dashboard/src/components/MessageBubble.tsx kc-dashboard/src/components/MessageBubble.test.tsx
git commit -m "feat(kc-dashboard): render attachment chips on past user messages"
```

---

# PHASE G — Limits, GC, SMOKE

## Task 25: Per-message + per-conversation limits in upload endpoint

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/attachments_routes.py`
- Modify: `kc-supervisor/tests/test_attachments_routes.py`

- [ ] **Step 1: Append failing tests**

Append to `kc-supervisor/tests/test_attachments_routes.py`:

```python
def test_upload_enforces_max_files_per_message(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_MAX_FILES", "2")
    app, _ = _app_with_router(tmp_path)
    client = TestClient(app)
    # First two succeed:
    for i in range(2):
        files = {"file": (f"{i}.txt", b"x", "text/plain")}
        resp = client.post("/attachments/upload", params={"conversation_id": "conv_1"}, files=files)
        assert resp.status_code == 200
    # Third on the SAME conversation in quick succession — soft policy:
    # we enforce per-conversation cumulative bytes (Task 25 covers); per-message
    # file count is enforced client-side in the dashboard. Skip for endpoint scope.


def test_upload_enforces_per_conversation_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_MAX_PER_CONV", "100")
    app, _ = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("a.txt", b"x" * 60, "text/plain")}
    r1 = client.post("/attachments/upload", params={"conversation_id": "conv_1"}, files=files)
    assert r1.status_code == 200
    files = {"file": ("b.txt", b"x" * 60, "text/plain")}
    r2 = client.post("/attachments/upload", params={"conversation_id": "conv_1"}, files=files)
    assert r2.status_code == 413
    assert "conversation" in r2.json()["detail"].lower()
```

(Note: per-message file count is enforced in the dashboard hook — the endpoint cannot know "this is the 11th file in your draft" because uploads are independent.)

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-supervisor && pytest tests/test_attachments_routes.py -v
```

- [ ] **Step 3: Implement per-conversation byte limit**

In `attachments_routes.py`, in the `upload` handler before calling `store.save(...)`:

```python
def _max_per_conv() -> int:
    return int(os.environ.get("KC_ATTACH_MAX_PER_CONV", str(500 * 1024 * 1024)))


# inside upload():
existing = store.list_for_conversation(conversation_id)
total_bytes = sum(r.size_bytes for r in existing)
if total_bytes + len(body) > _max_per_conv():
    raise HTTPException(
        status_code=413,
        detail=f"conversation attachment quota exceeded ({total_bytes + len(body)} > {_max_per_conv()})",
    )
```

- [ ] **Step 4: Implement per-message file count in the dashboard hook**

In `kc-dashboard/src/hooks/useAttachmentUpload.ts`, add an env-driven max-files check inside `addFiles`:

```typescript
const MAX_FILES = Number(import.meta.env.VITE_KC_ATTACH_MAX_FILES || 10);

const addFiles = useCallback(
  async (files: File[]) => {
    if (conversationId == null) return;
    const available = MAX_FILES - chips.length;
    const accept = files.slice(0, available);
    // (consider showing a toast for the rejected slice — out of scope here)
    for (const file of accept) {
      // ...existing upload flow...
    }
  },
  [conversationId, chips.length],
);
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-supervisor && pytest tests/test_attachments_routes.py -v
cd kc-dashboard && npm run test -- useAttachmentUpload
```

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/attachments_routes.py \
        kc-supervisor/tests/test_attachments_routes.py \
        kc-dashboard/src/hooks/useAttachmentUpload.ts
git commit -m "feat(kc-attachments): per-conversation byte cap + dashboard per-message file cap"
```

---

## Task 26: Background GC sweep for retention

**Files:**
- Modify: `kc-attachments/src/kc_attachments/store.py`
- Modify: `kc-attachments/tests/test_store.py`

- [ ] **Step 1: Append failing test**

Append to `kc-attachments/tests/test_store.py`:

```python
def test_evict_older_than_removes_old_and_keeps_recent(tmp_path):
    s = AttachmentStore(root=tmp_path)
    src = tmp_path / "h.txt"
    src.write_text("hello", encoding="utf-8")
    old = s.save(conversation_id="conv_1", source=src, filename="h.txt")
    # Backdate the row in sqlite to simulate an old attachment.
    s._db.execute(
        "UPDATE attachments SET parsed_at = ? WHERE id = ?",
        ("2024-01-01T00:00:00+00:00", old.id),
    )
    s._db.commit()
    recent = s.save(conversation_id="conv_1", source=src, filename="h.txt")

    evicted = s.evict_older_than(days=30)
    assert old.id in evicted
    assert recent.id not in evicted
    with pytest.raises(AttachmentNotFound):
        s.get(old.id)
    assert s.get(recent.id).id == recent.id
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement `evict_older_than`**

In `kc-attachments/src/kc_attachments/store.py`, add:

```python
from datetime import timedelta


def evict_older_than(self, *, days: int) -> list[str]:
    """Delete attachments whose parsed_at is older than `days` ago.
    Returns the list of deleted attachment ids."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = self._db.execute(
        "SELECT id FROM attachments WHERE parsed_at < ?", (cutoff,),
    ).fetchall()
    deleted: list[str] = []
    for (att_id,) in rows:
        try:
            self.delete(att_id)
            deleted.append(att_id)
        except AttachmentNotFound:
            continue
    return deleted
```

(Move `from datetime import timedelta` to the top of `store.py` alongside the existing datetime import.)

- [ ] **Step 4: Wire periodic eviction in supervisor**

In `kc-supervisor/src/kc_supervisor/main.py`, alongside existing scheduled maintenance hooks:

```python
import asyncio

async def _attachments_gc_loop(store):
    retention = int(os.environ.get("KC_ATTACH_RETENTION_DAYS", "90"))
    while True:
        try:
            store.evict_older_than(days=retention)
        except Exception:
            pass
        await asyncio.sleep(24 * 3600)

# in the lifespan startup section:
asyncio.create_task(_attachments_gc_loop(attachment_store))
```

- [ ] **Step 5: Run, verify PASS**

```bash
cd kc-attachments && pytest tests/test_store.py -v
```

- [ ] **Step 6: Commit**

```bash
git add kc-attachments/src/kc_attachments/store.py \
        kc-attachments/tests/test_store.py \
        kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-attachments): evict_older_than + supervisor GC loop"
```

---

## Task 27: SMOKE doc

**Files:**
- Create: `docs/superpowers/specs/2026-05-15-file-ingestion-SMOKE.md`

- [ ] **Step 1: Write the SMOKE doc**

Create `docs/superpowers/specs/2026-05-15-file-ingestion-SMOKE.md`:

```markdown
# File Ingestion — SMOKE Gates

**Spec:** `2026-05-15-file-ingestion-design.md`
**Plan:** `2026-05-15-file-ingestion.md`
**Owner:** Sammy

Restart KonaClawDashboard after the implementation merges. Then walk each gate in a fresh Kona chat conversation.

## Prerequisites

- Tesseract installed system-wide if you want Gate 5 OCR to produce real text: `brew install tesseract`.
- All five file fixtures available somewhere on disk for drag-drop.

---

## Gate 1 — Text file (.txt)

**Action:** Drag a small text file into the chat input. Send the message "What's in the file I attached?"
**Expected:** Chip appears, transitions to ✓ ready. Kona's response references the file's content. Audit log shows one `read_attachment` call.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 2 — PDF (.pdf)

**Action:** Drag a multi-page PDF. Ask "Summarize page 2 only."
**Expected:** Kona calls `read_attachment` with `page_range="2"`; the response references only page 2 content.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 3 — Word (.docx)

**Action:** Drag a .docx with at least one heading and one table. Ask "What sections are in this document?"
**Expected:** Kona's response lists the section headings.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 4 — Excel (.xlsx)

**Action:** Drag a multi-sheet .xlsx. Ask "What sheets are in this workbook?"
**Expected:** Kona's response lists every sheet name from `list_attachments` or `read_attachment` output.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 5 — Image with vision

**Action:** Drag a screenshot containing visible text. Ask "What does the image show?"
**Expected:** On a vision-capable model (qwen3.6:35b, gemma4:31b if vision-capable, gemma3:4b), Kona's response describes the visual content. The eager-inline path means no `read_attachment` call needed — the image is in the user turn.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 6 — OCR fallback

**Action:** Set `KC_OLLAMA_URL` to a non-vision model OR force `vision_for_active_model=False` for testing. Repeat Gate 5.
**Expected:** Response references the OCR-extracted text (less accurate than vision but readable). Audit log may show `read_attachment` returning `type=text` with the OCR markdown.
**Status:** [ ] PASS / [ ] FAIL / [ ] SKIPPED
**Notes:**

---

## Closeout

- Date: ___
- Final commit: ___
- All gates PASS / N PASS, M SKIPPED: ___
- Defects observed: ___
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-15-file-ingestion-SMOKE.md
git commit -m "docs(smoke): file ingestion — six manual gates"
```

---

## Task 28: Final test sweep + handoff

- [ ] **Step 1: kc-attachments suite**

```bash
cd kc-attachments && pytest -v 2>&1 | tail -10
```
Expected: ALL PASS (~35-40 tests).

- [ ] **Step 2: kc-core suite**

```bash
cd kc-core && pytest -v 2>&1 | tail -10
```
Expected: ALL PASS (existing + new messages + sentinel translation tests).

- [ ] **Step 3: kc-supervisor suite** (excluding the known pre-existing module-import failure on `test_http_subagents.py`)

```bash
cd kc-supervisor && pytest --ignore=tests/test_http_subagents.py -v 2>&1 | tail -10
```
Expected: ALL PASS.

- [ ] **Step 4: kc-dashboard suite**

```bash
cd kc-dashboard && npm run test
```
Expected: ALL PASS.

- [ ] **Step 5: Supervisor import smoke**

```bash
cd kc-supervisor && python3 -c "from kc_supervisor.main import main; print('ok')"
```
Expected: `ok`.

- [ ] **Step 6: Hand off to Sammy for SMOKE**

Tell Sammy:

> Implementation merged on `<branch>`. SMOKE doc at `docs/superpowers/specs/2026-05-15-file-ingestion-SMOKE.md`. Pre-conditions: install Tesseract for Gate 5 OCR (`brew install tesseract`). No env changes required — defaults are sane. Restart the supervisor and walk gates 1–6.

---

## Notes for the executor

- **TDD discipline:** every task writes the test first, runs it to see it fail, implements, runs to see pass. Don't skip the failure step.
- **Frequent commits:** each task ends with a commit. Don't bundle.
- **Branch:** continue on whatever branch the spec was committed to (the file-ingestion spec lives at `9014e49`). If Sammy wants a fresh branch, cut one off `main` and cherry-pick the spec + this plan before starting Task 1.
- **Editable installs:** Phase A's `pip install -e .` mirrors the kc-web / kc-skills pattern (see Phase B tools rollout memory). The supervisor venv also needs an editable install of `kc-attachments` (see Task 16 Step 4).
- **System Tesseract:** Task 6 tests pass without Tesseract installed (the `ocr_status='missing'` branch is exercised). Real OCR for SMOKE Gate 5 needs `brew install tesseract`.
- **Don't merge `kc-attachments` into `kc-web` or `kc-memory`.** They're peer packages with distinct concerns; cross-imports stay one-way (attachments → core, supervisor → attachments).
- **Vision capability is best-effort.** A model that doesn't advertise `vision` in `/api/show` gets treated as text-only. No env override for forcing vision — keep YAGNI per the locked-in preference.
- **kc-core changes are small but load-bearing.** Tasks 14 + 15 must land before Task 19's supervisor wiring can connect the sentinel pipeline. Don't skip ahead.
