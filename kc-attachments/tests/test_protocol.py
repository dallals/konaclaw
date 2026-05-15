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

    fp = FakeParser()
    out = fp.parse(Path("/tmp/anything"), {})
    assert isinstance(out, ParseResult)
