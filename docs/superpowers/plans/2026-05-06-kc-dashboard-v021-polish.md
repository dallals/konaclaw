# kc-dashboard v0.2.1 polish — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New `/connectors` master-detail tab (Telegram · iMessage · Gmail · Calendar · Zapier), drill-down `/connectors/zapier` zaps page, and denied-row surfacing in the existing Audit view. Backed by new `/connectors/...` HTTP endpoints in kc-supervisor that read/write the encrypted secrets store landed in the sibling backend plan.

**Architecture:** New `connectors_routes.py` module on the supervisor exposing typed JSON endpoints. Plaintext secrets never leave the supervisor after save — `GET /connectors/{name}` returns `has_token` + a 4-char `token_hint` only. Google OAuth stays server-side: dashboard POSTs to kick `InstalledAppFlow.run_local_server` in a background thread, then polls `/connectors/google/status`. Telegram/iMessage/Zapier hot-restart on PATCH so changes take effect without a supervisor restart. Frontend uses the existing TanStack Query + Zustand stack; no new top-level deps.

**Tech Stack:** Python (FastAPI), TypeScript (React 18, react-router 6, TanStack Query), Tailwind 3, Vitest.

**Spec:** `docs/superpowers/specs/2026-05-06-kc-dashboard-v021-polish-design.md`
**Depends on:** `docs/superpowers/plans/2026-05-06-kc-supervisor-v021-polish.md` (provides `SecretsStore.save`, `Storage.list_audit(decision=...)`, `Deps.secrets_store`)

---

## File map

| File | Role |
|---|---|
| `kc-supervisor/src/kc_supervisor/connectors_routes.py` | **New.** All `/connectors/*` routes. |
| `kc-supervisor/src/kc_supervisor/service.py` | **Modify.** Mount `connectors_routes`. |
| `kc-supervisor/src/kc_supervisor/main.py` | **Modify.** Build `GoogleOAuthState` on Deps; expose connector restart hooks. |
| `kc-supervisor/tests/test_connectors_routes.py` | **New.** Endpoint shape + masking + OAuth state machine + audit-join. |
| `kc-dashboard/src/main.tsx` | **Modify.** Add `/connectors` and `/connectors/zapier` routes. |
| `kc-dashboard/src/App.tsx` | **Modify.** Add Connectors nav tab. |
| `kc-dashboard/src/api/connectors.ts` | **New.** Fetchers + TanStack Query hooks. |
| `kc-dashboard/src/api/audit.ts` | **Modify.** Accept optional `decision` filter. |
| `kc-dashboard/src/views/Connectors.tsx` | **New.** Master-detail container. |
| `kc-dashboard/src/views/Zaps.tsx` | **New.** Zapier drill-down. |
| `kc-dashboard/src/views/Audit.tsx` | **Modify.** Denied-row pill + filter chip. |
| `kc-dashboard/src/components/connectors/ConnectorList.tsx` | **New.** Left rail. |
| `kc-dashboard/src/components/connectors/SecretInput.tsx` | **New.** Masked input + Save. |
| `kc-dashboard/src/components/connectors/AllowlistEditor.tsx` | **New.** Chip list. |
| `kc-dashboard/src/components/connectors/TelegramPanel.tsx` | **New.** |
| `kc-dashboard/src/components/connectors/IMessagePanel.tsx` | **New.** |
| `kc-dashboard/src/components/connectors/GooglePanel.tsx` | **New.** |
| `kc-dashboard/src/components/connectors/ZapierPanel.tsx` | **New.** |

---

## Wave A: Supervisor HTTP endpoints

