from __future__ import annotations
from pathlib import Path
from typing import Any

from PIL import Image

from . import ParseResult, REGISTRY


_MAX_DIM = 4096  # px on either side; oversize images get downscaled when requested.


def _maybe_downscale(im: Image.Image, dest: Path) -> tuple[Image.Image, int, int, bool]:
    """If image exceeds _MAX_DIM, return a thumbnailed copy AND write it to dest."""
    orig_w, orig_h = im.size
    if orig_w <= _MAX_DIM and orig_h <= _MAX_DIM:
        return im, orig_w, orig_h, False
    im2 = im.copy()
    im2.thumbnail((_MAX_DIM, _MAX_DIM))
    im2.save(dest)
    return im2, im2.size[0], im2.size[1], True


def _try_ocr(im: Image.Image) -> tuple[str, str]:
    """Returns (markdown, status). Status: 'ok', 'missing', 'error'."""
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


for mime in ("image/png", "image/jpeg", "image/webp", "image/heic"):
    REGISTRY[mime] = ImageParser()
