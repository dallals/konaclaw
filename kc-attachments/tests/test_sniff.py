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