### Task 1: Skeleton + GET /connectors

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/src/kc_supervisor/service.py`
- Create: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Write failing test**

Write `kc-supervisor/tests/test_connectors_routes.py`:

```python
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from kc_supervisor.service import build_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build the app with isolated state. Reuse the existing pattern from
    test_http.py — copy its `make_deps` helper if needed."""
    # See test_http.py::client for the canonical wiring; we only need
    # secrets_store and storage to be present.
    from tests.test_http import _make_app  # if exported, otherwise inline
    app = _make_app(tmp_path)
    return TestClient(app)


def test_get_connectors_lists_five_with_status(client):
    res = client.get("/connectors")
    assert res.status_code == 200
    body = res.json()
    names = [c["name"] for c in body["connectors"]]
    assert names == ["telegram", "imessage", "gmail", "calendar", "zapier"]
    for c in body["connectors"]:
        assert "status" in c
        assert c["status"] in ("not_configured", "connected", "unavailable", "error")
```

> **Note on the fixture:** if `test_http.py` doesn't export a builder, copy its 10-line setup pattern into a `conftest.py` helper. The endpoint test itself is the contract.

- [ ] **Step 2: Run test; confirm failure**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_connectors_routes.py -v
```

Expected: 404 on `/connectors`.

- [ ] **Step 3: Create the module**

Write `kc-supervisor/src/kc_supervisor/connectors_routes.py`:

```python
from __future__ import annotations
import platform
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/connectors")

CONNECTOR_NAMES = ("telegram", "imessage", "gmail", "calendar", "zapier")


def _token_hint(value: str | None) -> str | None:
    if not value or len(value) < 4:
        return None
    return "..." + value[-4:]


def _connector_summary(name: str, secrets: dict[str, Any], deps: Any) -> dict[str, Any]:
    if name == "telegram":
        token = secrets.get("telegram_bot_token")
        allowlist = secrets.get("telegram_allowlist") or []
        return {
            "name": name,
            "status": "connected" if token else "not_configured",
            "has_token": bool(token),
            "allowlist_count": len(allowlist),
            "summary": f"{len(allowlist)} chat(s) allowlisted" if token else "no token configured",
        }
    if name == "imessage":
        if platform.system() != "Darwin":
            return {"name": name, "status": "unavailable", "has_token": False,
                    "allowlist_count": 0, "summary": "macOS only"}
        allowlist = secrets.get("imessage_allowlist") or []
        return {"name": name,
                "status": "connected" if allowlist else "not_configured",
                "has_token": False,
                "allowlist_count": len(allowlist),
                "summary": f"{len(allowlist)} handle(s) allowlisted" if allowlist else "no handles allowlisted"}
    if name in ("gmail", "calendar"):
        token_path = (deps and getattr(deps, "google_token_path", None))
        connected = bool(token_path and token_path.exists())
        return {"name": name,
                "status": "connected" if connected else "not_configured",
                "has_token": connected,
                "allowlist_count": 0,
                "summary": "OAuth tokens cached" if connected else "not connected"}
    if name == "zapier":
        api_key = secrets.get("zapier_api_key")
        zap_count = 0
        if deps and getattr(deps, "mcp_manager", None) is not None:
            zap_count = sum(1 for n in deps.mcp_manager.names() if n == "zapier")
            # Actual zap-tool count is computed on the dedicated /zaps endpoint
        return {"name": name,
                "status": "connected" if api_key else "not_configured",
                "has_token": bool(api_key),
                "allowlist_count": 0,
                "summary": "API key set" if api_key else "no API key"}
    raise ValueError(f"unknown connector: {name}")


def install(app, deps: Any) -> None:
    """Mount the connectors router. Called from service.py at app build time."""

    @router.get("")
    def list_connectors():
        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        return {
            "connectors": [_connector_summary(n, secrets, deps) for n in CONNECTOR_NAMES],
        }

    app.include_router(router)
```

- [ ] **Step 4: Mount in service.py**

In `kc-supervisor/src/kc_supervisor/service.py`, find where other routers/routes are wired (look for `http_routes` or `ws_routes` includes) and add:

```python
from kc_supervisor import connectors_routes
connectors_routes.install(app, deps)
```

- [ ] **Step 5: Run tests; confirm pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_connectors_routes.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/connectors_routes.py kc-supervisor/src/kc_supervisor/service.py kc-supervisor/tests/test_connectors_routes.py
git commit -m "feat(kc-supervisor): GET /connectors returns 5-connector summary"
```

---

### Task 2: GET /connectors/{name} detail

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_get_telegram_detail_masks_token(client):
    # Save a known secret first.
    client.patch("/connectors/telegram", json={"bot_token": "8123:abcdefghij"})
    res = client.get("/connectors/telegram")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "telegram"
    assert body["has_token"] is True
    assert body["token_hint"] == "...ghij"
    assert "bot_token" not in body  # plaintext NEVER returned
    assert "allowlist" in body


def test_get_unknown_connector_returns_404(client):
    res = client.get("/connectors/nope")
    assert res.status_code == 404
```

- [ ] **Step 2: Run; confirm failure**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_connectors_routes.py -v
```

- [ ] **Step 3: Add detail handler**

Inside the `install()` function in `connectors_routes.py`, **before** the closing `app.include_router(router)` line, add:

```python
    @router.get("/{name}")
    def get_connector(name: str):
        if name not in CONNECTOR_NAMES:
            raise HTTPException(404, detail=f"unknown connector: {name}")
        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        summary = _connector_summary(name, secrets, deps)
        if name == "telegram":
            summary["token_hint"] = _token_hint(secrets.get("telegram_bot_token"))
            summary["allowlist"] = list(secrets.get("telegram_allowlist") or [])
        elif name == "imessage":
            summary["allowlist"] = list(secrets.get("imessage_allowlist") or [])
            summary["flags"] = {"platform_supported": platform.system() == "Darwin"}
        elif name == "zapier":
            summary["token_hint"] = _token_hint(secrets.get("zapier_api_key"))
        elif name in ("gmail", "calendar"):
            summary["flags"] = {"oauth": True}
        return summary
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_connectors_routes.py -v
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(kc-supervisor): GET /connectors/{name} with masked tokens + allowlists"
```

---

### Task 3: PATCH /connectors/{name}

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_patch_telegram_persists_through_secrets_store(client):
    res = client.patch("/connectors/telegram", json={
        "bot_token": "8000:secret",
        "allowlist": ["@alice", "@bob"],
    })
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    detail = client.get("/connectors/telegram").json()
    assert detail["has_token"] is True
    assert detail["token_hint"] == "...cret"
    assert detail["allowlist"] == ["@alice", "@bob"]


def test_patch_zapier_api_key(client):
    client.patch("/connectors/zapier", json={"api_key": "zk_live_xyz"})
    detail = client.get("/connectors/zapier").json()
    assert detail["has_token"] is True
    assert detail["token_hint"] == "..._xyz"


def test_patch_imessage_allowlist_only(client):
    client.patch("/connectors/imessage", json={"allowlist": ["+15551234567"]})
    detail = client.get("/connectors/imessage").json()
    assert detail["allowlist"] == ["+15551234567"]


def test_patch_unknown_connector_returns_404(client):
    res = client.patch("/connectors/nope", json={"x": 1})
    assert res.status_code == 404
```

- [ ] **Step 2: Run tests; confirm failure**

- [ ] **Step 3: Add PATCH handler**

```python
class TelegramPatch(BaseModel):
    bot_token: str | None = None
    allowlist: list[str] | None = None


class IMessagePatch(BaseModel):
    allowlist: list[str] | None = None


class ZapierPatch(BaseModel):
    api_key: str | None = None


_PATCH_KEYS: dict[str, dict[str, str]] = {
    "telegram": {"bot_token": "telegram_bot_token", "allowlist": "telegram_allowlist"},
    "imessage": {"allowlist": "imessage_allowlist"},
    "zapier":   {"api_key": "zapier_api_key"},
}


def _restart_connector(name: str, deps: Any) -> None:
    """Best-effort hot-restart for telegram/imessage so PATCH takes effect
    without a supervisor reboot. Errors are logged, not raised — secret was
    saved either way. Wire-up of these hooks lives in main.py (Task 7)."""
    hook = getattr(deps, f"restart_{name}", None)
    if hook is None:
        return
    try:
        hook()
    except Exception:
        # The connector restart hook should log internally; we don't
        # surface failure to the dashboard because the secret is saved.
        pass


    @router.patch("/{name}")
    def patch_connector(name: str, payload: dict[str, Any]):
        if name not in _PATCH_KEYS:
            raise HTTPException(404, detail=f"unknown connector: {name}")
        if name == "telegram":
            data = TelegramPatch(**payload).dict(exclude_none=True)
        elif name == "imessage":
            data = IMessagePatch(**payload).dict(exclude_none=True)
        elif name == "zapier":
            data = ZapierPatch(**payload).dict(exclude_none=True)
        else:
            raise HTTPException(404)

        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        for body_key, secret_key in _PATCH_KEYS[name].items():
            if body_key in data:
                secrets[secret_key] = data[body_key]
        deps.secrets_store.save(secrets)

        if name in ("telegram", "imessage"):
            _restart_connector(name, deps)
        return {"ok": True}
```

> Note: the `_restart_connector` hooks are populated in Task 7. Until then, PATCH still saves the secret correctly.

- [ ] **Step 4: Run tests; confirm pass**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(kc-supervisor): PATCH /connectors/{name} writes through SecretsStore"
```

---

### Task 4: Google OAuth state machine + endpoints

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py`
- Modify: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Define `GoogleOAuthState` on Deps**

In `main.py`, add near other dataclass imports:

```python
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class GoogleOAuthState:
    state: Literal["idle", "pending", "connected"] = "idle"
    since: float = 0.0
    last_error: Optional[str] = None
```

Add a field on `Deps`:

```python
google_oauth: GoogleOAuthState = field(default_factory=GoogleOAuthState)
```

- [ ] **Step 2: Write failing tests**

```python
def test_google_status_initial_is_idle(client):
    body = client.get("/connectors/google/status").json()
    assert body["state"] == "idle"


def test_google_connect_returns_202_pending(client, monkeypatch):
    # Patch the InstalledAppFlow runner so the test doesn't open a browser.
    import kc_supervisor.connectors_routes as cr
    monkeypatch.setattr(cr, "_run_google_flow", lambda deps: None)

    res = client.post("/connectors/google/connect")
    assert res.status_code == 202
    assert res.json()["state"] == "pending"


def test_google_connect_double_click_is_noop_while_pending(client, monkeypatch):
    import kc_supervisor.connectors_routes as cr
    started = []
    monkeypatch.setattr(cr, "_run_google_flow",
                        lambda deps: started.append(1))
    client.post("/connectors/google/connect")
    client.post("/connectors/google/connect")
    # Only one flow kicked off.
    assert len(started) == 1


def test_google_disconnect_resets_state(client, tmp_path):
    # Pretend a token file existed.
    token = tmp_path / "google_token.json"
    token.write_text("{}")
    # The fixture should expose deps.google_token_path = token; if not,
    # this assertion documents the expected behavior.
    res = client.post("/connectors/google/disconnect")
    assert res.status_code == 200
    assert client.get("/connectors/google/status").json()["state"] == "idle"
```

- [ ] **Step 3: Run; confirm failure**

- [ ] **Step 4: Implement endpoints**

Append to `connectors_routes.py`:

```python
import threading
import time


def _run_google_flow(deps: Any) -> None:
    """Run InstalledAppFlow.run_local_server, then update deps.google_oauth.

    Imported lazily so the supervisor still boots without google-auth-oauthlib.
    """
    state = deps.google_oauth
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        flow = InstalledAppFlow.from_client_secrets_file(
            str(deps.google_credentials_path),
            scopes=getattr(deps, "google_scopes",
                           ["https://www.googleapis.com/auth/gmail.modify",
                            "https://www.googleapis.com/auth/calendar"]),
        )
        creds = flow.run_local_server(host="localhost", port=0, open_browser=True)
        deps.google_token_path.write_text(creds.to_json())
        state.state = "connected"
        state.since = time.time()
        state.last_error = None
    except Exception as exc:
        state.state = "idle"
        state.last_error = f"{type(exc).__name__}: {exc}"


    @router.post("/google/connect", status_code=202)
    def google_connect():
        state = deps.google_oauth
        if state.state == "pending":
            return {"state": "pending", "since": state.since}
        state.state = "pending"
        state.since = time.time()
        state.last_error = None
        threading.Thread(target=_run_google_flow, args=(deps,), daemon=True).start()
        return {"state": "pending", "since": state.since}

    @router.get("/google/status")
    def google_status():
        s = deps.google_oauth
        return {"state": s.state, "since": s.since, "last_error": s.last_error}

    @router.post("/google/disconnect")
    def google_disconnect():
        token_path = getattr(deps, "google_token_path", None)
        if token_path is not None and token_path.exists():
            token_path.unlink()
        deps.google_oauth.state = "idle"
        deps.google_oauth.since = time.time()
        deps.google_oauth.last_error = None
        return {"ok": True}
```

- [ ] **Step 5: Run tests; confirm pass**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(kc-supervisor): /connectors/google/{connect,status,disconnect} endpoints"
```

---

### Task 5: GET /connectors/zapier/zaps with audit join

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Modify: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Add a storage helper for the audit aggregation**

Append to `storage.py`:

```python
    def audit_aggregate_by_tool_prefix(
        self, prefix: str,
    ) -> list[dict]:
        """Per-tool MAX(ts) and COUNT(*) for tools matching prefix%."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT tool, MAX(ts) AS last_ts, COUNT(*) AS n "
                "FROM audit WHERE tool LIKE ? AND decision='allowed' "
                "GROUP BY tool",
                (prefix + "%",),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 2: Write storage test**

