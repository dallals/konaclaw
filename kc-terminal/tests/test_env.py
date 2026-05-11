from kc_terminal.env import build_child_env, DEFAULT_SECRET_PREFIXES


def test_strips_default_secret_prefixes():
    parent = {
        "ANTHROPIC_API_KEY": "secret",
        "SUPABASE_KEY": "secret",
        "KC_SKILL_DIR": "/tmp",
        "OPENAI_API_KEY": "secret",
        "TELEGRAM_BOT_TOKEN": "secret",
        "ZAPIER_NLA_KEY": "secret",
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
    }
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert "ANTHROPIC_API_KEY" not in out
    assert "SUPABASE_KEY" not in out
    assert "KC_SKILL_DIR" not in out
    assert "OPENAI_API_KEY" not in out
    assert "TELEGRAM_BOT_TOKEN" not in out
    assert "ZAPIER_NLA_KEY" not in out
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/Users/x"


def test_preserves_safe_vars():
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
        "USER": "x",
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "xterm",
        "TMPDIR": "/tmp",
        "SSH_AUTH_SOCK": "/x.sock",
        "AWS_ACCESS_KEY_ID": "AKIA...",
    }
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert out == parent


def test_preserves_github_token_by_name():
    parent = {"GITHUB_TOKEN": "ghp_xxx", "PATH": "/usr/bin"}
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert out["GITHUB_TOKEN"] == "ghp_xxx"


def test_empty_parent_yields_empty():
    assert build_child_env({}, DEFAULT_SECRET_PREFIXES) == {}


def test_case_sensitivity_documented():
    # Prefix match is exact (case-sensitive). Lower/mixed-case keys are preserved.
    parent = {"anthropic_lower": "x", "Anthropic_Mixed": "x", "ANTHROPIC_REAL": "secret"}
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert "anthropic_lower" in out
    assert "Anthropic_Mixed" in out
    assert "ANTHROPIC_REAL" not in out


def test_custom_prefix_list():
    parent = {"MYAPP_KEY": "secret", "PATH": "/usr/bin"}
    out = build_child_env(parent, ("MYAPP_",))
    assert "MYAPP_KEY" not in out
    assert out["PATH"] == "/usr/bin"
