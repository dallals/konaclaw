"""Markdown → Telegram HTML conversion.

Telegram's HTML parse mode supports a small subset of tags: <b>, <i>, <u>, <s>,
<code>, <pre>, <a href>, <blockquote>, <tg-spoiler>. It does NOT support tables
or list tags — but plain '- ' bullets render fine without markup.

This converter is intentionally hand-rolled (no `markdown` lib dep) so we
control exactly which tags are emitted. If anything looks off, fall back to
plain text in the connector.
"""
from __future__ import annotations
import re
from html import escape as _html_escape


def md_to_telegram_html(text: str) -> str:
    """Convert Kona-flavored markdown to Telegram-compatible HTML.

    Handles: code blocks (```), inline code (`), bold (**), italic (_),
    links ([text](url)), and GFM tables (converted to readable plain text
    since Telegram has no table support).
    Lists ('- ' / '* ' / numbered) pass through unchanged.
    """
    text = _convert_tables(text)

    # Stash code blocks/spans BEFORE escaping so their content gets
    # html-escaped inside the placeholder, but the rest of the text doesn't
    # interpret backticks as anything special.
    code_blocks: list[str] = []

    def _save_code_block(m: re.Match) -> str:
        code = _html_escape(m.group(1), quote=False)
        code_blocks.append(f"<pre>{code}</pre>")
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```(?:[\w+-]*\n)?([\s\S]*?)```", _save_code_block, text)

    inline_codes: list[str] = []

    def _save_inline_code(m: re.Match) -> str:
        code = _html_escape(m.group(1), quote=False)
        inline_codes.append(f"<code>{code}</code>")
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _save_inline_code, text)

    # Escape remaining HTML special chars in the body.
    text = _html_escape(text, quote=False)

    # Bold: **text** → <b>text</b>
    text = re.sub(r"\*\*([^\*\n]+?)\*\*", r"<b>\1</b>", text)

    # Italic: _text_ → <i>text</i> (only when surrounded by non-word chars,
    # so we don't mangle snake_case identifiers).
    text = re.sub(r"(?<![A-Za-z0-9])_([^_\n]+?)_(?![A-Za-z0-9])", r"<i>\1</i>", text)

    # Links: [text](url) → <a href="url">text</a>
    text = re.sub(
        r"\[([^\]\n]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{_html_escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        text,
    )

    # Restore code placeholders.
    for i, html in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", html)
    for i, html in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", html)

    return text


def _convert_tables(text: str) -> str:
    """Replace GFM markdown tables with readable plain text.

    Two-column tables (the common 'label | value' shape Kona produces for
    reminder confirmations) become 'label: value' lines.
    Wider tables become 'cell | cell | cell' lines (no formatting).
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    sep_re = re.compile(r"^\s*\|?\s*[\-:]+\s*(\|\s*[\-:]+\s*)+\|?\s*$")
    while i < len(lines):
        is_table = (
            i + 1 < len(lines)
            and "|" in lines[i]
            and sep_re.match(lines[i + 1])
        )
        if not is_table:
            out.append(lines[i])
            i += 1
            continue

        header_cells = _split_table_row(lines[i])
        i += 2  # skip header + separator
        rows: list[list[str]] = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append(_split_table_row(lines[i]))
            i += 1

        # If the header row is empty (Kona sometimes emits this), use the
        # first data row's width to decide how to render.
        header_blank = all(not c.strip() for c in header_cells)
        effective_width = (
            len(rows[0]) if header_blank and rows else len(header_cells)
        )

        if effective_width == 2:
            for row in rows:
                if len(row) >= 2:
                    label = re.sub(r"\*\*(.*?)\*\*", r"\1", row[0]).strip()
                    value = row[1].strip()
                    out.append(f"{label}: {value}" if label else value)
        else:
            # Emit header row first if it has any non-blank cells.
            if not header_blank:
                out.append(" | ".join(c.strip() for c in header_cells))
            for row in rows:
                out.append(" | ".join(c.strip() for c in row))

    return "\n".join(out)


def _split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]