Append to `tests/test_storage.py`:

```python
def test_audit_aggregate_by_tool_prefix(tmp_path):
    s = Storage(tmp_path / "db.sqlite"); s.init()
    s.append_audit(agent="a", tool="mcp.zapier.gmail_send", args_json="{}", decision="allowed", result="ok", undoable=False)
    s.append_audit(agent="a", tool="mcp.zapier.gmail_send", args_json="{}", decision="allowed", result="ok", undoable=False)
    s.append_audit(agent="a", tool="mcp.zapier.notion_create", args_json="{}", decision="allowed", result="ok", undoable=False)
    s.append_audit(agent="a", tool="other.tool", args_json="{}", decision="allowed", result="ok", undoable=False)

    out = sorted(s.audit_aggregate_by_tool_prefix("mcp.zapier."), key=lambda r: r["tool"])
    assert len(out) == 2
    assert out[0]["tool"] == "mcp.zapier.gmail_send"
    assert out[0]["n"] == 2
    assert out[1]["n"] == 1
```

- [ ] **Step 3: Run; confirm pass after impl**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_storage.py -v -k aggregate
```

- [ ] **Step 4: Add the route + test**

Add the route to `connectors_routes.py`:

```python
    @router.get("/zapier/zaps")
    def list_zaps():
        # Find live mcp.zapier.* tools through deps.mcp_manager.
        live: list[dict[str, Any]] = []
        manager = getattr(deps, "mcp_manager", None)
        if manager is not None and "zapier" in manager.names():
            handle = manager.get("zapier")
            for tool in handle.tools_cache or []:
                # tool comes from kc_mcp.tool_adapter — name is "mcp.zapier.<x>"
                live.append({
                    "tool": tool.name,
                    "description": tool.description or "",
                })
        # Join with audit aggregation.
        agg = {r["tool"]: r for r in deps.storage.audit_aggregate_by_tool_prefix("mcp.zapier.")}
        for entry in live:
            row = agg.get(entry["tool"])
            entry["last_used_ts"] = row["last_ts"] if row else None
            entry["call_count"] = row["n"] if row else 0
        return {"zaps": live}
