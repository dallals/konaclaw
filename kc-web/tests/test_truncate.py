from kc_web.truncate import head_tail


def test_short_text_unchanged():
    text = "hello world"
    out, truncated = head_tail(text, cap_bytes=1024)
    assert out == text
    assert truncated is False


def test_exactly_at_cap_unchanged():
    text = "x" * 100
    out, truncated = head_tail(text, cap_bytes=100)
    assert out == text
    assert truncated is False


def test_long_text_head_and_tail():
    text = "A" * 500 + "B" * 500  # 1000 bytes
    out, truncated = head_tail(text, cap_bytes=200)
    assert truncated is True
    # head 100 bytes of A, tail 100 bytes of B, marker between.
    assert out.startswith("A" * 100)
    assert out.endswith("B" * 100)
    assert "[TRUNCATED" in out
    assert "800 bytes" in out  # 1000 - 200 = 800 dropped


def test_unicode_byte_length():
    # Emoji is 4 bytes in UTF-8. cap_bytes is bytes, not chars.
    text = "😀" * 1000  # 4000 bytes
    out, truncated = head_tail(text, cap_bytes=400)
    assert truncated is True
    assert "[TRUNCATED" in out


def test_marker_format():
    text = "x" * 1000
    out, truncated = head_tail(text, cap_bytes=200)
    assert truncated is True
    # Marker shape: \n\n...[TRUNCATED N bytes]...\n\n
    assert "...[TRUNCATED 800 bytes]..." in out


def test_empty_text():
    out, truncated = head_tail("", cap_bytes=100)
    assert out == ""
    assert truncated is False
