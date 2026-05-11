import os
from pathlib import Path

import pytest

from kc_web.config import WebConfig


def test_with_defaults():
    cfg = WebConfig.with_defaults(api_key="sk-test")
    assert cfg.firecrawl_api_key == "sk-test"
    assert cfg.session_soft_cap == 50
    assert cfg.daily_hard_cap == 500
    assert cfg.fetch_cap_bytes == 32 * 1024
    assert cfg.default_search_max_results == 10
    assert cfg.default_fetch_timeout_s == 30
    assert cfg.budget_db_path == Path.home() / ".kona" / "web_budget.sqlite"
    assert cfg.extra_blocked_hosts == ()
    assert isinstance(cfg.session_id, str) and len(cfg.session_id) > 0


def test_from_env_requires_nonempty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        WebConfig.from_env(api_key="")
    with pytest.raises(ValueError, match="api_key"):
        WebConfig.from_env(api_key="   ")


def test_from_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("KC_WEB_SESSION_SOFT_CAP", "7")
    monkeypatch.setenv("KC_WEB_DAILY_HARD_CAP", "77")
    monkeypatch.setenv("KC_WEB_FETCH_CAP_BYTES", "1024")
    monkeypatch.setenv("KC_WEB_SEARCH_DEFAULT_N", "3")
    monkeypatch.setenv("KC_WEB_FETCH_DEFAULT_TIMEOUT", "45")
    monkeypatch.setenv("KC_WEB_BUDGET_DB", str(tmp_path / "b.sqlite"))
    monkeypatch.setenv("KC_WEB_BLOCKED_HOSTS", "evil.com,bad.example")
    cfg = WebConfig.from_env(api_key="sk-env")
    assert cfg.firecrawl_api_key == "sk-env"
    assert cfg.session_soft_cap == 7
    assert cfg.daily_hard_cap == 77
    assert cfg.fetch_cap_bytes == 1024
    assert cfg.default_search_max_results == 3
    assert cfg.default_fetch_timeout_s == 45
    assert cfg.budget_db_path == tmp_path / "b.sqlite"
    assert cfg.extra_blocked_hosts == ("evil.com", "bad.example")


def test_from_env_blocked_hosts_strip_whitespace(monkeypatch):
    monkeypatch.setenv("KC_WEB_BLOCKED_HOSTS", " evil.com ,  bad.example,")
    cfg = WebConfig.from_env(api_key="k")
    assert cfg.extra_blocked_hosts == ("evil.com", "bad.example")
