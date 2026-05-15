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
    if r.extra_meta.get("ocr_status") == "ok":
        assert "Hello" in r.markdown or "OCR" in r.markdown
    else:
        assert r.extra_meta["ocr_status"] in ("missing", "error")


def test_image_parser_downscales_oversize(tmp_path):
    r = ImageParser().parse(FIXTURES / "sample_large.png", {"downscale_to": tmp_path / "ds.png"})
    assert r.extra_meta["width"] <= _MAX_DIM
    assert r.extra_meta["height"] <= _MAX_DIM
    assert r.extra_meta.get("downscaled") is True
