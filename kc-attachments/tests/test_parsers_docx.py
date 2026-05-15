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
