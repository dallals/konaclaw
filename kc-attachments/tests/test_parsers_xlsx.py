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