```

Add the route test:

```python
def test_zapier_zaps_returns_empty_when_unconfigured(client):
    body = client.get("/connectors/zapier/zaps").json()
    assert body == {"zaps": []}
```

- [ ] **Step 5: Run tests; confirm pass**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(kc-supervisor): GET /connectors/zapier/zaps joins live MCP tools with audit"
```

---

### Task 6: POST /connectors/zapier/refresh

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/connectors_routes.py`
- Modify: `kc-supervisor/tests/test_connectors_routes.py`

- [ ] **Step 1: Write failing test**

```python
def test_zapier_refresh_calls_registry_load_all(client, monkeypatch):
    calls = []
    # The fixture's deps.registry should expose load_all; if it doesn't, mock it.
    monkeypatch.setattr(client.app.state.deps.registry, "load_all",
                        lambda: calls.append(1))
    res = client.post("/connectors/zapier/refresh")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert calls == [1]
```

- [ ] **Step 2: Add handler**

```python
    @router.post("/zapier/refresh")
    def refresh_zapier():
        deps.registry.load_all()
        return {"ok": True, "refreshed_at": time.time()}
```

- [ ] **Step 3: Run; commit**

```bash
git commit -am "feat(kc-supervisor): POST /connectors/zapier/refresh triggers registry.load_all()"
```

---

### Task 7: Wire connector hot-restart hooks on Deps

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py`

- [ ] **Step 1: Find the existing connector boot block**

Run:

```bash
grep -n "TelegramConnector\|IMessageConnector\|connector_registry" kc-supervisor/src/kc_supervisor/main.py
```

- [ ] **Step 2: Refactor the connector boot to a builder + restart pair**

The existing main.py wires connectors at module top-level (or inside `build_app()` — find the actual scope first with `grep -n "TelegramConnector(" kc-supervisor/src/kc_supervisor/main.py`). Use a small mutable holder so the closure can rebind without `nonlocal` issues regardless of scope:

```python
# Replace the existing one-shot wiring with this builder + restart pattern.
def _build_telegram(secrets_dict: dict):
    token = secrets_dict.get("telegram_bot_token")
    if not token:
        return None
    return TelegramConnector(
        token=token,
        allowlist=secrets_dict.get("telegram_allowlist") or [],
    )

# Mutable holder so the restart closure works at any scope.
_telegram_holder: list[Any] = [_build_telegram(secrets)]
if _telegram_holder[0]:
    connector_registry.register(_telegram_holder[0])

def _restart_telegram() -> None:
    fresh = deps.secrets_store.load()
    old = _telegram_holder[0]
    if old is not None:
        try: connector_registry.unregister("telegram")
        except Exception: pass
        try:
            import asyncio
            asyncio.get_event_loop().create_task(old.stop())
        except Exception: pass
    new = _build_telegram(fresh)
    _telegram_holder[0] = new
    if new is not None:
        connector_registry.register(new)
        try:
            import asyncio
            asyncio.get_event_loop().create_task(new.start(deps.inbound_router))
        except Exception: pass

deps.restart_telegram = _restart_telegram
```

Repeat the same builder + holder + restart pattern for iMessage. Set `deps.restart_imessage = _restart_imessage`.

> If `connector_registry.unregister` doesn't exist, add a thin helper that pops the entry by name. Restart errors are swallowed because the PATCH-side `_restart_connector` already treats failures as best-effort.

- [ ] **Step 3: Smoke-test PATCH end-to-end**

Boot supervisor. From another terminal:

```bash
curl -X PATCH http://127.0.0.1:8765/connectors/telegram \
  -H 'Content-Type: application/json' \
  -d '{"bot_token":"<test token>","allowlist":["@you"]}'
curl -s http://127.0.0.1:8765/connectors/telegram | jq
```

Expected: `has_token: true`, allowlist populated. Bot replies to messages from `@you` without supervisor restart.

- [ ] **Step 4: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-supervisor): expose deps.restart_{telegram,imessage} hooks for PATCH hot-restart"
```

---

## Wave B: Frontend scaffolding

### Task 8: Routes + nav tab

**Files:**
- Modify: `kc-dashboard/src/main.tsx`
- Modify: `kc-dashboard/src/App.tsx`

- [ ] **Step 1: Create stub views so routes compile**

Write `kc-dashboard/src/views/Connectors.tsx`:

```tsx
export default function Connectors() {
  return <div className="p-6 text-muted">Connectors view — populated in Task 11.</div>;
}
```

Write `kc-dashboard/src/views/Zaps.tsx`:

```tsx
export default function Zaps() {
  return <div className="p-6 text-muted">Zaps view — populated in Task 15.</div>;
}
```

- [ ] **Step 2: Edit `main.tsx`**

Add imports:

```tsx
import Connectors from "./views/Connectors";
import Zaps from "./views/Zaps";
```

Add routes inside `<Route path="/" element={<App />}>`, between agents and shares:

```tsx
<Route path="connectors" element={<Connectors />} />
<Route path="connectors/zapier" element={<Zaps />} />
```

- [ ] **Step 3: Add nav tab**

In `kc-dashboard/src/App.tsx`, edit the `tabs` array:

```tsx
const tabs = [
  { to: "/chat", label: "Chat" },
  { to: "/agents", label: "Agents" },
  { to: "/connectors", label: "Connectors" },
  { to: "/shares", label: "Shares" },
  { to: "/permissions", label: "Permissions" },
  { to: "/monitor", label: "Monitor" },
  { to: "/audit", label: "Audit" },
];
```

- [ ] **Step 4: Smoke-build**

```bash
cd kc-dashboard && npm run build
```

Expected: build succeeds. Open dev server (`npm run dev`); verify Connectors tab appears and routes render the stub.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/main.tsx kc-dashboard/src/App.tsx kc-dashboard/src/views/Connectors.tsx kc-dashboard/src/views/Zaps.tsx
git commit -m "feat(kc-dashboard): scaffold Connectors + Zaps routes and nav tab"
```

---

### Task 9: api/connectors.ts client

**Files:**
- Create: `kc-dashboard/src/api/connectors.ts`

- [ ] **Step 1: Write the typed client**

