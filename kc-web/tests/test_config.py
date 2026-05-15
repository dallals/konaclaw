import os
from pathlib import Path

import pytest

from kc_web.config import WebConfig


# --- defaults ---

def test_with_defaults_backend_ollama_only():
    cfg = WebConfig.with_defaults(backend="ollama", ollama_api_key="sk-o")
    assert cfg.backend == "ollama"
    assert cfg.ollama_api_key == "sk-o"
    assert cfg.firecrawl_api_key is None


def test_with_defaults_raised_caps():
    cfg = WebConfig.with_defaults(backend="ollama", ollama_api_key="sk-o")
    assert cfg.session_soft_cap == 100
    assert cfg.daily_hard_cap == 1000
    assert cfg.fetch_cap_bytes == 32 * 1024
    assert cfg.default_search_max_results == 10
    assert cfg.default_fetch_timeout_s == 30
    assert cfg.budget_db_path == Path.home() / ".kona" / "web_budget.sqlite"


# --- from_env: backend resolution ---

def test_from_env_defaults_backend_to_ollama(monkeypatch):
    monkeypatch.delenv("KC_WEB_BACKEND", raising=False)
    cfg = WebConfig.from_env(ollama_api_key="sk-o")
    assert cfg.backend == "ollama"


def test_from_env_honors_kc_web_backend_env(monkeypatch):
    monkeypatch.setenv("KC_WEB_BACKEND", "firecrawl")
    cfg = WebConfig.from_env(firecrawl_api_key="fc-key")
    assert cfg.backend == "firecrawl"


def test_from_env_explicit_backend_kwarg_wins(monkeypatch):
    monkeypatch.setenv("KC_WEB_BACKEND", "firecrawl")
    cfg = WebConfig.from_env(ollama_api_key="sk-o", backend="ollama")
    assert cfg.backend == "ollama"


def test_from_env_rejects_invalid_backend(monkeypatch):
    monkeypatch.setenv("KC_WEB_BACKEND", "bingo")
    with pytest.raises(ValueError, match="invalid"):
        WebConfig.from_env(ollama_api_key="sk-o")


# --- from_env: key validation ---

def test_from_env_ollama_without_key_raises():
    with pytest.raises(ValueError, match="ollama_api_key"):
        WebConfig.from_env(backend="ollama")


def test_from_env_firecrawl_without_key_raises():
    with pytest.raises(ValueError, match="firecrawl_api_key"):
        WebConfig.from_env(backend="firecrawl")


def test_from_env_whitespace_key_treated_as_missing():
    with pytest.raises(ValueError):
        WebConfig.from_env(backend="ollama", ollama_api_key="   ")


def test_from_env_both_keys_present_is_fine():
    cfg = WebConfig.from_env(
        backend="ollama",
        ollama_api_key="sk-o",
        firecrawl_api_key="fc-key",
    )
    assert cfg.backend == "ollama"
    assert cfg.ollama_api_key == "sk-o"
    assert cfg.firecrawl_api_key == "fc-key"


# --- from_env: env overrides for caps still work ---

def test_from_env_cap_overrides(monkeypatch):
    monkeypatch.setenv("KC_WEB_SESSION_SOFT_CAP", "200")
    monkeypatch.setenv("KC_WEB_DAILY_HARD_CAP", "5000")
    cfg = WebConfig.from_env(backend="ollama", ollama_api_key="sk-o")
    assert cfg.session_soft_cap == 200
    assert cfg.daily_hard_cap == 5000


def test_from_env_blocked_hosts_override(monkeypatch):
    monkeypatch.setenv("KC_WEB_BLOCKED_HOSTS", "evil.com, bad.example.net")
    cfg = WebConfig.from_env(backend="ollama", ollama_api_key="sk-o")
    assert cfg.extra_blocked_hosts == ("evil.com", "bad.example.net")
