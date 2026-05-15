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
