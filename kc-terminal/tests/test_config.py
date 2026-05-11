from pathlib import Path
from kc_terminal.config import TerminalConfig


def test_defaults():
    cfg = TerminalConfig.with_defaults()
    assert cfg.max_timeout_seconds == 600
    assert cfg.default_timeout_seconds == 60
    assert cfg.output_cap_bytes == 128 * 1024
    assert any("KonaClaw" in str(r) for r in cfg.roots)
    assert "ANTHROPIC_" in cfg.secret_prefixes


def test_from_env_overrides(monkeypatch, tmp_path):
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir(); r2.mkdir()
    monkeypatch.setenv("KC_TERMINAL_ROOTS", f"{r1}:{r2}")
    monkeypatch.setenv("KC_TERMINAL_DEFAULT_TIMEOUT", "30")
    monkeypatch.setenv("KC_TERMINAL_MAX_TIMEOUT", "120")
    monkeypatch.setenv("KC_TERMINAL_OUTPUT_CAP_BYTES", "2048")
    cfg = TerminalConfig.from_env()
    assert cfg.roots == (r1, r2)
    assert cfg.default_timeout_seconds == 30
    assert cfg.max_timeout_seconds == 120
    assert cfg.output_cap_bytes == 2048


def test_from_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.delenv("KC_TERMINAL_ROOTS", raising=False)
    monkeypatch.delenv("KC_TERMINAL_DEFAULT_TIMEOUT", raising=False)
    cfg = TerminalConfig.from_env()
    assert cfg.default_timeout_seconds == 60
    assert cfg.max_timeout_seconds == 600


def test_clamp_timeout():
    cfg = TerminalConfig.with_defaults()
    assert cfg.clamp_timeout(None) == cfg.default_timeout_seconds
    assert cfg.clamp_timeout(0) == 1
    assert cfg.clamp_timeout(-5) == 1
    assert cfg.clamp_timeout(10_000) == cfg.max_timeout_seconds
    assert cfg.clamp_timeout(45) == 45


def test_output_cap_bytes_falls_back(monkeypatch):
    monkeypatch.delenv("KC_TERMINAL_OUTPUT_CAP_BYTES", raising=False)
    cfg = TerminalConfig.from_env()
    assert cfg.output_cap_bytes == 128 * 1024
