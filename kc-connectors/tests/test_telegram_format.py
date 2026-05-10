"""Tests for the markdown → Telegram HTML converter."""
from __future__ import annotations
from kc_connectors._telegram_format import md_to_telegram_html


def test_bold_converts_to_b_tag():
    assert md_to_telegram_html("**Reminder set!**") == "<b>Reminder set!</b>"


def test_italic_converts_to_i_tag():
    assert md_to_telegram_html("hello _world_") == "hello <i>world</i>"


def test_italic_does_not_break_snake_case():
    # _foo_bar_ should NOT become <i>foo</i>bar_ — underscores inside identifiers
    # don't trigger italic.
    out = md_to_telegram_html("see foo_bar_baz here")
    assert out == "see foo_bar_baz here"


def test_inline_code_uses_code_tag():
    assert md_to_telegram_html("run `pytest -v`") == "run <code>pytest -v</code>"


def test_code_block_uses_pre_tag():
    out = md_to_telegram_html("```\nhello\n```")
    assert "<pre>" in out and "</pre>" in out
    assert "hello" in out


def test_code_block_with_language_strips_language_marker():
    out = md_to_telegram_html("```python\nprint('hi')\n```")
    assert "<pre>" in out
    assert "print('hi')" in out  # quotes stay literal; only <>& need escaping
    assert "python" not in out  # the lang marker isn't emitted


def test_link_converts_to_a_tag():
    out = md_to_telegram_html("[Anthropic](https://anthropic.com)")
    assert out == '<a href="https://anthropic.com">Anthropic</a>'


def test_html_special_chars_are_escaped():
    out = md_to_telegram_html("a < b & c > d")
    assert out == "a &lt; b &amp; c &gt; d"


def test_two_column_table_becomes_label_value_pairs():
    md = (
        "| | |\n"
        "|---|---|\n"
        "| **What** | Take Kona's bed to Turlock |\n"
        "| **When** | Today at 6:24 AM |\n"
    )
    out = md_to_telegram_html(md)
    # Bold markers in the label cell are stripped by the table converter
    # before HTML rendering; quotes pass through (quote=False on escape).
    assert "What: Take Kona's bed to Turlock" in out
    assert "When: Today at 6:24 AM" in out


def test_two_column_table_with_real_header():
    md = (
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| name | Kona |\n"
        "| age  | 5 |\n"
    )
    out = md_to_telegram_html(md)
    assert "name: Kona" in out
    assert "age: 5" in out


def test_three_column_table_uses_pipe_separator():
    md = (
        "| A | B | C |\n"
        "|---|---|---|\n"
        "| 1 | 2 | 3 |\n"
        "| 4 | 5 | 6 |\n"
    )
    out = md_to_telegram_html(md)
    assert "1 | 2 | 3" in out
    assert "4 | 5 | 6" in out


def test_lists_pass_through():
    md = "- item one\n- item two"
    assert md_to_telegram_html(md) == "- item one\n- item two"


def test_list_with_bold_inside():
    md = "- **What:** Take Kona's bed to Turlock\n- **When:** 11:20 PM"
    out = md_to_telegram_html(md)
    assert "- <b>What:</b> Take Kona's bed to Turlock" in out
    assert "- <b>When:</b> 11:20 PM" in out


def test_full_reminder_message():
    """The exact shape from Sammy's screenshot — verifies the end-to-end path."""
    md = (
        "✅ **Reminder set!**\n"
        "\n"
        "- **What:** Take Kona's bed to Turlock\n"
        "- **When:** Tonight at 11:20 PM (in 5 minutes)"
    )
    out = md_to_telegram_html(md)
    assert "✅ <b>Reminder set!</b>" in out
    assert "<b>What:</b>" in out
    assert "<b>When:</b>" in out


def test_code_block_content_is_escaped():
    """HTML special chars inside code blocks must be escaped so Telegram
    doesn't try to parse them as tags."""
    out = md_to_telegram_html("```\n<script>alert('x')</script>\n```")
    assert "<pre>" in out
    assert "&lt;script&gt;" in out
    assert "&lt;/script&gt;" in out


def test_inline_code_content_is_escaped():
    out = md_to_telegram_html("use `<div>` here")
    assert "<code>&lt;div&gt;</code>" in out


def test_empty_string():
    assert md_to_telegram_html("") == ""


def test_plain_text_unchanged():
    assert md_to_telegram_html("just a plain message") == "just a plain message"
