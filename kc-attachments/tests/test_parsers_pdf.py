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
