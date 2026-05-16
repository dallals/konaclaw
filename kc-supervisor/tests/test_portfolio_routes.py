import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kc_supervisor.portfolio_routes import build_portfolio_router


SAMPLE_PAYLOAD = {
    "total_value": 4_500_000.0,
    "total_gain": 2_000_000.0,
    "total_day_change": 50_000.0,
    "day_pct": 1.12,
    "holdings": [
        {"ticker": "AAPL", "value": 1_300_000.0, "day_change": 20_000.0, "gain_pct": 100.0},
        {"ticker": "NVDA", "value": 1_000_000.0, "day_change": 18_000.0, "gain_pct": 2500.0},
    ],
}


def _app_with_router(tmp_path: Path, *, cache_s: int = 60):
    app = FastAPI()
    router = build_portfolio_router(workspace_dir=tmp_path, cache_seconds=cache_s)
    app.include_router(router)
    return app


def _ok_completed(payload: dict) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = json.dumps(payload) + "\n"
    proc.stderr = ""
    return proc


def test_snapshot_returns_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok_completed(SAMPLE_PAYLOAD))
    client = TestClient(_app_with_router(tmp_path))
    r = client.get("/portfolio/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["payload"]["total_value"] == 4_500_000.0
    assert body["stale"] is False
    assert "cached_at" in body


def test_snapshot_cached_within_window(tmp_path, monkeypatch):
    call_count = {"n": 0}

    def fake_run(*a, **k):
        call_count["n"] += 1
        return _ok_completed(SAMPLE_PAYLOAD)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path, cache_s=60))
    client.get("/portfolio/snapshot")
    client.get("/portfolio/snapshot")
    assert call_count["n"] == 1


def test_snapshot_refresh_bypasses_cache(tmp_path, monkeypatch):
    call_count = {"n": 0}

    def fake_run(*a, **k):
        call_count["n"] += 1
        return _ok_completed(SAMPLE_PAYLOAD)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path, cache_s=60))
    client.get("/portfolio/snapshot")
    client.get("/portfolio/snapshot?refresh=true")
    assert call_count["n"] == 2


def test_snapshot_returns_error_with_last_good(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok_completed(SAMPLE_PAYLOAD)
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "boom"
        return proc

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path, cache_s=0))
    client.get("/portfolio/snapshot")
    r = client.get("/portfolio/snapshot")
    body = r.json()
    assert "error" in body
    assert body["last_good"]["total_value"] == 4_500_000.0


def test_snapshot_timeout_returns_error(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=5)
    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path, cache_s=0))
    r = client.get("/portfolio/snapshot")
    body = r.json()
    assert "error" in body


# ------------------------------------------------------------------ POST /sync


def _sync_summary(n_tickers: int = 17) -> dict:
    return {
        "synced_at": "2026-05-16T22:00:00+00:00",
        "user_email": "sammydallal@gmail.com",
        "tickers": n_tickers,
        "total_basis": 2_416_980.77,
        "file": "/workspace/holdings.json",
    }


def test_sync_runs_script_and_returns_summary(tmp_path, monkeypatch):
    summary = _sync_summary()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok_completed(summary))
    client = TestClient(_app_with_router(tmp_path))
    r = client.post("/portfolio/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["tickers"] == 17
    assert body["user_email"] == "sammydallal@gmail.com"


def test_sync_invalidates_snapshot_cache(tmp_path, monkeypatch):
    """A successful sync must drop the snapshot cache so the next /snapshot
    call re-runs portfolio.py against the freshly-synced holdings.json."""
    calls: list[str] = []

    def fake_run(cmd, *a, **k):
        script = cmd[1] if len(cmd) > 1 else ""
        calls.append(script)
        if "sync_holdings.py" in script:
            return _ok_completed(_sync_summary())
        return _ok_completed(SAMPLE_PAYLOAD)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path, cache_s=60))
    client.get("/portfolio/snapshot")
    client.post("/portfolio/sync")
    client.get("/portfolio/snapshot")
    portfolio_calls = [c for c in calls if "portfolio.py" in c]
    assert len(portfolio_calls) == 2  # cache was invalidated → re-fetched


def test_sync_failure_returns_502(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 3
        proc.stdout = ""
        proc.stderr = '{"error": "psql exit 2: connection refused"}'
        return proc

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path))
    r = client.post("/portfolio/sync")
    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]


def test_sync_timeout_returns_504(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=30)
    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(_app_with_router(tmp_path))
    r = client.post("/portfolio/sync")
    assert r.status_code == 504
