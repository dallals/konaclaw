from __future__ import annotations


def head_tail(text: str, cap_bytes: int) -> tuple[str, bool]:
    """Truncate text to roughly cap_bytes by keeping the head and tail with a
    marker between. Returns (truncated_text, was_truncated).

    Byte-aware: cap_bytes is UTF-8 bytes, not characters. The output may slightly
    exceed cap_bytes due to the marker text. Marker format:
        \\n\\n...[TRUNCATED N bytes]...\\n\\n
    """
    encoded = text.encode("utf-8")
    n = len(encoded)
    if n <= cap_bytes:
        return text, False

    half = cap_bytes // 2
    head_bytes = encoded[:half]
    tail_bytes = encoded[-half:]
    dropped = n - len(head_bytes) - len(tail_bytes)

    # Decode each half, ignoring partial utf-8 sequences at the boundary.
    head = head_bytes.decode("utf-8", errors="ignore")
    tail = tail_bytes.decode("utf-8", errors="ignore")

    marker = f"\n\n...[TRUNCATED {dropped} bytes]...\n\n"
    return head + marker + tail, True
