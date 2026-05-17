# ZeroClaw Port Milestone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port three features from Sammy's ZeroClaw (Raspberry Pi) project into KonaClaw: the money-manager scripts (Phase A), a dashboard portfolio widget (Phase B), and Tessy — a Tesla-specialist subagent (Phase C).

**Architecture:** One milestone, three independent phases. Phase A copies + sanitizes 4 Python scripts into a new `workspace/` directory at the repo root, plus a SKILL.md so Kona uses them. Phase B adds a supervisor HTTP route and a dashboard widget that reads the portfolio. Phase C copies + sanitizes 4 Tesla scripts, adds 4 supervisor tools wrapping them, and creates a Tessy subagent template. ZeroClaw stays untouched — the port is one-way via SSH.

**Tech Stack:** Python 3.11+ (workspace scripts use stdlib only — `urllib`, `json`, `numpy` for monte_carlo; tesla scripts use httpx via stdlib `urllib`); React 18 + TypeScript + Vitest (kc-dashboard widget); FastAPI (supervisor route); existing kc-subagents infrastructure.

**Spec:** `docs/superpowers/specs/2026-05-15-zeroclaw-port-milestone-design.md`

**Source machine for the copy:** ZeroClaw Raspberry Pi at `100.79.47.123` (Tailscale IP). SSH access via `sshpass`-wrapped `ssh sammydallal@100.79.47.123` (password is in this session's history; do NOT memo it). The executor should use `SSHPASS='<password>' sshpass -e ssh ...` for password-via-env (safer than `-p`). Every copy operation is **read-only** on the remote.

---

## File Map

### Phase A — money-manager port

| File | Action |
|---|---|
| `workspace/.gitkeep` | Create — placeholder so the dir is tracked even when contents are gitignored |
| `workspace/portfolio.py` | Create — sanitized copy from ZeroClaw |
| `workspace/ytd.py` | Create — sanitized copy from ZeroClaw |
| `workspace/stock.py` | Create — sanitized copy from ZeroClaw |
| `workspace/monte_carlo.py` | Create — sanitized copy from ZeroClaw |
| `workspace/finances.md` | Create — verbatim copy (gitignored) |
| `workspace/tests/__init__.py` | Create — empty |
| `workspace/tests/test_smoke.py` | Create — smoke test |
| `~/KonaClaw/skills/productivity/money-manager/SKILL.md` | Create — adapted SKILL.md pointing at the new workspace paths |
| `.gitignore` | Modify — add `workspace/finances.md`, `workspace/*.json`, etc. |

### Phase B — Portfolio dashboard widget

| File | Action |
|---|---|
| `kc-supervisor/src/kc_supervisor/portfolio_routes.py` | Create — `GET /portfolio/snapshot` |
| `kc-supervisor/tests/test_portfolio_routes.py` | Create |
| `kc-supervisor/src/kc_supervisor/main.py` | Modify — include the new router |
| `kc-dashboard/src/api/portfolio.ts` | Create — typed API client |
| `kc-dashboard/src/components/PortfolioWidget.tsx` | Create |
| `kc-dashboard/src/components/PortfolioWidget.test.tsx` | Create |
| `kc-dashboard/src/views/Portfolio.tsx` | Create |
| `kc-dashboard/src/App.tsx` (or wherever the sidebar nav lives) | Modify — add Portfolio tab |

### Phase C — Tessy subagent

| File | Action |
|---|---|
| `workspace/tesla_price.py` | Create — sanitized copy |
| `workspace/update_tesla_pricing.py` | Create — sanitized copy |
| `workspace/update_tesla_offers.py` | Create — sanitized copy |
| `workspace/update_tesla_from_screenshot.py` | Create — **rewritten** (replaces ZeroClaw's `describe_image.py` dep) |
| `workspace/tesla_pricing.json` | Create — verbatim copy (gitignored) |
| `workspace/tesla_offers.json` | Create — verbatim copy (gitignored) |
| `workspace/tesla_offers.md` | Create — verbatim copy (gitignored) |
| `workspace/tests/test_tesla.py` | Create — unit tests for the 4 scripts |
| `kc-supervisor/src/kc_supervisor/tessy_tools.py` | Create — `tesla.price`, `tesla.update_pricing`, `tesla.confirm_pricing`, `tesla.update_offers_from_image` |
| `kc-supervisor/tests/test_tessy_tools.py` | Create |
| `kc-supervisor/src/kc_supervisor/assembly.py` | Modify — register Tessy tools on the Tessy subagent (per-template tool gating) |
| `~/KonaClaw/subagent-templates/tessy.yaml` | Create — new subagent template |

---

# PHASE A — money-manager port

## Task A1: Bootstrap workspace directory + .gitignore

**Files:**
- Create: `workspace/.gitkeep`
- Create: `workspace/tests/__init__.py` (empty)
- Modify: `.gitignore`

- [ ] **Step 1: Create the workspace skeleton**

```bash
mkdir -p workspace/tests
touch workspace/.gitkeep workspace/tests/__init__.py
```

- [ ] **Step 2: Update .gitignore**

Append to `.gitignore`:

```
# Workspace runtime data — copied from ZeroClaw, contains private financial + pricing data
workspace/finances.md
workspace/tesla_pricing.json
workspace/tesla_offers.json
workspace/tesla_offers.md
workspace/*.json.bak.*
workspace/__pycache__/
workspace/tests/__pycache__/
```

- [ ] **Step 3: Verify**

```bash
git check-ignore -v workspace/finances.md workspace/tesla_pricing.json
```
Expected: both paths reported as ignored by the new `.gitignore` entries.

- [ ] **Step 4: Commit**

```bash
git add workspace/.gitkeep workspace/tests/__init__.py .gitignore
git commit -m "chore: bootstrap workspace/ for ZeroClaw port + gitignore private data"
```

---

## Task A2: Copy + sanitize the 4 money-manager scripts

**Files:**
- Create: `workspace/portfolio.py`
- Create: `workspace/ytd.py`
- Create: `workspace/stock.py`
- Create: `workspace/monte_carlo.py`

- [ ] **Step 1: Pull each script from ZeroClaw verbatim, then sanitize**

For each of the four scripts, run from the repo root:

```bash
SSHPASS='<password from session>' sshpass -e ssh -o StrictHostKeyChecking=accept-new \
  sammydallal@100.79.47.123 \
  'cat ~/.zeroclaw/workspace/portfolio.py' > workspace/portfolio.py
```

Repeat with `ytd.py`, `stock.py`, `monte_carlo.py` substituted in both the remote path and the local destination.

- [ ] **Step 2: Apply the sanitization rules to all 4 scripts**

Each script has a header block like this (with slight variations):

```python
BOT_TOKEN = os.environ.get("BOT_TOKEN_OVERRIDE", "<REDACTED-rotate-via-BotFather>")
CHAT_ID   = os.environ.get("CHAT_ID_OVERRIDE", "8627206839")
SILENT    = "--silent" in sys.argv
```

For each script:
1. **Delete the `BOT_TOKEN` and `CHAT_ID` lines** (any line that defines or references either constant).
2. **Delete the `send_telegram(...)` helper function** if present (search for `def send_telegram`).
3. **Delete the `_telegram_send` helper** if present.
4. **Delete every call site** of those helpers (search the file for `send_telegram(`, `_telegram_send(`, and remove the lines / surrounding `if not SILENT:` blocks).
5. **Keep `SILENT = "--silent" in sys.argv` and all `if SILENT:` / `if not SILENT:` branches** — the `--silent` branch is the JSON-emit path Kona uses; the non-silent branch can stay (it's just `print()` to stdout instead of Telegram).
6. **Keep the `HOLDINGS` dict, `fetch(...)` functions, and all calculation logic unchanged.**
7. **Keep `numpy` import in `monte_carlo.py`** — it's the only third-party dep across these four scripts.

After sanitization, verify no Telegram references remain:

```bash
grep -nE 'BOT_TOKEN|CHAT_ID|telegram|Telegram' workspace/portfolio.py workspace/ytd.py workspace/stock.py workspace/monte_carlo.py
```
Expected: no matches.

- [ ] **Step 3: Verify each script runs `--silent` cleanly**

```bash
cd workspace
python3 portfolio.py --silent 2>&1 | head -5
python3 ytd.py --silent 2>&1 | head -5
python3 stock.py AAPL 2>&1 | head -5
# monte_carlo runs longer; just check it imports without error:
python3 -c "import monte_carlo" 2>&1 | head -3
```

Expected: each command produces JSON (portfolio/ytd) or readable output (stock); none raise ImportError or NameError. If `numpy` is missing, install it: `arch -arm64 python3 -m pip install numpy --break-system-packages` (matches the repo's existing install convention — see kc-attachments deps).

- [ ] **Step 4: Commit**

```bash
git add workspace/portfolio.py workspace/ytd.py workspace/stock.py workspace/monte_carlo.py
git commit -m "feat(workspace): port + sanitize money-manager scripts (portfolio/ytd/stock/monte_carlo) from ZeroClaw"
```

---

## Task A3: Copy finances.md + create SKILL.md

**Files:**
- Create: `workspace/finances.md` (gitignored)
- Create: `~/KonaClaw/skills/productivity/money-manager/SKILL.md`

- [ ] **Step 1: Pull finances.md from ZeroClaw**

```bash
SSHPASS='<password>' sshpass -e ssh -o StrictHostKeyChecking=accept-new \
  sammydallal@100.79.47.123 \
  'cat ~/.zeroclaw/workspace/finances.md' > workspace/finances.md
```

Verify gitignore is working:

```bash
git status --porcelain workspace/finances.md
```
Expected: no output (file is ignored).

- [ ] **Step 2: Create the SKILL.md**

```bash
mkdir -p ~/KonaClaw/skills/productivity/money-manager
```

Create `~/KonaClaw/skills/productivity/money-manager/SKILL.md`:

```markdown
---
name: money-manager
description: |
  Access Sammy's complete financial profile and live Yahoo Finance stock data.
  Use for portfolio lookups, YTD performance, single-stock quotes, and retirement
  planning analysis. All data is local; no web search needed for market prices.
tags: [finance, portfolio, retirement, stocks]
related_skills: []
---

# Money Manager

Sammy's financial data lives in the local workspace. ALWAYS read it before
answering any financial question.

## Step 1 — Read financial profile

```
terminal_run(command="cat finances.md", cwd="<repo-root>/workspace")
```

Contains: portfolio breakdown, all holdings, income, expenses, retirement
targets, tax situation.

## Step 2 — Live market data (Yahoo Finance — no internet search needed)

**Full portfolio snapshot (current prices + today's P&L):**
```
terminal_run(command="python3 portfolio.py --silent", cwd="<repo-root>/workspace")
```

**YTD performance (Jan 1 → today):**
```
terminal_run(command="python3 ytd.py --silent", cwd="<repo-root>/workspace")
```

**Single or multiple tickers:**
```
terminal_run(command="python3 stock.py AAPL NVDA VOO TSLA", cwd="<repo-root>/workspace")
```

## Step 3 — Monte Carlo retirement simulation

```
terminal_run(command="python3 monte_carlo.py", cwd="<repo-root>/workspace")
terminal_run(command="python3 monte_carlo.py --retire-age 58", cwd="<repo-root>/workspace")
terminal_run(command="python3 monte_carlo.py --crash", cwd="<repo-root>/workspace")
terminal_run(command="python3 monte_carlo.py --all", cwd="<repo-root>/workspace")
```

## Rules

- NEVER ask Sammy for portfolio data — it's all in finances.md and the scripts above.
- NEVER say "I don't have access to real-time data" — you do, run the scripts.
- ALWAYS run `cat finances.md` first, then the relevant pricing script.
- All scripts query Yahoo Finance directly — no web search needed for market data.
```

Replace `<repo-root>` in the cwd= lines with the actual absolute path: `/Users/sammydallal/Desktop/claudeCode/SammyClaw`. Use sed:

```bash
sed -i '' 's|<repo-root>|/Users/sammydallal/Desktop/claudeCode/SammyClaw|g' \
  ~/KonaClaw/skills/productivity/money-manager/SKILL.md
```

- [ ] **Step 3: Verify the skill is discoverable**

```bash
ls -la ~/KonaClaw/skills/productivity/money-manager/SKILL.md
```
Expected: file exists, 1-2 KB.

- [ ] **Step 4: Commit (SKILL.md only — finances.md is gitignored)**

The SKILL.md lives OUTSIDE the repo (in `~/KonaClaw/skills/`), so it's not committable here. Record what was done in the commit message of the next task instead, OR add a brief note in `docs/superpowers/specs/2026-05-15-zeroclaw-port-milestone-design.md` if useful.

No git commit needed for this task.

---

## Task A4: Smoke test

**Files:**
- Create: `workspace/tests/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `workspace/tests/test_smoke.py`:

```python
"""Smoke tests for the ported workspace scripts."""
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
    assert "total_value" in payload or "ytd_pct" in payload, f"keys: {list(payload.keys())}"


def test_stock_runs_for_known_ticker():
    r = subprocess.run(
        [sys.executable, "stock.py", "AAPL"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "AAPL" in r.stdout
```

- [ ] **Step 2: Run the smoke test**

```bash
arch -arm64 python3 -m pytest workspace/tests/test_smoke.py -v
```

Expected: 3/3 PASS. If `pytest` isn't installed for system Python: `arch -arm64 python3 -m pip install pytest --break-system-packages`.

If `ytd.py`'s actual output shape differs (e.g., uses a different top-level key), adjust the assertion in `test_ytd_silent_returns_json` to match what the script actually emits — print `r.stdout` to see.

- [ ] **Step 3: Commit**

```bash
git add workspace/tests/test_smoke.py
git commit -m "test(workspace): smoke tests for portfolio/ytd/stock scripts"
```

- [ ] **Step 4: Manual verification in a fresh Kona chat (no test, just confirmation)**

Tell Sammy: "money-manager port done. In a fresh Kona chat, try: *'What's my portfolio worth?'* — Kona should read the SKILL.md, then call `terminal_run(\"python3 portfolio.py --silent\", cwd=\"workspace\")` and synthesize the answer. Confirm `KC_TERMINAL_ENABLED=true` is set in `~/.konaclaw.env` first — if not, the skill won't execute."

---

# PHASE B — Portfolio dashboard widget

**Depends on Phase A.** Assumes `workspace/portfolio.py --silent` works.

## Task B1: Portfolio API client (TypeScript)

**Files:**
- Create: `kc-dashboard/src/api/portfolio.ts`

- [ ] **Step 1: Write the client**

Create `kc-dashboard/src/api/portfolio.ts`:

```typescript
import { getBaseUrl } from "./client";

export interface PortfolioHolding {
  ticker: string;
  value: number;
  day_change: number;
  gain_pct: number;
}

export interface PortfolioSnapshot {
  total_value: number;
  total_gain: number;
  total_day_change: number;
  day_pct: number;
  holdings: PortfolioHolding[];
}

export interface SnapshotResponse {
  cached_at: string;            // ISO timestamp
  payload: PortfolioSnapshot | null;
  stale: boolean;
  error?: string;
  last_good?: PortfolioSnapshot | null;
}

export async function getSnapshot(refresh = false): Promise<SnapshotResponse> {
  const url = `${getBaseUrl()}/portfolio/snapshot${refresh ? "?refresh=true" : ""}`;
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`portfolio snapshot failed (${r.status}): ${await r.text()}`);
  }
  return r.json();
}
```

- [ ] **Step 2: tsc check**

```bash
cd kc-dashboard && arch -arm64 npx tsc --noEmit -p tsconfig.json
```
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add kc-dashboard/src/api/portfolio.ts
git commit -m "feat(kc-dashboard): typed API client for /portfolio/snapshot"
```

---

## Task B2: Supervisor portfolio route + tests + main.py wiring

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/portfolio_routes.py`
- Create: `kc-supervisor/tests/test_portfolio_routes.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py`

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_portfolio_routes.py`:

```python
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
    assert call_count["n"] == 1  # second call hit cache


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
    """When the subprocess fails after a previously-good call, return the
    error plus the last-good payload."""
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
    client = TestClient(_app_with_router(tmp_path, cache_s=0))  # cache off
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
```

Run and verify FAIL (module doesn't exist yet):

```bash
arch -arm64 python3 -m pytest kc-supervisor/tests/test_portfolio_routes.py -v
```

- [ ] **Step 2: Implement the router**

Create `kc-supervisor/src/kc_supervisor/portfolio_routes.py`:

```python
from __future__ import annotations
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query


_DEFAULT_TIMEOUT_S = 5
_DEFAULT_CACHE_S = 60


def build_portfolio_router(
    *, workspace_dir: Path,
    cache_seconds: int | None = None,
    subprocess_timeout: int = _DEFAULT_TIMEOUT_S,
) -> APIRouter:
    """Builds a router exposing GET /portfolio/snapshot.

    Runs `python3 portfolio.py --silent` in `workspace_dir`. Caches the
    last-good result for `cache_seconds` seconds (default from env
    KC_PORTFOLIO_CACHE_S, fallback 60). `?refresh=true` bypasses cache.
    """
    if cache_seconds is None:
        cache_seconds = int(os.environ.get("KC_PORTFOLIO_CACHE_S", str(_DEFAULT_CACHE_S)))

    router = APIRouter(prefix="/portfolio", tags=["portfolio"])
    state: dict[str, Any] = {"payload": None, "cached_at_ts": 0.0}

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _fetch_subprocess() -> dict[str, Any]:
        proc = subprocess.run(
            ["python3", "portfolio.py", "--silent"],
            cwd=str(workspace_dir), capture_output=True, text=True,
            timeout=subprocess_timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"portfolio.py exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        # Last line of stdout is the JSON payload.
        last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        return json.loads(last_line)

    @router.get("/snapshot")
    def snapshot(refresh: bool = Query(False)):
        now = time.time()
        # Serve from cache?
        if (
            not refresh
            and state["payload"] is not None
            and (now - state["cached_at_ts"]) < cache_seconds
        ):
            return {
                "cached_at": datetime.fromtimestamp(state["cached_at_ts"], tz=timezone.utc).isoformat(timespec="seconds"),
                "payload": state["payload"],
                "stale": False,
            }
        # Otherwise fetch fresh.
        try:
            payload = _fetch_subprocess()
            state["payload"] = payload
            state["cached_at_ts"] = now
            return {"cached_at": _now_iso(), "payload": payload, "stale": False}
        except subprocess.TimeoutExpired:
            return {
                "cached_at": _now_iso(),
                "payload": None,
                "stale": True,
                "error": f"timeout after {subprocess_timeout}s",
                "last_good": state["payload"],
            }
        except (RuntimeError, json.JSONDecodeError) as e:
            return {
                "cached_at": _now_iso(),
                "payload": None,
                "stale": True,
                "error": str(e)[:300],
                "last_good": state["payload"],
            }

    return router
```

- [ ] **Step 3: Run tests, verify PASS**

```bash
arch -arm64 python3 -m pytest kc-supervisor/tests/test_portfolio_routes.py -v
```
Expected: 5/5 PASS.

- [ ] **Step 4: Wire the router into main.py**

In `kc-supervisor/src/kc_supervisor/main.py`, alongside the existing `build_attachments_router` include, add:

```python
from kc_supervisor.portfolio_routes import build_portfolio_router
# ...
app.include_router(
    build_portfolio_router(
        workspace_dir=Path(__file__).resolve().parents[3] / "workspace",
    )
)
```

The `parents[3]` derives the repo root from `kc-supervisor/src/kc_supervisor/main.py` (3 levels up = `SammyClaw/`). Verify by adding `print(...)` once and removing.

If the supervisor lives somewhere with a different depth, count `parents[]` accordingly. To be safer, look for a marker file:

```python
def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for ancestor in [p, *p.parents]:
        if (ancestor / "workspace").is_dir() and (ancestor / "kc-supervisor").is_dir():
            return ancestor
    raise RuntimeError("could not locate SammyClaw repo root from kc-supervisor main.py")
```

Use whichever feels cleaner. The marker-file approach is more robust.

- [ ] **Step 5: Supervisor import smoke + manual curl**

```bash
arch -arm64 python3 -c "from kc_supervisor.main import main; print('ok')"
```
Then restart the supervisor (Sammy can do this via `KonaClawDashboard.command`) and:

```bash
curl -s http://127.0.0.1:8765/portfolio/snapshot | python3 -m json.tool | head -30
```
Expected: a JSON object with `payload.total_value`, `payload.holdings`, etc.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/portfolio_routes.py \
        kc-supervisor/tests/test_portfolio_routes.py \
        kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-supervisor): GET /portfolio/snapshot with 60s cache + last-good fallback"
```

---

## Task B3: PortfolioWidget component + tests

**Files:**
- Create: `kc-dashboard/src/components/PortfolioWidget.tsx`
- Create: `kc-dashboard/src/components/PortfolioWidget.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `kc-dashboard/src/components/PortfolioWidget.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { PortfolioWidget } from "./PortfolioWidget";

vi.mock("../api/portfolio", () => ({
  getSnapshot: vi.fn(),
}));

import { getSnapshot } from "../api/portfolio";


const SAMPLE_SNAPSHOT = {
  cached_at: "2026-05-15T22:00:00+00:00",
  payload: {
    total_value: 4_524_912.12,
    total_gain: 2_361_309.12,
    total_day_change: 76_430.15,
    day_pct: 1.72,
    holdings: [
      { ticker: "AAPL", value: 1_340_070.57, day_change: 33_870.00, gain_pct: 107.51 },
      { ticker: "NVDA", value: 1_084_030.00, day_change: 17_898.72, gain_pct: 2607.64 },
      { ticker: "VOO",  value:   716_099.66, day_change:  8_688.26, gain_pct:   47.48 },
      { ticker: "TSLA", value:   500_000.00, day_change:  -5_000.00, gain_pct:  10.00 },
    ],
  },
  stale: false,
};


describe("PortfolioWidget", () => {
  beforeEach(() => {
    (getSnapshot as any).mockReset();
  });

  it("renders loading state initially", () => {
    (getSnapshot as any).mockReturnValue(new Promise(() => {}));
    render(<PortfolioWidget />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders total value + day change on success", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    expect(screen.getByText(/\+\$76,430/)).toBeInTheDocument();
    expect(screen.getByText(/1\.72%/)).toBeInTheDocument();
  });

  it("renders top 3 movers ordered by abs(day_change)", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    const moversSection = screen.getByTestId("top-movers");
    expect(moversSection).toHaveTextContent("AAPL");
    expect(moversSection).toHaveTextContent("NVDA");
    // TSLA has -5000 abs > VOO's 8688? NO, 8688 > 5000 so VOO wins. Verify:
    expect(moversSection).toHaveTextContent("VOO");
  });

  it("refresh button forces a refresh=true call", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    (getSnapshot as any).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledWith(true));
  });

  it("renders error with last_good as stale", async () => {
    (getSnapshot as any).mockResolvedValue({
      cached_at: "2026-05-15T22:00:00+00:00",
      payload: null,
      stale: true,
      error: "yahoo down",
      last_good: SAMPLE_SNAPSHOT.payload,
    });
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    expect(screen.getByText(/yahoo down/i)).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-stale")).toBeInTheDocument();
  });
});
```

Run, verify FAIL:

```bash
cd kc-dashboard && arch -arm64 npm test -- PortfolioWidget --run
```

- [ ] **Step 2: Implement the component**

Create `kc-dashboard/src/components/PortfolioWidget.tsx`:

```typescript
import { useEffect, useState, useCallback } from "react";
import { getSnapshot, type PortfolioSnapshot, type SnapshotResponse } from "../api/portfolio";


function fmtMoney(n: number): string {
  return `$${Math.round(n).toLocaleString("en-US")}`;
}


function fmtChange(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${fmtMoney(Math.abs(n))}`;
}


function fmtPct(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}


function topMovers(p: PortfolioSnapshot): PortfolioSnapshot["holdings"] {
  return [...p.holdings]
    .sort((a, b) => Math.abs(b.day_change) - Math.abs(a.day_change))
    .slice(0, 3);
}


export function PortfolioWidget() {
  const [snap, setSnap] = useState<SnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const r = await getSnapshot(refresh);
      setSnap(r);
    } catch (e) {
      setSnap({
        cached_at: new Date().toISOString(),
        payload: null,
        stale: true,
        error: (e as Error)?.message ?? "fetch failed",
        last_good: null,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false);
    const t = setInterval(() => load(false), 5 * 60 * 1000);
    return () => clearInterval(t);
  }, [load]);

  if (loading && !snap) {
    return <div className="portfolio-widget portfolio-widget--loading">Loading portfolio…</div>;
  }

  // Decide which payload to render (live or last_good fallback).
  const payload: PortfolioSnapshot | null = snap?.payload ?? snap?.last_good ?? null;
  const isStale = Boolean(snap?.stale && payload);
  const errorMsg = snap?.error;

  if (!payload) {
    return (
      <div className="portfolio-widget portfolio-widget--error">
        <div>Could not load portfolio.</div>
        {errorMsg && <div className="text-xs text-muted">{errorMsg}</div>}
        <button onClick={() => load(true)}>Retry</button>
      </div>
    );
  }

  const movers = topMovers(payload);
  const changeColor = payload.total_day_change >= 0 ? "text-green-600" : "text-red-600";

  return (
    <div
      className={`portfolio-widget ${isStale ? "opacity-60" : ""}`}
      data-testid={isStale ? "portfolio-stale" : "portfolio-fresh"}
    >
      <div className="text-3xl font-semibold">{fmtMoney(payload.total_value)}</div>
      <div className={`text-lg ${changeColor}`}>
        {fmtChange(payload.total_day_change)} ({fmtPct(payload.day_pct)})
      </div>

      <div className="mt-4" data-testid="top-movers">
        <div className="text-sm font-bold mb-1">Top movers</div>
        <ul className="text-sm">
          {movers.map((h) => (
            <li key={h.ticker} className="flex justify-between gap-4">
              <span>{h.ticker}</span>
              <span className={h.day_change >= 0 ? "text-green-600" : "text-red-600"}>
                {fmtChange(h.day_change)}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-2 text-xs text-muted">
        Updated: {new Date(snap!.cached_at).toLocaleTimeString()}{" "}
        <button onClick={() => load(true)} aria-label="refresh">↻ Refresh</button>
      </div>

      {errorMsg && (
        <div className="mt-2 text-xs text-red-600" role="alert">
          {errorMsg}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Run tests, verify PASS**

```bash
cd kc-dashboard && arch -arm64 npm test -- PortfolioWidget --run
```
Expected: 5/5 PASS.

- [ ] **Step 4: Commit**

```bash
git add kc-dashboard/src/components/PortfolioWidget.tsx \
        kc-dashboard/src/components/PortfolioWidget.test.tsx
git commit -m "feat(kc-dashboard): PortfolioWidget — total value, day change, top movers, refresh"
```

---

## Task B4: Portfolio view + sidebar entry

**Files:**
- Create: `kc-dashboard/src/views/Portfolio.tsx`
- Modify: dashboard sidebar / navigation file (location varies — find it)

- [ ] **Step 1: Create the view**

Create `kc-dashboard/src/views/Portfolio.tsx`:

```typescript
import { PortfolioWidget } from "../components/PortfolioWidget";

export default function Portfolio() {
  return (
    <div className="p-8 max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Portfolio</h1>
      <PortfolioWidget />
    </div>
  );
}
```

- [ ] **Step 2: Find and modify the sidebar / route table**

Inspect `kc-dashboard/src/App.tsx` to find the routing setup. There will be a `<Routes>` block with `<Route path="..." element={<...View />} />` entries. Add:

```tsx
import Portfolio from "./views/Portfolio";
// ... inside <Routes>:
<Route path="/portfolio" element={<Portfolio />} />
```

Find the sidebar navigation (likely a `<nav>` block in `App.tsx` or a `Sidebar.tsx` component). Add a new entry matching the existing pattern. Example:

```tsx
<Link to="/portfolio">Portfolio</Link>
```

- [ ] **Step 3: tsc check**

```bash
cd kc-dashboard && arch -arm64 npx tsc --noEmit -p tsconfig.json
```
Expected: no new errors.

- [ ] **Step 4: Run the dashboard test suite**

```bash
cd kc-dashboard && arch -arm64 npm test --run 2>&1 | tail -8
```
Expected: all tests pass (pre-existing flaky Chat.test.tsx failures from prior phases are acceptable; new failures introduced by this task are not).

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Portfolio.tsx kc-dashboard/src/App.tsx
git commit -m "feat(kc-dashboard): Portfolio view + sidebar entry"
```

---

## Task B5: Manual smoke

- [ ] **Step 1: Restart KonaClaw**

Stop and re-run `KonaClawDashboard.command` so the supervisor picks up the new portfolio route.

- [ ] **Step 2: Visit /portfolio in the browser**

Open `http://localhost:5173/portfolio`. Expected: total value, day change, 3 top movers, last-updated timestamp, refresh button. Click refresh — value updates.

- [ ] **Step 3: Hand off**

Tell Sammy: "Portfolio widget live at http://localhost:5173/portfolio. Verify the numbers match what `portfolio.py --silent` returns in the workspace. If anything looks off, check the supervisor stderr for the /portfolio/snapshot call."

---

# PHASE C — Tessy subagent

**Independent of Phases A and B.** Requires Gemini API key in secrets.

## Task C1: Save Gemini API key to secrets (manual prereq)

- [ ] **Step 1: Ask Sammy for the Gemini API key**

The Tessy port uses Gemini for the Tesla screenshot extraction (replacing ZeroClaw's `describe_image.py`). Tessy's `tesla_price.py` ALSO uses Gemini for NLP arg parsing. Both need `GEMINI_API_KEY`.

In execution, prompt: "I need a Gemini API key for the Tesla scripts. Paste it (it'll be saved to ~/KonaClaw/config/secrets.yaml.enc, not committed) — or tell me to skip and the screenshot/NLP features won't work."

- [ ] **Step 2: Save via SecretsStore**

```bash
cd kc-supervisor && python3 -c "
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from kc_supervisor.secrets_store import SecretsStore, SecurityCliKeychain

store = SecretsStore(Path.home() / 'KonaClaw' / 'config', SecurityCliKeychain())
secrets = store.load()
secrets['gemini_api_key'] = '<KEY>'  # replace before running
store.save(secrets)
print('gemini_api_key saved, len:', len(secrets['gemini_api_key']))
"
```

- [ ] **Step 3: Wire the env var into the supervisor's subprocess environment**

In `kc-supervisor/src/kc_supervisor/main.py`, near where other secrets are pulled and exposed, capture `gemini_api_key` from secrets and make it available to subprocess calls Tessy's tools will spawn. Simplest: set as a process env at supervisor startup so child processes inherit it:

```python
_gemini_key = secrets.get("gemini_api_key")
if _gemini_key:
    os.environ.setdefault("GEMINI_API_KEY", _gemini_key)
```

Similarly for `FIRECRAWL_API_KEY` (already in secrets per the file-ingestion memory):

```python
_firecrawl_key = secrets.get("firecrawl_api_key")
if _firecrawl_key:
    os.environ.setdefault("FIRECRAWL_API_KEY", _firecrawl_key)
```

(`setdefault` so existing env vars win — useful for tests.)

No commit yet — this goes with Task C6's tool wiring commit.

---

## Task C2: Copy + sanitize tesla_price.py

**Files:**
- Create: `workspace/tesla_price.py`

- [ ] **Step 1: Pull from ZeroClaw**

```bash
SSHPASS='<password>' sshpass -e ssh -o StrictHostKeyChecking=accept-new \
  sammydallal@100.79.47.123 \
  'cat ~/.zeroclaw/workspace/tesla_price.py' > workspace/tesla_price.py
```

- [ ] **Step 2: Apply sanitization**

In `workspace/tesla_price.py`:

1. Find the constant block at the top:

```python
BOT_TOKEN       = os.environ.get("BOT_TOKEN_OVERRIDE", "<REDACTED>")
CHAT_ID         = os.environ.get("CHAT_ID_OVERRIDE", "8627206839")
FIRECRAWL_KEY   = os.environ.get("FIRECRAWL_API_KEY", "fc-5aeb...")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "AIzaSyB...")
```

2. Replace with:

```python
BOT_TOKEN       = None  # KonaClaw does not deliver via Telegram from this script
CHAT_ID         = None
FIRECRAWL_KEY   = os.environ.get("FIRECRAWL_API_KEY")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
if FIRECRAWL_KEY is None:
    print("FIRECRAWL_API_KEY not set", file=sys.stderr); sys.exit(2)
if GEMINI_API_KEY is None:
    print("GEMINI_API_KEY not set", file=sys.stderr); sys.exit(2)
```

3. Search the file for `send_telegram(` and delete every call. The function body itself can be deleted too if not used elsewhere.

4. Search for `BOT_TOKEN` and `CHAT_ID` references; remove any remaining.

5. Make `--silent` the assumed mode for KonaClaw — but DO NOT delete the non-silent branch (it may still print useful debug to stdout). Just ensure `--silent` is the documented entry path.

Verify:

```bash
grep -nE 'BOT_TOKEN|CHAT_ID|telegram' workspace/tesla_price.py
```
Expected: zero matches.

- [ ] **Step 3: Smoke run**

Set env vars and run with a simple query:

```bash
cd workspace
GEMINI_API_KEY='<key>' FIRECRAWL_API_KEY='<key>' python3 tesla_price.py --trim rwd --zip 95128 --months 72 --down 7000 --silent 2>&1 | head -20
```
Expected: JSON output with pricing. If it crashes, the sanitization missed a Telegram reference or env access — find and fix.

- [ ] **Step 4: Commit**

```bash
git add workspace/tesla_price.py
git commit -m "feat(workspace): port + sanitize tesla_price.py from ZeroClaw"
```

---

## Task C3: Copy + sanitize update_tesla_pricing.py + update_tesla_offers.py

**Files:**
- Create: `workspace/update_tesla_pricing.py`
- Create: `workspace/update_tesla_offers.py`

- [ ] **Step 1: Pull both from ZeroClaw**

```bash
for f in update_tesla_pricing.py update_tesla_offers.py; do
  SSHPASS='<password>' sshpass -e ssh -o StrictHostKeyChecking=accept-new \
    sammydallal@100.79.47.123 \
    "cat ~/.zeroclaw/workspace/$f" > "workspace/$f"
done
```

- [ ] **Step 2: Apply the standard sanitization to both files**

Same recipe as Task C2 Step 2:
1. Replace the constant block (`BOT_TOKEN`, `CHAT_ID`, `FIRECRAWL_KEY`, `GEMINI_API_KEY`) with the env-only version + sys.exit guard for missing keys.
2. Delete every `send_telegram(...)` call.
3. Delete the `send_telegram` function definition if it's local to the file.
4. **Special for `update_tesla_pricing.py`**: the "post diff to Telegram" branch in the `--nlp` path needs to be replaced with `print(json.dumps({"pending": True, "diff": <diff_dict>}))` so Tessy (the tool wrapper) can pick up the diff and return it as the tool result.

Verify:

```bash
grep -nE 'BOT_TOKEN|CHAT_ID|telegram' workspace/update_tesla_*.py
```
Expected: zero matches.

- [ ] **Step 3: Commit**

```bash
git add workspace/update_tesla_pricing.py workspace/update_tesla_offers.py
git commit -m "feat(workspace): port + sanitize update_tesla_pricing/offers from ZeroClaw"
```

---

## Task C4: Rewrite update_tesla_from_screenshot.py for KonaClaw

**Files:**
- Create: `workspace/update_tesla_from_screenshot.py` (new — does NOT copy ZeroClaw's version)

ZeroClaw's version depends on `describe_image.py` (vision) and `rebuild_tessy_prompt.py` (regenerates ZeroClaw's config.toml). Neither exists in KonaClaw. We replace the vision pipeline with a direct Gemini call and drop the prompt-regen step entirely.

- [ ] **Step 1: Write the new script**

Create `workspace/update_tesla_from_screenshot.py`:

```python
#!/usr/bin/env python3
"""
update_tesla_from_screenshot.py — Parse a Tesla offers screenshot via Gemini
and update tesla_offers.md + tesla_offers.json.

Invoked by KonaClaw's Tessy subagent. Takes an absolute file path to an image
(the supervisor's Tessy tool wrapper resolves an attachment id to a path
before calling this script).

Usage:
    GEMINI_API_KEY=<key> python3 update_tesla_from_screenshot.py <image_path>
"""
import base64
import json
import os
import pathlib
import sys
import urllib.request
from datetime import datetime


WORKSPACE = pathlib.Path(__file__).parent
OFFERS_MD = WORKSPACE / "tesla_offers.md"
OFFERS_JSON = WORKSPACE / "tesla_offers.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-1.5-pro")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

EXTRACT_PROMPT = (
    "This is a screenshot of Tesla's current-offers page (USA). "
    "Extract ALL offers, financing rates, lease prices, APR deals, bonuses, and perks visible. "
    "For each vehicle model or 'Every New Tesla' section, list every offer with full details "
    "(price, terms, eligibility, expiration if shown). Be complete and verbatim. "
    "Return the text formatted as readable markdown."
)


def extract_offers(image_path: pathlib.Path) -> str:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    body = {
        "contents": [{
            "parts": [
                {"text": EXTRACT_PROMPT},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ],
        }],
    }
    req = urllib.request.Request(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        print(f"unexpected Gemini response: {data!r}", file=sys.stderr)
        raise


def write_offers(extracted: str, image_path: pathlib.Path) -> str:
    stamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    content = f"""# Tesla Current Offers (USA)
*Last updated: {stamp} — sourced from screenshot of tesla.com/current-offers*

> This file is auto-updated. Tessy should always read this file for current Tesla promotions.

---

{extracted}
"""
    OFFERS_MD.write_text(content, encoding="utf-8")
    # Also persist a minimal JSON snapshot so future code can read structured
    # offers without re-parsing the markdown. v1: store the raw text under
    # `every_new_tesla` since KonaClaw doesn't yet need per-model parsing.
    OFFERS_JSON.write_text(json.dumps({
        "updated_at": stamp,
        "source_image": str(image_path),
        "raw_markdown": extracted,
    }, indent=2), encoding="utf-8")
    return stamp


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update_tesla_from_screenshot.py <image_path>", file=sys.stderr)
        sys.exit(1)
    image_path = pathlib.Path(sys.argv[1])
    if not image_path.exists():
        print(f"ERROR: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)
    extracted = extract_offers(image_path)
    if not extracted or len(extracted) < 50:
        print("ERROR: extracted text too short — Gemini may have failed", file=sys.stderr)
        sys.exit(1)
    stamp = write_offers(extracted, image_path)
    print(json.dumps({
        "ok": True,
        "updated_at": stamp,
        "chars": len(extracted),
    }))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable + verify imports**

```bash
chmod +x workspace/update_tesla_from_screenshot.py
python3 -c "import ast; ast.parse(open('workspace/update_tesla_from_screenshot.py').read())"
```
Expected: no error (syntax check).

- [ ] **Step 3: Commit**

```bash
git add workspace/update_tesla_from_screenshot.py
git commit -m "feat(workspace): rewrite update_tesla_from_screenshot.py for KonaClaw (direct Gemini, no describe_image.py dep)"
```

---

## Task C5: Copy runtime data files

**Files:**
- Create: `workspace/tesla_pricing.json` (gitignored)
- Create: `workspace/tesla_offers.json` (gitignored)
- Create: `workspace/tesla_offers.md` (gitignored)

- [ ] **Step 1: Pull each from ZeroClaw**

```bash
for f in tesla_pricing.json tesla_offers.json tesla_offers.md; do
  SSHPASS='<password>' sshpass -e ssh -o StrictHostKeyChecking=accept-new \
    sammydallal@100.79.47.123 \
    "cat ~/.zeroclaw/workspace/$f" > "workspace/$f"
done
```

- [ ] **Step 2: Verify gitignore is working**

```bash
git status --porcelain workspace/tesla_pricing.json workspace/tesla_offers.json workspace/tesla_offers.md
```
Expected: no output (all 3 ignored).

- [ ] **Step 3: No commit** (files are gitignored). Move to next task.

---

## Task C6: Tessy tool wrappers (supervisor) + tests

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/tessy_tools.py`
- Create: `kc-supervisor/tests/test_tessy_tools.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py` (env wiring from Task C1)
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py` (register tools when Tessy is the agent)

- [ ] **Step 1: Write the failing tests**

Create `kc-supervisor/tests/test_tessy_tools.py`:

```python
import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

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
async def test_tesla_price_shells_out_and_parses_json(workspace, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok('{"monthly": 612.34}'))
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    impl = tools["tesla.price"].impl
    out = await impl(trim="rwd", zip="95128", months=72, down=7000)
    parsed = json.loads(out)
    assert parsed["monthly"] == 612.34


@pytest.mark.asyncio
async def test_tesla_update_pricing_returns_diff(workspace, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _ok('{"pending": true, "diff": {"models.my.price": [39900, 41990]}}'),
    )
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=None)
    impl = tools["tesla.update_pricing"].impl
    out = await impl(nlp="raise Model Y RWD to $41,990")
    parsed = json.loads(out)
    assert parsed["pending"] is True
    assert "models.my.price" in parsed["diff"]


@pytest.mark.asyncio
async def test_tesla_update_offers_from_image_resolves_attachment(workspace, monkeypatch):
    fake_store = MagicMock()
    fake_path = workspace / "fake.png"
    fake_path.write_bytes(b"\x89PNG\r\n")
    fake_store.original_path.return_value = fake_path

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok('{"ok": true}'))
    tools = build_tessy_tools(workspace_dir=workspace, attachment_store=fake_store)
    impl = tools["tesla.update_offers_from_image"].impl
    out = await impl(attachment_id="att_abc")
    parsed = json.loads(out)
    assert parsed["ok"] is True
    fake_store.original_path.assert_called_once_with("att_abc")
```

Run, verify FAIL:

```bash
arch -arm64 python3 -m pytest kc-supervisor/tests/test_tessy_tools.py -v
```

- [ ] **Step 2: Implement the tools**

Create `kc-supervisor/src/kc_supervisor/tessy_tools.py`:

```python
from __future__ import annotations
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from kc_core.tools import Tool


_PRICE_PARAMS = {
    "type": "object",
    "properties": {
        "nlp": {"type": "string", "description": "Natural language config description, e.g. 'Model Y RWD, 7k down, 72 months, 95128'."},
        "trim": {"type": "string", "description": "rwd, lrawd, awd, performance, m3, m3lr, mx5, mxplaid, ms, ct, etc."},
        "paint": {"type": "string", "description": "red, blue, white, black, silver, grey, stealth"},
        "wheels": {"type": "integer", "description": "19 or 20"},
        "interior": {"type": "string", "description": "black, white, cream"},
        "zip": {"type": "string", "description": "ZIP code for tax calculation"},
        "months": {"type": "integer", "description": "36, 48, 60, 72, 84"},
        "down": {"type": "number", "description": "Amount due at signing"},
        "apr": {"type": "number", "description": "Annual percentage rate (default 5.99)"},
    },
}

_UPDATE_PRICING_PARAMS = {
    "type": "object",
    "properties": {
        "nlp": {"type": "string", "description": "Natural language pricing change, e.g. 'raise Model Y RWD to $41,990'."},
    },
    "required": ["nlp"],
}

_CONFIRM_PRICING_PARAMS = {"type": "object", "properties": {}}

_UPDATE_OFFERS_PARAMS = {
    "type": "object",
    "properties": {
        "attachment_id": {"type": "string", "description": "KonaClaw attachment id (att_xxxxxxxxxxxx) of a Tesla offers screenshot."},
    },
    "required": ["attachment_id"],
}


def _run_subprocess(workspace_dir: Path, args: list[str], timeout: int = 30) -> str:
    """Run a workspace script; return stdout. Raises on non-zero exit / timeout."""
    try:
        proc = subprocess.run(
            ["python3", *args],
            cwd=str(workspace_dir), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "timeout", "args": args})
    if proc.returncode != 0:
        return json.dumps({"error": "nonzero_exit", "code": proc.returncode, "stderr": proc.stderr.strip()[:300]})
    return proc.stdout.strip()


def build_tessy_tools(
    *, workspace_dir: Path, attachment_store: Any,
) -> dict[str, Tool]:
    """Returns the four Tessy tools.

    `attachment_store` is the kc_attachments.AttachmentStore singleton from
    Deps; only `tesla.update_offers_from_image` uses it (to resolve an
    attachment id into a file path).
    """

    async def _price_impl(**kwargs) -> str:
        args = ["tesla_price.py", "--silent"]
        nlp = kwargs.get("nlp")
        if nlp:
            args.extend(["--nlp", nlp])
        for k in ("trim", "paint", "wheels", "interior", "zip", "months", "down", "apr"):
            v = kwargs.get(k)
            if v is not None:
                args.extend([f"--{k}", str(v)])
        return await asyncio.to_thread(_run_subprocess, workspace_dir, args, 30)

    async def _update_pricing_impl(nlp: str) -> str:
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_pricing.py", "--nlp", nlp], 30,
        )

    async def _confirm_pricing_impl() -> str:
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_pricing.py", "--confirm"], 30,
        )

    async def _update_offers_impl(attachment_id: str) -> str:
        if attachment_store is None:
            return json.dumps({"error": "no_attachment_store"})
        try:
            path = attachment_store.original_path(attachment_id)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": "attachment_not_found", "attachment_id": attachment_id, "detail": str(e)})
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_from_screenshot.py", str(path)], 180,
        )

    return {
        "tesla.price": Tool(
            name="tesla.price",
            description="Calculate Tesla pricing/financing for a config. Pass either `nlp` (free-text) or structured params (trim, paint, wheels, interior, zip, months, down, apr).",
            parameters=_PRICE_PARAMS,
            impl=_price_impl,
        ),
        "tesla.update_pricing": Tool(
            name="tesla.update_pricing",
            description="Propose a pricing change via natural language. Returns the proposed diff; call tesla.confirm_pricing to apply.",
            parameters=_UPDATE_PRICING_PARAMS,
            impl=_update_pricing_impl,
        ),
        "tesla.confirm_pricing": Tool(
            name="tesla.confirm_pricing",
            description="Apply the last proposed pricing change from tesla.update_pricing.",
            parameters=_CONFIRM_PRICING_PARAMS,
            impl=_confirm_pricing_impl,
        ),
        "tesla.update_offers_from_image": Tool(
            name="tesla.update_offers_from_image",
            description="Parse a Tesla offers screenshot (by attachment id) and update tesla_offers.md/json.",
            parameters=_UPDATE_OFFERS_PARAMS,
            impl=_update_offers_impl,
        ),
    }
```

- [ ] **Step 3: Run, verify PASS**

```bash
arch -arm64 python3 -m pytest kc-supervisor/tests/test_tessy_tools.py -v
```
Expected: 3/3 PASS.

- [ ] **Step 4: Wire env vars in main.py (from Task C1)**

Already covered in Task C1 Step 3 — apply that edit now if not yet applied. Verify by running:

```bash
arch -arm64 python3 -c "from kc_supervisor.main import main; print('ok')"
```

- [ ] **Step 5: Wire Tessy tools in assembly.py**

Find `assemble_agent` in `kc-supervisor/src/kc_supervisor/assembly.py`. Look for where MCP / connector / web / attachment tools are conditionally registered based on the agent name or YAML config. Add a Tessy-specific branch:

```python
if agent_def.name == "tessy":
    from kc_supervisor.tessy_tools import build_tessy_tools
    repo_root = _find_repo_root()  # reuse the helper from Task B2 step 4
    workspace_dir = repo_root / "workspace"
    tessy_tools = build_tessy_tools(
        workspace_dir=workspace_dir,
        attachment_store=attachment_store,  # already in scope from file-ingestion phase
    )
    for tool in tessy_tools.values():
        tool_registry.register(tool)
        # Tier: read-only tesla.price is SAFE; the three update tools are MUTATING
        # so they surface an approval card before running.
        from kc_sandbox.permissions import Tier
        tier_map[tool.name] = (
            Tier.SAFE if tool.name == "tesla.price" else Tier.MUTATING
        )
```

Adapt the conditional to whatever the existing code uses to gate per-agent tools (agent name, agent_def field, etc.).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/tessy_tools.py \
        kc-supervisor/tests/test_tessy_tools.py \
        kc-supervisor/src/kc_supervisor/main.py \
        kc-supervisor/src/kc_supervisor/assembly.py
git commit -m "feat(kc-supervisor): Tessy tool wrappers (tesla.price/update_pricing/confirm_pricing/update_offers_from_image)"
```

---

## Task C7: Tessy subagent template + integration

**Files:**
- Create: `~/KonaClaw/subagent-templates/tessy.yaml`

- [ ] **Step 1: Write the template**

Create `~/KonaClaw/subagent-templates/tessy.yaml`:

```yaml
name: tessy
description: |
  Tesla pricing and offers specialist. Calculates monthly payments, lease
  deals, financing rates, and tracks current Tesla promotions. Can update
  local pricing data via natural language and ingest Tesla offers
  screenshots. Invoke when Sammy asks about Tesla pricing, current offers,
  or wants to update the local Tesla data.
system_prompt: |
  You are Tessy, KonaClaw's Tesla specialist agent.

  Tesla pricing data lives in `workspace/tesla_pricing.json` and current
  offers in `workspace/tesla_offers.json` / `workspace/tesla_offers.md`.
  These files are local; you do not need to scrape Tesla's site directly.

  Your tools:
    - tesla.price                    — calculate pricing/financing for a config
    - tesla.update_pricing           — propose pricing changes via NLP
    - tesla.confirm_pricing          — apply the last proposed change
    - tesla.update_offers_from_image — parse a Tesla offers screenshot

  Workflow guidance:
    - When Sammy asks pricing/financing questions, use tesla.price.
      Pass either `nlp` with the full natural-language description, OR
      structured params (trim, paint, wheels, interior, zip, months, down, apr).
    - When Sammy reports a price change ("Model Y RWD is now $41,990"),
      call tesla.update_pricing with that text. You'll get back a proposed
      diff. SHOW Sammy the diff and ASK for confirmation before calling
      tesla.confirm_pricing. Never confirm on your own initiative.
    - When Sammy attaches a Tesla offers screenshot, call
      tesla.update_offers_from_image with the attachment id from the
      [attached: ...] line in his message.
    - Always cite which data version you used. If pricing data feels stale
      (e.g., last updated > 30 days ago), say so.
tools:
  tesla.price: {}
  tesla.update_pricing: {}
  tesla.confirm_pricing: {}
  tesla.update_offers_from_image: {}
timeout_seconds: 300
max_tool_calls: 20
```

- [ ] **Step 2: Verify the template loads**

Restart the supervisor. The subagent-templates loader scans the directory on startup; new templates should appear in the supervisor logs (look for "loaded subagent template: tessy" or similar).

If the supervisor's existing template loader has issues with the new file, look at how `coder.yaml` and `email-drafter.yaml` are loaded and adapt.

- [ ] **Step 3: Manual invocation test**

In a Kona chat, say: *"Ask Tessy what a Model Y RWD costs in 95128 with $7k down for 72 months."*

Expected: Kona uses `delegate_subagent(template="tessy", task=...)`. Tessy spawns, calls `tesla.price(...)`, returns a JSON pricing breakdown, and Kona summarizes it for Sammy.

If Tessy can't find its tools, check the assembly.py wiring from Task C6 Step 5 — the conditional needs to actually fire for `agent_def.name == "tessy"`.

- [ ] **Step 4: Commit** (template lives outside the repo — no commit needed for the YAML; record in a docs commit if useful)

No git commit for this task.

---

## Task C8: Tesla unit tests

**Files:**
- Create: `workspace/tests/test_tesla.py`

- [ ] **Step 1: Write tests for the workspace scripts**

Create `workspace/tests/test_tesla.py`:

```python
"""Unit tests for the ported tesla workspace scripts."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


WORKSPACE = Path(__file__).parent.parent


def _has_keys() -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY")) and bool(os.environ.get("GEMINI_API_KEY"))


@pytest.mark.skipif(not _has_keys(), reason="needs FIRECRAWL_API_KEY + GEMINI_API_KEY")
def test_tesla_price_runs_with_structured_args():
    """A live call to Tesla pricing. Network-dependent."""
    r = subprocess.run(
        [sys.executable, "tesla_price.py", "--silent",
         "--trim", "rwd", "--zip", "95128", "--months", "72", "--down", "7000"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    # Output should be JSON or contain a price summary
    assert r.stdout.strip(), "empty stdout"


def test_update_tesla_pricing_rejects_nonexistent_path(tmp_path, monkeypatch):
    """The --nlp path validation should reject paths not in tesla_pricing.json."""
    # This test runs the script but expects a graceful error since the proposed
    # patch will reference a path that doesn't exist in the live JSON.
    # It's best-effort — the actual validation is Gemini-dependent so we just
    # check the script doesn't crash hard on a synthetic input.
    if not _has_keys():
        pytest.skip("needs GEMINI_API_KEY for NLP parsing")
    r = subprocess.run(
        [sys.executable, "update_tesla_pricing.py", "--nlp",
         "change the price of a completely nonexistent model variant to $999999"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=30,
    )
    # Either rejects (nonzero exit) or returns a pending diff with an error field.
    # Both are acceptable; what's NOT acceptable is a Python exception.
    assert "Traceback" not in r.stderr, f"script crashed: {r.stderr}"


def test_update_tesla_from_screenshot_requires_image_arg():
    r = subprocess.run(
        [sys.executable, "update_tesla_from_screenshot.py"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=5,
    )
    assert r.returncode != 0
    assert "Usage" in r.stderr or "image_path" in r.stderr
```

- [ ] **Step 2: Run the tests**

```bash
arch -arm64 python3 -m pytest workspace/tests/test_tesla.py -v
```

Expected: at minimum the `requires_image_arg` test passes. The other two skip without keys.

- [ ] **Step 3: Commit**

```bash
git add workspace/tests/test_tesla.py
git commit -m "test(workspace): unit tests for tesla scripts (live tests skipped without keys)"
```

---

# Milestone wrap-up

## Task M1: Full test sweep + memory update

- [ ] **Step 1: Run all suites**

```bash
arch -arm64 python3 -m pytest workspace/tests/ -v
arch -arm64 python3 -m pytest kc-supervisor/tests/test_portfolio_routes.py kc-supervisor/tests/test_tessy_tools.py -v
cd kc-dashboard && arch -arm64 npm test --run 2>&1 | tail -10
```

Expected: all PASS (pre-existing Chat.test.tsx flakes acceptable).

- [ ] **Step 2: Update memory**

Update `~/.claude/projects/-Users-sammydallal-Desktop-claudeCode-SammyClaw/memory/MEMORY.md` with a `project_zeroclaw_port.md` entry. Capture: phase ranges, final commit SHA, knobs (KC_PORTFOLIO_CACHE_S, KC_TERMINAL_ENABLED), and that Tessy needs `gemini_api_key` in secrets.

- [ ] **Step 3: Hand off to Sammy**

Tell Sammy:
- Phase A: money-manager skill landed. Try *"What's my portfolio worth?"* in a Kona chat (KC_TERMINAL_ENABLED must be true).
- Phase B: Portfolio widget at http://localhost:5173/portfolio. Auto-refresh every 5 min, cached for 60s, manual refresh button.
- Phase C: Tessy is a delegable subagent. Try *"Ask Tessy what a Model Y RWD costs in 95128, $7k down, 72 months."* Tessy will use tesla.price (auto-allowed, SAFE) and return a pricing breakdown. Pricing/offers updates surface as approval cards before they apply.

---

## Notes for the executor

- **Branch:** continue on whatever branch is checked out when the plan starts (currently `phase-subagents`). Don't switch.
- **`arch -arm64` prefix** is required for all `npm`, `node`, `pip`, `python3 -m pip` invocations. The harness defaults to x86_64 emulation on this Mac which causes native-binding issues. See the `never-rosetta` memory.
- **ZeroClaw is read-only** — every `ssh` command in this plan uses `cat` to read files; nothing is `scp`-pushed back. Don't add any write operations on the remote.
- **Hardcoded API keys in this plan's task text** (FIRECRAWL, GEMINI, BOT_TOKEN) come from ZeroClaw's checked-in scripts. Don't preserve them in KonaClaw — the sanitization rules in Tasks C2/C3 explicitly remove them.
- **Tessy's mutating tools surface approval cards.** That's by design (per spec). If Sammy finds it annoying after using it for a while, a follow-up phase can mark them SAFE within Tessy's permission scope.
- **Don't try to mirror ZeroClaw's digest.py / digest_sections/.** That was descoped during brainstorming.
