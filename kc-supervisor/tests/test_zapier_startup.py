from __future__ import annotations
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

from kc_supervisor.service import _maybe_register_zapier


class _FakeRegistry:
    def __init__(self):
        self.load_all_calls = 0

    def load_all(self):
        self.load_all_calls += 1


class _FakeDeps:
    """Minimal Deps duck-type for the helper. We can't reuse the real Deps
    fixture here because `_maybe_register_zapier` only touches `mcp_manager`
    and `registry`."""
    def __init__(self, *, mcp_manager, registry):
        self.mcp_manager = mcp_manager
        self.registry = registry


def _run(coro):
    return asyncio.run(coro)


def test_zapier_startup_skips_when_kc_zapier_not_importable(monkeypatch):
    """If kc_zapier import fails, helper returns silently and does not reload."""
    # Force import to raise ImportError by inserting None into sys.modules.
    monkeypatch.setitem(sys.modules, "kc_zapier", None)
    monkeypatch.setitem(sys.modules, "kc_zapier.config", None)
    monkeypatch.setitem(sys.modules, "kc_zapier.register", None)

    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=MagicMock(), registry=registry)

    _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0


def test_zapier_startup_skips_when_no_api_key(monkeypatch):
    """If load_config raises KeyError (zapier_api_key missing), silent skip."""
    from kc_zapier import config as zap_config_mod

    def _raise_keyerror():
        raise KeyError("zapier_api_key missing from secrets.yaml")

    monkeypatch.setattr(zap_config_mod, "load_config", _raise_keyerror)

    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=MagicMock(), registry=registry)

    _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0


def test_zapier_startup_calls_register_and_reloads(monkeypatch):
    """When config loads fine and register succeeds, registry.load_all() runs."""
    from kc_zapier import config as zap_config_mod
    from kc_zapier import register as zap_register_mod

    fake_cfg = zap_config_mod.ZapierConfig(api_key="fake-key")
    monkeypatch.setattr(zap_config_mod, "load_config", lambda: fake_cfg)

    calls = []

    async def _fake_register(manager, cfg):
        calls.append((manager, cfg))
        return []

    monkeypatch.setattr(zap_register_mod, "register_zapier_mcp", _fake_register)

    registry = _FakeRegistry()
    fake_mgr = MagicMock()
    deps = _FakeDeps(mcp_manager=fake_mgr, registry=registry)

    _run(_maybe_register_zapier(deps))

    assert len(calls) == 1
    assert calls[0][0] is fake_mgr
    assert calls[0][1] is fake_cfg
    assert registry.load_all_calls == 1


def test_zapier_startup_warns_and_skips_reload_on_register_failure(monkeypatch, caplog):
    """If register_zapier_mcp raises, we log a warning and do NOT reload."""
    from kc_zapier import config as zap_config_mod
    from kc_zapier import register as zap_register_mod

    fake_cfg = zap_config_mod.ZapierConfig(api_key="fake-key")
    monkeypatch.setattr(zap_config_mod, "load_config", lambda: fake_cfg)

    async def _fake_register_raises(manager, cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(zap_register_mod, "register_zapier_mcp", _fake_register_raises)

    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=MagicMock(), registry=registry)

    with caplog.at_level("WARNING"):
        _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0
    # Warning should have been logged.
    assert any("zapier MCP registration failed" in rec.message for rec in caplog.records)


def test_zapier_startup_skips_when_no_mcp_manager():
    """When deps.mcp_manager is None, helper returns silently."""
    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=None, registry=registry)
    _run(_maybe_register_zapier(deps))
    assert registry.load_all_calls == 0