```ts
import { apiGet, apiPatch, apiPost } from "./client";

export type ConnectorStatus = "not_configured" | "connected" | "unavailable" | "error";

export type ConnectorSummary = {
  name: "telegram" | "imessage" | "gmail" | "calendar" | "zapier";
  status: ConnectorStatus;
  has_token: boolean;
  allowlist_count: number;
  summary: string;
};

export type ConnectorDetail = ConnectorSummary & {
  token_hint?: string;
  allowlist?: string[];
  flags?: { platform_supported?: boolean; oauth?: boolean };
};

export type GoogleOAuthStatus = {
  state: "idle" | "pending" | "connected";
  since: number;
  last_error: string | null;
};

export type Zap = {
  tool: string;
  description: string;
  last_used_ts: number | null;
  call_count: number;
};

export const listConnectors = () =>
  apiGet<{ connectors: ConnectorSummary[] }>("/connectors");

export const getConnector = (name: string) =>
  apiGet<ConnectorDetail>(`/connectors/${name}`);

export const patchConnector = (name: string, body: Record<string, unknown>) =>
  apiPatch<{ ok: boolean }>(`/connectors/${name}`, body);

export const googleConnect = () =>
  apiPost<{ state: string; since: number }>("/connectors/google/connect", {});

export const googleStatus = () =>
  apiGet<GoogleOAuthStatus>("/connectors/google/status");

export const googleDisconnect = () =>
  apiPost<{ ok: boolean }>("/connectors/google/disconnect", {});

export const listZaps = () =>
  apiGet<{ zaps: Zap[] }>("/connectors/zapier/zaps");

export const refreshZaps = () =>
  apiPost<{ ok: boolean; refreshed_at: number }>("/connectors/zapier/refresh", {});
```

- [ ] **Step 2: Confirm `apiPatch` exists in `client.ts`**

```bash
grep -n "apiPatch\|export const apiGet" kc-dashboard/src/api/client.ts
```

If `apiPatch` is missing, add it next to `apiPost`:

```ts
export const apiPatch = <T,>(path: string, body: unknown) =>
  fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body),
  }).then(r => r.json() as Promise<T>);
```

- [ ] **Step 3: Commit**

```bash
git add kc-dashboard/src/api/connectors.ts kc-dashboard/src/api/client.ts
git commit -m "feat(kc-dashboard): typed /connectors API client"
```

---

### Task 10: SecretInput + AllowlistEditor primitives

**Files:**
- Create: `kc-dashboard/src/components/connectors/SecretInput.tsx`
- Create: `kc-dashboard/src/components/connectors/AllowlistEditor.tsx`
- Create: `kc-dashboard/src/components/connectors/SecretInput.test.tsx`

- [ ] **Step 1: Write failing vitest**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SecretInput from "./SecretInput";

describe("SecretInput", () => {
  it("shows masked placeholder when has_value is true", () => {
    render(<SecretInput label="Bot token" hasValue tokenHint="...abcd" onSave={() => {}} />);
    expect(screen.getByPlaceholderText(/abcd/)).toBeInTheDocument();
  });

  it("calls onSave with the typed value when Save is clicked", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SecretInput label="API key" hasValue={false} onSave={onSave} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "zk_xyz" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("zk_xyz"));
  });
});
```

- [ ] **Step 2: Implement SecretInput**

```tsx
import { useState } from "react";

type Props = {
  label: string;
  hasValue: boolean;
  tokenHint?: string;
  onSave: (value: string) => Promise<unknown> | void;
};

