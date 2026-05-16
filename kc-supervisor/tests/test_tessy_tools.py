import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kc_supervisor.tessy_tools import build_tessy_tools


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


def _ok(stdout: str) -> MagicMock:
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = 0
    p.stdout = stdout
    p.stderr = ""
    return p


@pytest.mark.asyncio
async def test_tesla_price_shells_out_and_returns_stdout(workspace, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok('{"monthly": 612.34}'))
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    impl = tools["tesla.price"].impl
    out = await impl(trim="rwd", zip="95128", months=72, down=7000)
    parsed = json.loads(out)
    assert parsed["monthly"] == 612.34


@pytest.mark.asyncio
async def test_tesla_price_accepts_nlp_arg(workspace, monkeypatch):
    captured = {}
    def fake_run(args, **kwargs):
        captured["args"] = args
        return _ok('{"monthly": 500.00}')
    monkeypatch.setattr(subprocess, "run", fake_run)
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    out = await tools["tesla.price"].impl(nlp="Model Y RWD 95128 72 months 7k down")
    parsed = json.loads(out)
    assert parsed["monthly"] == 500.00
    # The --nlp flag should be passed through.
    assert "--nlp" in captured["args"]


@pytest.mark.asyncio
async def test_tesla_update_pricing_returns_diff(workspace, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _ok('{"pending": true, "diff": {"models.my.price": [39900, 41990]}}'),
    )
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    out = await tools["tesla.update_pricing"].impl(nlp="raise Model Y RWD to $41,990")
    parsed = json.loads(out)
    assert parsed["pending"] is True
    assert "models.my.price" in parsed["diff"]


@pytest.mark.asyncio
async def test_tesla_confirm_pricing_runs_confirm_flag(workspace, monkeypatch):
    captured = {}
    def fake_run(args, **kwargs):
        captured["args"] = args
        return _ok('{"ok": true, "status": "applied"}')
    monkeypatch.setattr(subprocess, "run", fake_run)
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    out = await tools["tesla.confirm_pricing"].impl()
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert "--confirm" in captured["args"]


@pytest.mark.asyncio
async def test_tesla_update_offers_from_image_resolves_attachment(workspace, monkeypatch):
    fake_store = MagicMock()
    fake_path = workspace / "fake.png"
    fake_path.write_bytes(b"\x89PNG\r\n")
    fake_store.original_path.return_value = fake_path
    captured = {}
    def fake_run(args, **kwargs):
        captured["args"] = args
        return _ok('{"ok": true}')
    monkeypatch.setattr(subprocess, "run", fake_run)
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=fake_store)
    out = await tools["tesla.update_offers_from_image"].impl(attachment_id="att_abc")
    parsed = json.loads(out)
    assert parsed["ok"] is True
    fake_store.original_path.assert_called_once_with("att_abc")
    # The resolved file path was passed to the subprocess.
    assert str(fake_path) in captured["args"]


@pytest.mark.asyncio
async def test_tesla_update_offers_returns_error_when_no_attachment_store(workspace):
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    out = await tools["tesla.update_offers_from_image"].impl(attachment_id="att_abc")
    parsed = json.loads(out)
    assert "error" in parsed


def test_build_tessy_tools_returns_4_named_tools(workspace):
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    assert set(tools.keys()) == {
        "tesla.price", "tesla.update_pricing",
        "tesla.confirm_pricing", "tesla.update_offers_from_image",
    }
