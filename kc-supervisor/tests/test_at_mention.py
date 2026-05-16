from kc_supervisor.at_mention import parse_at_mention


def test_simple_mention():
    assert parse_at_mention("@tessy what's the Model Y price?") == (
        "tessy", "what's the Model Y price?",
    )


def test_no_at_prefix_returns_none():
    assert parse_at_mention("tessy what's the price?") is None
    assert parse_at_mention("ask @tessy something") is None


def test_at_mention_without_task_returns_none():
    assert parse_at_mention("@tessy") is None
    assert parse_at_mention("@tessy   ") is None


def test_uppercase_template_name_rejected():
    """Template names are lowercase-kebab; @Tessy should not match."""
    assert parse_at_mention("@Tessy hi") is None


def test_kebab_name_accepted():
    assert parse_at_mention("@web-researcher find X") == ("web-researcher", "find X")


def test_multiline_task_preserved():
    out = parse_at_mention("@tessy line1\nline2\nline3")
    assert out == ("tessy", "line1\nline2\nline3")


def test_leading_whitespace_tolerated():
    assert parse_at_mention("  @tessy hi") == ("tessy", "hi")


def test_empty_string():
    assert parse_at_mention("") is None
