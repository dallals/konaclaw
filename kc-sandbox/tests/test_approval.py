import io

from kc_sandbox.approval import InteractiveApproval


def test_y_allows():
    cb = InteractiveApproval(in_stream=io.StringIO("y\n"), out_stream=io.StringIO())
    allowed, reason = cb("kc", "file.delete", {"share": "docs", "relpath": "x.md"})
    assert allowed is True
    assert reason is None


def test_yes_full_word_allows():
    cb = InteractiveApproval(in_stream=io.StringIO("yes\n"), out_stream=io.StringIO())
    allowed, _ = cb("kc", "file.delete", {})
    assert allowed is True


def test_n_denies():
    cb = InteractiveApproval(in_stream=io.StringIO("n\n"), out_stream=io.StringIO())
    allowed, reason = cb("kc", "file.delete", {"share": "docs", "relpath": "x.md"})
    assert allowed is False
    assert "declined" in (reason or "")


def test_blank_denies():
    """Blank input defaults to deny — privacy by default."""
    cb = InteractiveApproval(in_stream=io.StringIO("\n"), out_stream=io.StringIO())
    allowed, _ = cb("kc", "file.delete", {})
    assert allowed is False


def test_eof_denies():
    """Closed stdin (Ctrl-D before any answer) must deny, not crash."""
    cb = InteractiveApproval(in_stream=io.StringIO(""), out_stream=io.StringIO())
    allowed, _ = cb("kc", "file.delete", {})
    assert allowed is False


def test_prompt_shows_agent_tool_and_args():
    out = io.StringIO()
    cb = InteractiveApproval(in_stream=io.StringIO("n\n"), out_stream=out)
    cb("KonaClaw", "file.delete", {"share": "docs", "relpath": "secrets.txt"})
    text = out.getvalue()
    assert "KonaClaw" in text
    assert "file.delete" in text
    assert "secrets.txt" in text
    assert "Allow?" in text
