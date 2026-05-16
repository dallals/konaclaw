"""Smoke tests for the ported workspace scripts (Phase A of ZeroClaw port)."""
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent


def test_portfolio_silent_returns_json():
    r = subprocess.run(
        [sys.executable, "portfolio.py", "--silent"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    # portfolio.py emits a single JSON object on stdout in silent mode.
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert "total_value" in payload, f"keys: {list(payload.keys())}"
    assert "holdings" in payload
    assert isinstance(payload["holdings"], list)
    assert len(payload["holdings"]) > 0


def test_ytd_silent_returns_json():
    r = subprocess.run(
        [sys.executable, "ytd.py", "--silent"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    # ytd.py emits at minimum a total + holdings shape similar to portfolio.py.
    # Be loose on the exact key name — adjust if needed after seeing real output.
    assert "total_value" in payload or "ytd_pct" in payload or "holdings" in payload, (
        f"unexpected keys: {list(payload.keys())}"
    )


def test_stock_runs_for_known_ticker():
    r = subprocess.run(
        [sys.executable, "stock.py", "AAPL"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "AAPL" in r.stdout
