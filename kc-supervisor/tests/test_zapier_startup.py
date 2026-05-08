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


class _FakeSecretsStore:
    def __init__(self, secrets: dict | None = None):
        self._secrets = secrets or {}

    def load(self):
        return dict(self._secrets)


class _FakeDeps:
    """Minimal Deps duck-type for the helper. `_maybe_register_zapier` reads
    `mcp_manager`, `secrets_store`, and `registry`."""
    def __init__(self, *, mcp_manager, registry, secrets_store=None):
        self.mcp_manager = mcp_manager
        self.registry = registry
        self.secrets_store = secrets_store


def _run(coro):
    return asyncio.run(coro)


def test_zapier_startup_skips_when_kc_zapier_not_importable(monkeypatch):
    """If kc_zapier import fails, helper returns silently and does not reload."""
    monkeypatch.setitem(sys.modules, "kc_zapier", None)
    monkeypatch.setitem(sys.modules, "kc_zapier.config", None)
    monkeypatch.setitem(sys.modules, "kc_zapier.register", None)

    registry = _FakeRegistry()
    deps = _FakeDeps(
        mcp_manager=MagicMock(),
        registry=registry,
        secrets_store=_FakeSecretsStore({"zapier_api_key": "fake-key"}),
    )

    _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0


def test_zapier_startup_skips_when_no_api_key():
    """When secrets store lacks `zapier_api_key`, silent skip."""
    registry = _FakeRegistry()
    deps = _FakeDeps(
        mcp_manager=MagicMock(),
        registry=registry,
        secrets_store=_FakeSecretsStore({}),
    )

    _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0


def test_zapier_startup_calls_register_and_reloads(monkeypatch):
    """When the secrets store has a key and register succeeds, registry.load_all() runs."""
    from kc_zapier import register as zap_register_mod
    from kc_zapier.config import ZapierConfig

    calls = []

    async def _fake_register(manager, cfg):
        calls.append((manager, cfg))
        return []

    monkeypatch.setattr(zap_register_mod, "register_zapier_mcp", _fake_register)

    registry = _FakeRegistry()
    fake_mgr = MagicMock()
    deps = _FakeDeps(
        mcp_manager=fake_mgr,
        registry=registry,
        secrets_store=_FakeSecretsStore({"zapier_api_key": "fake-key"}),
    )

    _run(_maybe_register_zapier(deps))

    assert len(calls) == 1
    assert calls[0][0] is fake_mgr
    assert isinstance(calls[0][1], ZapierConfig)
    assert calls[0][1].api_key == "fake-key"
    assert registry.load_all_calls == 1


def test_zapier_startup_warns_and_skips_reload_on_register_failure(monkeypatch, caplog):
    """If register_zapier_mcp raises, we log a warning and do NOT reload."""
    from kc_zapier import register as zap_register_mod

    async def _fake_register_raises(manager, cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(zap_register_mod, "register_zapier_mcp", _fake_register_raises)

    registry = _FakeRegistry()
    deps = _FakeDeps(
        mcp_manager=MagicMock(),
        registry=registry,
        secrets_store=_FakeSecretsStore({"zapier_api_key": "fake-key"}),
    )

    with caplog.at_level("WARNING"):
        _run(_maybe_register_zapier(deps))

    assert registry.load_all_calls == 0
    assert any("zapier MCP registration failed" in rec.message for rec in caplog.records)


def test_zapier_startup_skips_when_no_mcp_manager():
    """When deps.mcp_manager is None, helper returns silently."""
    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=None, registry=registry,
                     secrets_store=_FakeSecretsStore({"zapier_api_key": "k"}))
    _run(_maybe_register_zapier(deps))
    assert registry.load_all_calls == 0


def test_zapier_startup_skips_when_no_secrets_store():
    """When deps.secrets_store is None, helper returns silently."""
    registry = _FakeRegistry()
    deps = _FakeDeps(mcp_manager=MagicMock(), registry=registry, secrets_store=None)
    _run(_maybe_register_zapier(deps))
    assert registry.load_all_calls == 0