export default function SecretInput({ label, hasValue, tokenHint, onSave }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const placeholder = hasValue
    ? (tokenHint ? `••••••••${tokenHint}` : "•••••••• (saved)")
    : "paste token...";

  const save = async () => {
    if (!value) return;
    setBusy(true);
    try {
      await onSave(value);
      setValue("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <label className="text-xs uppercase text-muted tracking-wide">{label}</label>
      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 px-3 py-2 rounded bg-bg border border-line font-mono text-sm"
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button
          className="px-3 py-2 rounded bg-accent text-bg text-sm font-semibold disabled:opacity-50"
          disabled={!value || busy}
          onClick={save}
        >Save</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Implement AllowlistEditor**

```tsx
import { useState } from "react";

type Props = {
  label: string;
  values: string[];
  onChange: (next: string[]) => Promise<unknown> | void;
};

export default function AllowlistEditor({ label, values, onChange }: Props) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const commit = async (next: string[]) => {
    setBusy(true);
    try { await onChange(next); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-2">
      <label className="text-xs uppercase text-muted tracking-wide">{label}</label>
      <div className="flex flex-wrap gap-1.5">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-panel border border-line text-xs">
            {v}
            <button
              className="text-bad hover:opacity-80"
              onClick={() => commit(values.filter((x) => x !== v))}
              disabled={busy}
            >×</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="flex-1 px-3 py-1.5 rounded bg-bg border border-line text-sm"
          placeholder="add entry..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft) {
              commit([...values, draft]); setDraft("");
            }
          }}
        />
        <button
          className="px-3 py-1.5 rounded bg-accent text-bg text-sm"
          disabled={!draft || busy}
          onClick={() => { commit([...values, draft]); setDraft(""); }}
        >Add</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run vitest; confirm pass**

```bash
cd kc-dashboard && npm test -- --run SecretInput
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(kc-dashboard): SecretInput + AllowlistEditor primitives"
```

---

## Wave C: Connector panels and views

### Task 11: ConnectorList + Connectors view shell

**Files:**
- Create: `kc-dashboard/src/components/connectors/ConnectorList.tsx`
- Modify: `kc-dashboard/src/views/Connectors.tsx`

- [ ] **Step 1: Implement ConnectorList**

```tsx
import { ConnectorSummary } from "../../api/connectors";

const ICONS: Record<ConnectorSummary["name"], string> = {
  telegram: "📱", imessage: "💬", gmail: "📧", calendar: "📅", zapier: "⚡",
};

type Props = {
  items: ConnectorSummary[];
  selected: string;
  onSelect: (name: string) => void;
};

export default function ConnectorList({ items, selected, onSelect }: Props) {
  return (
    <div className="p-2 space-y-1">
      {items.map((c) => {
        const tone = c.status === "connected" ? "bg-good"
          : c.status === "unavailable" ? "bg-line"
          : c.status === "error" ? "bg-bad"
          : "bg-muted";
        return (
          <button
            key={c.name}
            onClick={() => onSelect(c.name)}
            className={"w-full flex items-center justify-between px-3 py-2 rounded text-sm "
              + (selected === c.name ? "bg-panel border border-accent" : "hover:bg-panel border border-transparent")}
          >
            <span className="flex items-center gap-2">
              <span>{ICONS[c.name]}</span>
              <span className="capitalize">{c.name}</span>
            </span>
            <span className={"w-2 h-2 rounded-full " + tone}></span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Wire `Connectors.tsx`**

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listConnectors } from "../api/connectors";
import ConnectorList from "../components/connectors/ConnectorList";
import TelegramPanel from "../components/connectors/TelegramPanel";
import IMessagePanel from "../components/connectors/IMessagePanel";
import GooglePanel from "../components/connectors/GooglePanel";
import ZapierPanel from "../components/connectors/ZapierPanel";

export default function Connectors() {
  const [selected, setSelected] = useState<string>("telegram");
  const { data } = useQuery({
    queryKey: ["connectors"], queryFn: listConnectors, refetchInterval: 5000,
  });
  const items = data?.connectors ?? [];

  return (
    <div className="grid grid-cols-[220px_1fr] h-full">
      <aside className="border-r border-line bg-bg">
        <ConnectorList items={items} selected={selected} onSelect={setSelected} />
      </aside>
      <section className="overflow-auto p-6">
        {selected === "telegram" && <TelegramPanel />}
        {selected === "imessage" && <IMessagePanel />}
        {(selected === "gmail" || selected === "calendar") && <GooglePanel which={selected as "gmail" | "calendar"} />}
        {selected === "zapier" && <ZapierPanel />}
      </section>
    </div>
  );
}
```

> The four `*Panel` components are scaffolded next; the import resolves once Tasks 12–14 land.

- [ ] **Step 3: Commit (allow build to break temporarily)**

```bash
git add kc-dashboard/src/components/connectors/ConnectorList.tsx kc-dashboard/src/views/Connectors.tsx
git commit -m "feat(kc-dashboard): Connectors master-detail shell + ConnectorList"
```

> Build will fail until Task 14 lands. That's expected within this wave; tasks here form one logical commit chain.

---

### Task 12: TelegramPanel + IMessagePanel

**Files:**
- Create: `kc-dashboard/src/components/connectors/TelegramPanel.tsx`
- Create: `kc-dashboard/src/components/connectors/IMessagePanel.tsx`

- [ ] **Step 1: TelegramPanel**

```tsx
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import SecretInput from "./SecretInput";
import AllowlistEditor from "./AllowlistEditor";

export default function TelegramPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "telegram"], queryFn: () => getConnector("telegram"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("telegram", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      qc.invalidateQueries({ queryKey: ["connectors", "telegram"] });
    },
  });

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">📱 Telegram</h2>
        <p className="text-sm text-muted">Bot for sending/receiving messages on allowlisted chats.</p>
      </header>
      <SecretInput
        label="Bot token"
        hasValue={data?.has_token ?? false}
        tokenHint={data?.token_hint}
        onSave={(value) => patch.mutateAsync({ bot_token: value })}
      />
      <AllowlistEditor
        label="Allowed chat IDs"
        values={data?.allowlist ?? []}
        onChange={(next) => patch.mutateAsync({ allowlist: next })}
      />
    </div>
  );
}
```

- [ ] **Step 2: IMessagePanel**

```tsx
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import AllowlistEditor from "./AllowlistEditor";

export default function IMessagePanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "imessage"], queryFn: () => getConnector("imessage"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("imessage", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors", "imessage"] }),
  });

  if (data?.flags?.platform_supported === false) {
    return (
      <div className="space-y-3 max-w-xl">
        <h2 className="text-lg font-semibold">💬 iMessage</h2>
        <div className="p-3 rounded bg-panel border border-line text-sm text-muted">
          iMessage requires macOS. This connector is unavailable on the current platform.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">💬 iMessage</h2>
        <p className="text-sm text-muted">macOS Messages.app integration. Requires Full Disk Access.</p>
      </header>
      <AllowlistEditor
        label="Allowed handles"
        values={data?.allowlist ?? []}
        onChange={(next) => patch.mutateAsync({ allowlist: next })}
      />
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add kc-dashboard/src/components/connectors/TelegramPanel.tsx kc-dashboard/src/components/connectors/IMessagePanel.tsx
git commit -m "feat(kc-dashboard): Telegram + iMessage panels (token + allowlist)"
```

---

### Task 13: GooglePanel with poll loop

**Files:**
- Create: `kc-dashboard/src/components/connectors/GooglePanel.tsx`

- [ ] **Step 1: Implement**

```tsx
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  googleConnect, googleStatus, googleDisconnect,
} from "../../api/connectors";

export default function GooglePanel({ which }: { which: "gmail" | "calendar" }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["google-oauth-status"],
    queryFn: googleStatus,
    refetchInterval: (q) => (q.state.data?.state === "pending" ? 2000 : false),
  });

  // When state flips to connected, refetch the per-connector summary so
  // the right-panel and left-rail status pills update together.
  useEffect(() => {
    if (status.data?.state === "connected") {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      qc.invalidateQueries({ queryKey: ["connectors", "gmail"] });
      qc.invalidateQueries({ queryKey: ["connectors", "calendar"] });
    }
  }, [status.data?.state, qc]);

  const connect = useMutation({ mutationFn: googleConnect, onSuccess: () => status.refetch() });
  const disconnect = useMutation({ mutationFn: googleDisconnect,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["connectors"] }); status.refetch(); } });

  const heading = which === "gmail" ? "📧 Gmail" : "📅 Calendar";
  const state = status.data?.state ?? "idle";

  return (
    <div className="space-y-4 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">{heading}</h2>
        <p className="text-sm text-muted">
          One Google OAuth covers Gmail + Calendar. Connecting one connects both.
        </p>
      </header>

      {state === "idle" && (
        <button
          onClick={() => connect.mutate()}
          className="px-4 py-2 rounded bg-accent text-bg font-semibold"
          disabled={connect.isPending}
        >Connect with Google</button>
      )}

      {state === "pending" && (
        <div className="p-3 rounded bg-panel border border-line text-sm">
          Waiting for OAuth completion. A browser tab should have opened —
          finish the flow there.
        </div>
      )}

      {state === "connected" && (
        <div className="space-y-3">
          <div className="p-3 rounded bg-panel border border-good text-sm">Connected</div>
          <button
            onClick={() => disconnect.mutate()}
            className="px-3 py-1.5 rounded border border-bad text-bad text-sm"
          >Disconnect</button>
        </div>
      )}

      {status.data?.last_error && (
        <div className="p-3 rounded bg-panel border border-bad text-sm text-bad">
          {status.data.last_error}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add kc-dashboard/src/components/connectors/GooglePanel.tsx
git commit -m "feat(kc-dashboard): GooglePanel with OAuth state machine + poll loop"
```

---

### Task 14: ZapierPanel + verify build

**Files:**
- Create: `kc-dashboard/src/components/connectors/ZapierPanel.tsx`

- [ ] **Step 1: Implement**

```tsx
import { Link } from "react-router-dom";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import SecretInput from "./SecretInput";

export default function ZapierPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "zapier"], queryFn: () => getConnector("zapier"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("zapier", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors"] }),
  });

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">⚡ Zapier</h2>
        <p className="text-sm text-muted">
          {data?.has_token ? "API key set." : "API key required to enable Zapier MCP tools."}
        </p>
      </header>
      <SecretInput
        label="Zapier API key"
        hasValue={data?.has_token ?? false}
        tokenHint={data?.token_hint}
        onSave={(value) => patch.mutateAsync({ api_key: value })}
      />
      <Link to="/connectors/zapier"
            className="inline-block px-3 py-2 rounded border border-line text-sm hover:border-accent">
        Manage zaps →
      </Link>
    </div>
  );
}
```

- [ ] **Step 2: Build**

```bash
cd kc-dashboard && npm run build
```

Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add kc-dashboard/src/components/connectors/ZapierPanel.tsx
git commit -m "feat(kc-dashboard): ZapierPanel with API key + Manage zaps deep link"
```

---

### Task 15: Zaps drill-down view

**Files:**
- Modify: `kc-dashboard/src/views/Zaps.tsx`

- [ ] **Step 1: Implement**

```tsx
import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { listZaps, refreshZaps, getConnector, patchConnector, type Zap } from "../api/connectors";
import SecretInput from "../components/connectors/SecretInput";

function fmtAgo(ts: number | null): string {
  if (!ts) return "never";
  const sec = Math.floor(Date.now() / 1000) - ts;
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export default function Zaps() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const zaps = useQuery({ queryKey: ["zaps"], queryFn: listZaps });
  const detail = useQuery({ queryKey: ["connectors", "zapier"], queryFn: () => getConnector("zapier") });

  const refresh = useMutation({
    mutationFn: refreshZaps,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["zaps"] }),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("zapier", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors", "zapier"] }),
  });

  const items: Zap[] = zaps.data?.zaps ?? [];
  const filtered = useMemo(() => {
    const needle = q.toLowerCase();
    if (!needle) return items;
    return items.filter(z =>
      z.tool.toLowerCase().includes(needle)
      || z.description.toLowerCase().includes(needle));
  }, [items, q]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-3 text-sm">
        <Link to="/connectors" className="text-muted hover:text-text">← Connectors</Link>
        <span className="text-line">/</span>
        <h2 className="text-lg font-semibold">⚡ Zapier</h2>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Zaps available</div>
          <div className="text-2xl font-semibold mt-1">{items.length}</div>
        </div>
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Last refresh</div>
          <div className="text-sm mt-2">just now</div>
        </div>
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Transport</div>
          <div className="text-sm mt-2">Streamable HTTP</div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          className="flex-1 px-3 py-2 rounded bg-bg border border-line text-sm"
          placeholder="Search zaps..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button
          className="px-3 py-2 rounded border border-line text-sm hover:border-accent"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >↻ Refresh</button>
        <a className="px-3 py-2 text-accent text-sm" href="https://zapier.com/app/dashboard" target="_blank" rel="noopener">
          Add zap on zapier.com ↗
        </a>
      </div>

      <div className="rounded border border-line overflow-hidden">
        <div className="grid grid-cols-[1fr_2fr_120px_80px] gap-2 px-3 py-2 bg-panel text-xs uppercase text-muted">
          <div>Tool</div><div>Description</div><div>Last used</div><div>Calls</div>
        </div>
        {filtered.length === 0 ? (
          <div className="px-3 py-6 text-center text-muted text-sm">No zaps {q ? "match this filter" : "configured"}.</div>
        ) : (
          filtered.map(z => (
            <div key={z.tool} className="grid grid-cols-[1fr_2fr_120px_80px] gap-2 px-3 py-2 border-t border-line text-sm">
              <code className="text-accent">{z.tool}</code>
              <span className="text-muted">{z.description}</span>
              <span className="text-muted">{fmtAgo(z.last_used_ts)}</span>
              <span className="text-muted">{z.call_count}</span>
            </div>
          ))
        )}
      </div>

      <div className="max-w-xl pt-4">
        <SecretInput
          label="Zapier API key"
          hasValue={detail.data?.has_token ?? false}
          tokenHint={detail.data?.token_hint}
          onSave={(value) => patch.mutateAsync({ api_key: value })}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Build + manual smoke**

```bash
cd kc-dashboard && npm run build && npm run dev
```

Open `/connectors/zapier`. Verify the page renders, refresh button is wired, search filters.

- [ ] **Step 3: Commit**

```bash
git add kc-dashboard/src/views/Zaps.tsx
git commit -m "feat(kc-dashboard): /connectors/zapier zaps drill-down view"
```

---

## Wave D: Audit denied-row + filter chip

### Task 16: Update audit API client

**Files:**
- Modify: `kc-dashboard/src/api/audit.ts`

- [ ] **Step 1: Replace `listAudit`**

```ts
import { apiGet, apiPost } from "./client";

export type AuditEntry = {
  id: number; ts: number; agent: string; tool: string;
  args_json: string; decision: string; result: string | null;
  undoable: number; undone: number;
};

export type DecisionFilter = "all" | "allowed" | "denied";

export const listAudit = (
  agent?: string, limit = 100, decision: DecisionFilter = "all",
) => {
  const params = new URLSearchParams();
  if (agent) params.set("agent", agent);
  params.set("limit", String(limit));
  if (decision !== "all") params.set("decision", decision);
  return apiGet<{ entries: AuditEntry[] }>(`/audit?${params.toString()}`);
};

export const undoAudit = (id: number) => apiPost<{ undone: boolean }>(`/undo/${id}`, {});
```

- [ ] **Step 2: Commit**

```bash
git add kc-dashboard/src/api/audit.ts
git commit -m "feat(kc-dashboard): listAudit accepts decision filter"
```

---

### Task 17: Audit view denied-row pill + filter chip

**Files:**
- Modify: `kc-dashboard/src/views/Audit.tsx`

- [ ] **Step 1: Inspect existing Audit view**

```bash
cat kc-dashboard/src/views/Audit.tsx
```

Identify the `useQuery` call and the row-rendering JSX.

- [ ] **Step 2: Replace Audit.tsx**

Overwrite `kc-dashboard/src/views/Audit.tsx` with the merged version below. This preserves the existing Undo logic (mutation + per-row error display) and adds the filter chip + denied-row pill on top.

```tsx
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAudit, undoAudit, type DecisionFilter, type AuditEntry } from "../api/audit";

