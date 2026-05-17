from __future__ import annotations
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from . import ParseResult, REGISTRY


# A page extracts useful text when it has at least this many non-whitespace
# characters. Below this, we treat the page as a scanned image and try OCR.
_MIN_BODY_CHARS = 20

# Rasterizer scale — 2.0 ≈ 144 DPI, a good Tesseract sweet spot. Higher scales
# improve accuracy on small fonts at proportional CPU cost. Kept conservative
# so OCR latency stays usable on multi-page docs.
_OCR_RENDER_SCALE = 2.0


def _ocr_page_with_pdfium(source: Path, page_index: int) -> str:
    """Rasterize one PDF page via pypdfium2 and OCR it with Tesseract.

    Returns the stripped text. Empty string on any failure (missing
    dependency, render error, OCR miss) — the caller decides what to do.
    """
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        return ""
    try:
        pdf = pdfium.PdfDocument(str(source))
        try:
            page = pdf[page_index]
            try:
                bitmap = page.render(scale=_OCR_RENDER_SCALE)
                image = bitmap.to_pil()
            finally:
                page.close()
        finally:
            pdf.close()
    except Exception:
        return ""
    try:
        text = pytesseract.image_to_string(image)
    except Exception:
        return ""
    return text.strip()


class PdfParser:
    """Extracts text per page; emits `## Page N` headings.

    When pypdf returns near-empty text for a page (typical for scanned/image
    PDFs), falls back to rasterizing the page via pypdfium2 and running
    Tesseract OCR on it. The fallback is best-effort — if Tesseract or
    pypdfium2 are unavailable, the page just returns empty body.
    """

    def parse(self, source: Path, meta: dict[str, Any]) -> ParseResult:
        reader = PdfReader(str(source))
        parts: list[str] = []
        ocr_pages: list[int] = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < _MIN_BODY_CHARS:
                ocr_text = _ocr_page_with_pdfium(source, i - 1)
                if ocr_text:
                    text = ocr_text
                    ocr_pages.append(i)
            parts.append(f"## Page {i}\n\n{text}")
        extra: dict[str, Any] = {"page_count": len(reader.pages)}
        if ocr_pages:
            extra["ocr_pages"] = ocr_pages
        return ParseResult(markdown="\n\n".join(parts), extra_meta=extra)


REGISTRY["application/pdf"] = PdfParser()