export default function Audit() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filter = (params.get("decision") as DecisionFilter | null) ?? "all";
  const setFilter = (next: DecisionFilter) => {
    if (next === "all") params.delete("decision");
    else params.set("decision", next);
    setParams(params, { replace: true });
  };

  const q = useQuery({
    queryKey: ["audit", filter],
    queryFn: () => listAudit(undefined, 200, filter),
    refetchInterval: 3000,
  });

  const [rowError, setRowError] = useState<{ id: number; msg: string } | null>(null);

  const undo = useMutation({
    mutationFn: undoAudit,
    onSuccess: () => {
      setRowError(null);
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (e: Error, id) => {
      const m = e.message.match(/→ \d+: (.*)$/s);
      let detail = e.message;
      try { if (m) detail = JSON.parse(m[1]).detail ?? detail; } catch { /* keep raw */ }
      setRowError({ id, msg: detail });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const reasonOf = (entry: AuditEntry): string | null => {
    if (entry.decision !== "denied" || !entry.result) return null;
    try { return (JSON.parse(entry.result) as { reason?: string }).reason ?? null; }
    catch { return null; }
  };

  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Audit log</h2>

      <div className="flex items-center gap-2 mb-3">
        {(["all", "allowed", "denied"] as DecisionFilter[]).map(opt => (
          <button
            key={opt}
            onClick={() => setFilter(opt)}
            className={"px-3 py-1 rounded text-xs border "
              + (filter === opt ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{opt[0].toUpperCase() + opt.slice(1)}</button>
        ))}
      </div>

      <table className="w-full text-xs font-mono">
        <thead className="text-muted text-[10px] uppercase">
          <tr>
            <th className="text-left py-2">Time</th>
            <th className="text-left">Agent</th>
            <th className="text-left">Tool</th>
            <th className="text-left">Decision</th>
            <th className="text-left">Result</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {q.data?.entries.map((e) => {
            const denied = e.decision === "denied";
            const reason = reasonOf(e);
            return (
              <tr key={e.id} className="border-t border-line">
                <td className="py-2 text-muted">{new Date(e.ts * 1000).toLocaleTimeString()}</td>
                <td className="text-good">{e.agent}</td>
                <td className="text-cyan-300">{e.tool}</td>
                <td>
                  <span
                    className={"px-2 py-0.5 rounded inline-block "
                      + (denied
                        ? "bg-bad/20 text-bad border border-bad/40"
                        : "bg-good/20 text-good border border-good/40")}
                    title={denied ? (reason ?? "no reason recorded") : undefined}
                  >{e.decision}</span>
                </td>
                <td className="text-text">{e.result ?? "—"}</td>
                <td>
                  {denied ? (
                    "—"
                  ) : e.undone ? (
                    <span className="text-muted italic">✓ undone</span>
                  ) : e.undoable ? (
                    <button
                      className="text-accent hover:underline disabled:opacity-50"
                      disabled={undo.isPending && undo.variables === e.id}
                      onClick={() => { setRowError(null); undo.mutate(e.id); }}
                    >
                      {undo.isPending && undo.variables === e.id ? "Undoing…" : "↩ Undo"}
                    </button>
                  ) : (
                    "—"
                  )}
                  {rowError && rowError.id === e.id && (
                    <div className="text-[10px] text-bad mt-1 normal-case">{rowError.msg}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Build**

```bash
cd kc-dashboard && npm run build
```

- [ ] **Step 4: Manual smoke**

`npm run dev`. Trigger a tool that requires approval; click Deny in Permissions. Open Audit. Expect a red `denied` pill. Hover for reason tooltip. Click `Denied` chip in the filter row; URL updates to `?decision=denied`; only denied rows visible.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/views/Audit.tsx
git commit -m "feat(kc-dashboard): Audit view renders denied-row pill + filter chip"
```

---

## Task 18: Final test sweep + smoke

- [ ] **Step 1: kc-supervisor full suite**

```bash
cd kc-supervisor && .venv/bin/pytest -q
```

Expected: previous count + ~14 new tests, all green.

- [ ] **Step 2: kc-dashboard full suite**

```bash
cd kc-dashboard && npm test -- --run
```

Expected: green.

- [ ] **Step 3: End-to-end manual smoke**

Boot the launcher. Walk through:

1. Open `/connectors`. All 5 connectors visible. iMessage panel hides token field on macOS but shows allowlist editor.
2. Paste a Telegram bot token → Save → bot starts replying without supervisor restart.
3. Click Connect with Google → consent in browser tab → return to dashboard → Gmail + Calendar pills both flip to green.
4. Open `/connectors/zapier`. Search for "gmail". Refresh. Edit API key.
5. Trigger a destructive tool from Chat. Click Deny in Permissions. Open Audit. Filter to Denied only. Verify the red pill + tooltip + URL persistence.

- [ ] **Step 4: Update SMOKE.md**

Append a "Connectors view" section to `kc-dashboard/SMOKE.md` mirroring the manual steps above.

- [ ] **Step 5: Commit**

```bash
git commit -am "docs(kc-dashboard): SMOKE.md additions for v0.2.1 polish"
```

---

## Done criteria

- All kc-supervisor pytest + kc-dashboard vitest suites pass.
- `/connectors` shows 5 connectors with accurate status pills.
- Editing Telegram token + allowlist hot-restarts the bot (no supervisor restart needed).
- Gmail + Calendar connect via dashboard button; pills update without page reload.
- `/connectors/zapier` lists live `mcp.zapier.*` tools joined with audit (Last used, Calls).
- Refresh button calls `registry.load_all()` server-side.
- Audit view renders denied rows with red pill + reason tooltip; filter chip persists in URL.
- Plaintext secrets never appear in any HTTP response — only `has_token` + `token_hint` (last 4 chars).
