# Ollama Web Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second backend (`OllamaClient`) to `kc-web` so KonaClaw can search the web via `https://ollama.com/api/web_search` with the existing `WebClient` Protocol, while keeping `FirecrawlClient` as a peer.

**Architecture:** Polymorphic `WebClient`. `WebConfig` gains a `backend: Literal["ollama","firecrawl"]` selector (default `ollama`, env override `KC_WEB_BACKEND`) and carries both keys. `build_web_tools` branches once on `cfg.backend`. Tool descriptions go neutral; backend-specific quirks (Ollama's 10-cap on `max_results`, no freshness, no `include_links`) stay local to `OllamaClient`.

**Tech Stack:** Python 3.11+, httpx (new direct dep for kc-web), pytest + pytest-asyncio, existing kc-web modules.

**Spec:** `docs/superpowers/specs/2026-05-15-ollama-web-backend-design.md`

---

## File Map

| File | Action |
|---|---|
| `kc-web/src/kc_web/client.py` | Modify — add `WebClientError`, `OllamaClient`; keep `FirecrawlError` alias. |
| `kc-web/src/kc_web/config.py` | Modify — refactor `WebConfig` for backend selector + dual keys + raised caps. |
| `kc-web/src/kc_web/tools.py` | Modify — branch on backend; neutralize tool descriptions. |
| `kc-web/src/kc_web/search.py` | Modify — error import + JSON error string `firecrawl_error` → `backend_error`. |
| `kc-web/src/kc_web/fetch.py` | Modify — same as `search.py`. |
| `kc-web/pyproject.toml` | Modify — add `httpx>=0.27` to dependencies. |
| `kc-web/tests/test_ollama_client.py` | Create — ~12 tests using `httpx.MockTransport`. |
| `kc-web/tests/test_config.py` | Modify — extend with backend-selector cases. |
| `kc-web/tests/test_tool_integration.py` | Modify — backend-pick cases. |
| `kc-web/tests/test_search.py` | Modify — error import + assertion update. |
| `kc-web/tests/test_fetch.py` | Modify — same as `test_search.py`. |
| `kc-supervisor/src/kc_supervisor/main.py` | Modify — read both keys + `KC_WEB_BACKEND`; pass to `WebConfig.from_env`. |
| `docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md` | Create — six SMOKE gates. |

---

## Task 1: Introduce `WebClientError` generic exception

Adds a backend-neutral error class. Keeps `FirecrawlError` as an alias so existing tests still pass via `isinstance`.

**Files:**
- Modify: `kc-web/src/kc_web/client.py`
- Test: `kc-web/tests/test_web_client_error.py` (new)

- [ ] **Step 1: Write the failing test**

Create `kc-web/tests/test_web_client_error.py`:

```python
from kc_web.client import FirecrawlError, WebClientError


def test_web_client_error_carries_status_and_message():
    e = WebClientError(429, "rate limited")
    assert e.status == 429
    assert e.message == "rate limited"
    assert "429" in str(e)
    assert "rate limited" in str(e)


def test_firecrawl_error_is_alias_of_web_client_error():
    assert FirecrawlError is WebClientError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kc-web && pytest tests/test_web_client_error.py -v`
Expected: FAIL — `WebClientError` is not importable.

- [ ] **Step 3: Implement `WebClientError` + alias**

In `kc-web/src/kc_web/client.py`, replace the `FirecrawlError` class with:

```python
class WebClientError(Exception):
    """Generic error raised by any WebClient implementation.

    `status` is an HTTP status code when the backend returned a non-2xx
    response, or 0 for network/JSON/other errors that have no HTTP status.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"web backend error status={status}: {message}")
        self.status = status
        self.message = message


# Kept as an alias for one cycle so external imports / isinstance checks
# don't break. New code should use WebClientError directly.
FirecrawlError = WebClientError
```

(Leave `FirecrawlClient` as-is for now — it still raises `FirecrawlError`, which is `WebClientError` via the alias.)

- [ ] **Step 4: Run the new test**

Run: `cd kc-web && pytest tests/test_web_client_error.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full kc-web suite to catch breakage from message-format change**

Run: `cd kc-web && pytest -v`
Expected: Two tests fail in `test_search.py::test_firecrawl_error` and `test_fetch.py::test_firecrawl_error` because they assert the old `"firecrawl status=..."` message format. We'll fix these in Task 2.

- [ ] **Step 6: Commit**

```bash
git add kc-web/src/kc_web/client.py kc-web/tests/test_web_client_error.py
git commit -m "feat(kc-web): add WebClientError generic; FirecrawlError as alias"
```

---

## Task 2: Migrate search.py / fetch.py + tests to `backend_error`

Renames the JSON error key from `firecrawl_error` to `backend_error` and switches imports to `WebClientError`. Fixes the two failing tests from Task 1.

**Files:**
- Modify: `kc-web/src/kc_web/search.py:8` (import line)
- Modify: `kc-web/src/kc_web/search.py:57` (error string)
- Modify: `kc-web/src/kc_web/fetch.py:8` (import line)
- Modify: `kc-web/src/kc_web/fetch.py:61` (error string)
- Modify: `kc-web/tests/test_search.py:7, 105, 108`
- Modify: `kc-web/tests/test_fetch.py:7, 148, 151`

- [ ] **Step 1: Update import + return string in `search.py`**

In `kc-web/src/kc_web/search.py`, change line 8:

```python
from kc_web.client import WebClientError, WebClient
```

And in the `except` block (around line 55-60), change:

```python
        except WebClientError as e:
            return _json({
                "error": "backend_error",
                "status": e.status,
                "message": str(e),
            })
```

- [ ] **Step 2: Update import + return string in `fetch.py`**

In `kc-web/src/kc_web/fetch.py`, change line 8:

```python
from kc_web.client import WebClientError, WebClient
```

And in the `except` block (around line 59-65), change:

```python
        except WebClientError as e:
            return _json({
                "error": "backend_error",
                "status": e.status,
                "message": str(e),
            })
```

- [ ] **Step 3: Update `test_search.py`**

In `kc-web/tests/test_search.py`:
- Line 7: `from kc_web.client import WebClientError, SearchResult`
- Line 105 (inside `test_firecrawl_error`, which we'll rename): `client = FakeClient(exc=WebClientError(429, "rate limited"))`
- Line 108: `assert out == {"error": "backend_error", "status": 429, "message": "web backend error status=429: rate limited"}`
- Rename the test function from `test_firecrawl_error` to `test_backend_error`.

- [ ] **Step 4: Update `test_fetch.py`**

In `kc-web/tests/test_fetch.py`:
- Line 7: `from kc_web.client import WebClientError, ScrapeResult`
- Line 148: `client = FakeClient(exc=WebClientError(502, "bad gateway"))`
- Line 151: `assert out["error"] == "backend_error"`
- Rename the test function from `test_firecrawl_error` to `test_backend_error`.

- [ ] **Step 5: Run the full kc-web suite**

Run: `cd kc-web && pytest -v`
Expected: All tests PASS (75+).

- [ ] **Step 6: Commit**

```bash
git add kc-web/src/kc_web/search.py kc-web/src/kc_web/fetch.py \
        kc-web/tests/test_search.py kc-web/tests/test_fetch.py
git commit -m "refactor(kc-web): rename firecrawl_error -> backend_error in tool JSON"
```

---

## Task 3: Refactor `WebConfig` for backend selector + dual keys

Replaces the single `firecrawl_api_key` with a `backend` selector, `ollama_api_key`, and `firecrawl_api_key`. Raises default caps. Adds env-var support for `KC_WEB_BACKEND`. Updates `kc-supervisor/main.py` in the same commit to keep the repo bootable.

**Files:**
- Modify: `kc-web/src/kc_web/config.py` (full rewrite of `WebConfig`)
- Modify: `kc-web/tests/test_config.py` (rewrite to match new shape)
- Modify: `kc-supervisor/src/kc_supervisor/main.py:72-89`

- [ ] **Step 1: Write the failing tests**

Replace contents of `kc-web/tests/test_config.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-web && pytest tests/test_config.py -v`
Expected: All FAIL — old `WebConfig` doesn't have `backend` field.

- [ ] **Step 3: Rewrite `config.py`**

Replace entire contents of `kc-web/src/kc_web/config.py` with:

```python
from __future__ import annotations
import os
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal


Backend = Literal["ollama", "firecrawl"]
_VALID_BACKENDS = ("ollama", "firecrawl")


def _gen_session_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class WebConfig:
    backend: Backend
    ollama_api_key: str | None
    firecrawl_api_key: str | None
    session_soft_cap: int
    daily_hard_cap: int
    fetch_cap_bytes: int
    default_search_max_results: int
    default_fetch_timeout_s: int
    budget_db_path: Path
    extra_blocked_hosts: tuple[str, ...]
    session_id: str = field(default_factory=_gen_session_id)

    @classmethod
    def with_defaults(
        cls,
        *,
        backend: str = "ollama",
        ollama_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
    ) -> "WebConfig":
        return cls(
            backend=backend,  # type: ignore[arg-type]
            ollama_api_key=ollama_api_key,
            firecrawl_api_key=firecrawl_api_key,
            session_soft_cap=100,
            daily_hard_cap=1000,
            fetch_cap_bytes=32 * 1024,
            default_search_max_results=10,
            default_fetch_timeout_s=30,
            budget_db_path=Path.home() / ".kona" / "web_budget.sqlite",
            extra_blocked_hosts=(),
        )

    @classmethod
    def from_env(
        cls,
        *,
        ollama_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
        backend: str | None = None,
    ) -> "WebConfig":
        """Build WebConfig from explicit keys + optional KC_WEB_* env overrides.

        Backend resolution: explicit `backend` kwarg wins, then KC_WEB_BACKEND
        env, then default 'ollama'.

        Keys come from the supervisor's encrypted secrets store
        (~/KonaClaw/config/secrets.yaml.enc) — not env — matching the pattern
        used for newsapi_api_key, telegram_bot_token, etc.

        Raises:
            ValueError: if backend is not 'ollama' or 'firecrawl', or if the
                selected backend's key is missing/whitespace.
        """
        chosen = backend or os.environ.get("KC_WEB_BACKEND", "ollama")
        if chosen not in _VALID_BACKENDS:
            raise ValueError(
                f"invalid KC_WEB_BACKEND: {chosen!r} (expected one of {_VALID_BACKENDS})"
            )
        if chosen == "ollama":
            if not ollama_api_key or not ollama_api_key.strip():
                raise ValueError("ollama_api_key required when backend=ollama")
        else:
            if not firecrawl_api_key or not firecrawl_api_key.strip():
                raise ValueError("firecrawl_api_key required when backend=firecrawl")
        base = cls.with_defaults(
            backend=chosen,
            ollama_api_key=ollama_api_key,
            firecrawl_api_key=firecrawl_api_key,
        )
        blocked_raw = os.environ.get("KC_WEB_BLOCKED_HOSTS", "")
        blocked = tuple(h.strip() for h in blocked_raw.split(",") if h.strip())
        return replace(
            base,
            session_soft_cap=int(
                os.environ.get("KC_WEB_SESSION_SOFT_CAP", base.session_soft_cap)
            ),
            daily_hard_cap=int(
                os.environ.get("KC_WEB_DAILY_HARD_CAP", base.daily_hard_cap)
            ),
            fetch_cap_bytes=int(
                os.environ.get("KC_WEB_FETCH_CAP_BYTES", base.fetch_cap_bytes)
            ),
            default_search_max_results=int(
                os.environ.get(
                    "KC_WEB_SEARCH_DEFAULT_N", base.default_search_max_results
                )
            ),
            default_fetch_timeout_s=int(
                os.environ.get(
                    "KC_WEB_FETCH_DEFAULT_TIMEOUT", base.default_fetch_timeout_s
                )
            ),
            budget_db_path=Path(
                os.environ.get("KC_WEB_BUDGET_DB", str(base.budget_db_path))
            ).expanduser(),
            extra_blocked_hosts=blocked,
        )
```

- [ ] **Step 4: Run config tests**

Run: `cd kc-web && pytest tests/test_config.py -v`
Expected: All PASS.

- [ ] **Step 5: Rewire `kc-supervisor/main.py`**

Replace the block at `kc-supervisor/src/kc_supervisor/main.py:72-89` with:

```python
    # Web tools (web_search + web_fetch) — gated by KC_WEB_ENABLED env flag.
    # Backend is selected by KC_WEB_BACKEND env var (default: "ollama").
    # The selected backend's key must be present in the encrypted secrets
    # store at ~/KonaClaw/config/secrets.yaml.enc.
    web_config = None
    if os.environ.get("KC_WEB_ENABLED", "").lower() in ("1", "true", "yes"):
        ollama_key = secrets.get("ollama_api_key") or ""
        firecrawl_key = secrets.get("firecrawl_api_key") or ""
        backend_choice = os.environ.get("KC_WEB_BACKEND", "ollama")
        try:
            from kc_web import WebConfig
            web_config = WebConfig.from_env(
                ollama_api_key=ollama_key,
                firecrawl_api_key=firecrawl_key,
                backend=backend_choice,
            )
        except ValueError as e:
            raise RuntimeError(
                f"KC_WEB_ENABLED=true but {e}. "
                f"Add the required key via the supervisor secrets store, "
                f"then restart."
            ) from e
        except Exception as e:
            raise RuntimeError(f"failed to build WebConfig: {e}") from e
```

- [ ] **Step 6: Run the full kc-web suite + a smoke import check on kc-supervisor**

Run: `cd kc-web && pytest -v`
Expected: All PASS.

Run: `cd kc-supervisor && python -c "from kc_supervisor.main import main; print('ok')"`
Expected: `ok` printed, no ImportError.

- [ ] **Step 7: Commit**

```bash
git add kc-web/src/kc_web/config.py kc-web/tests/test_config.py \
        kc-supervisor/src/kc_supervisor/main.py
git commit -m "refactor(kc-web): WebConfig backend selector + dual keys + raised caps"
```

---

## Task 4: Add `httpx` as explicit kc-web dependency

`OllamaClient` will import `httpx` directly. It's not currently in `kc-web/pyproject.toml`.

**Files:**
- Modify: `kc-web/pyproject.toml`

- [ ] **Step 1: Add httpx to deps**

In `kc-web/pyproject.toml`, change the `dependencies` line under `[project]`:

```toml
dependencies = ["kc-core", "firecrawl-py>=1.0,<3.0", "httpx>=0.27"]
```

- [ ] **Step 2: Reinstall kc-web in editable mode**

Run: `cd kc-web && pip install -e . --no-deps`
(The `--no-deps` flag follows the established kc-supervisor convention for local-only deps — see the Phase B operational note in the tools-rollout memory.)
Then: `pip install httpx>=0.27`
Expected: success.

- [ ] **Step 3: Verify httpx importable from inside kc-web**

Run: `cd kc-web && python -c "import httpx; print(httpx.__version__)"`
Expected: version string printed.

- [ ] **Step 4: Commit**

```bash
git add kc-web/pyproject.toml
git commit -m "deps(kc-web): add httpx as explicit dependency for OllamaClient"
```

---

## Task 5: `OllamaClient.search` — happy path

Add the client skeleton and the search method.

**Files:**
- Modify: `kc-web/src/kc_web/client.py` (add `OllamaClient`)
- Test: `kc-web/tests/test_ollama_client.py` (new)

- [ ] **Step 1: Write the failing test**

Create `kc-web/tests/test_ollama_client.py`:

```python
import json

import httpx
import pytest

from kc_web.client import OllamaClient, SearchResult


def _make_client(handler):
    """Build an OllamaClient whose AsyncClient uses an httpx MockTransport.

    `handler` is a sync callable `(httpx.Request) -> httpx.Response`.
    """
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, timeout=10.0)
    return OllamaClient(api_key="sk-test", http=http, base_url="https://ollama.example/api")


@pytest.mark.asyncio
async def test_search_happy_path_maps_results():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://a.example", "content": "snip1"},
                    {"title": "T2", "url": "https://b.example", "content": "snip2"},
                ]
            },
        )

    client = _make_client(handler)
    results = await client.search("hello", max_results=5, freshness="any")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://ollama.example/api/web_search"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"] == {"query": "hello", "max_results": 5}
    assert results == [
        SearchResult(title="T1", url="https://a.example", snippet="snip1"),
        SearchResult(title="T2", url="https://b.example", snippet="snip2"),
    ]


@pytest.mark.asyncio
async def test_search_empty_results_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    client = _make_client(handler)
    results = await client.search("nothing here", max_results=5, freshness="any")
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: FAIL — `OllamaClient` not importable.

- [ ] **Step 3: Implement `OllamaClient` skeleton + search**

First, add the two new imports near the existing imports at the top of `kc-web/src/kc_web/client.py`:

```python
import json as _json_mod  # alias to avoid shadowing in user code

import httpx
```

Then append the constants and class to the end of the same file:

```python
_OLLAMA_DEFAULT_BASE_URL = "https://ollama.com/api"
_OLLAMA_DEFAULT_TIMEOUT = 60.0


class OllamaClient:
    """WebClient implementation backed by Ollama's hosted web search API.

    Quirks vs the WebClient Protocol:
      - `max_results` is silently clamped to [1, 10] (Ollama hard cap).
      - `freshness` is silently ignored (no Ollama equivalent).
      - `include_links` is silently ignored (Ollama always returns links;
        ScrapeResult has no `links` field).
      - `status_code` in ScrapeResult is always 0 (Ollama does not report it).
      - `final_url` echoes the input url (Ollama does not report redirects).
    """

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = _OLLAMA_DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_OLLAMA_DEFAULT_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def search(
        self,
        query: str,
        max_results: int,
        freshness: str,
    ) -> list[SearchResult]:
        clamped = max(1, min(10, int(max_results)))
        body: dict[str, Any] = {"query": query, "max_results": clamped}
        http = await self._client()
        try:
            resp = await http.post(
                f"{self._base_url}/web_search",
                json=body,
                headers=self._headers(),
            )
        except httpx.TimeoutException:
            # Convert to asyncio.TimeoutError so the search.py wait_for wrapper
            # catches it via its existing `except asyncio.TimeoutError` branch.
            raise asyncio.TimeoutError() from None
        except httpx.HTTPError as e:
            raise WebClientError(0, str(e)) from e
        if resp.status_code >= 400:
            raise WebClientError(resp.status_code, resp.text[:512])
        try:
            data = resp.json()
        except (_json_mod.JSONDecodeError, ValueError) as e:
            raise WebClientError(0, f"invalid_json: {e}") from e
        items = data.get("results") or []
        return [
            SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content", "")),
            )
            for r in items
        ]
```

- [ ] **Step 4: Run tests**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: Both PASS.

- [ ] **Step 5: Run the full kc-web suite to confirm no regressions**

Run: `cd kc-web && pytest -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-web/src/kc_web/client.py kc-web/tests/test_ollama_client.py
git commit -m "feat(kc-web): OllamaClient.search happy path"
```

---

## Task 6: `OllamaClient.scrape` — happy path

Adds the scrape method against `/api/web_fetch`.

**Files:**
- Modify: `kc-web/src/kc_web/client.py` (append method)
- Modify: `kc-web/tests/test_ollama_client.py` (append tests)

- [ ] **Step 1: Write the failing test**

Append to `kc-web/tests/test_ollama_client.py`:

```python
from kc_web.client import ScrapeResult


@pytest.mark.asyncio
async def test_scrape_happy_path_maps_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "title": "Example",
                "content": "# Hello\n\nWorld.",
                "links": ["https://a.example", "https://b.example"],
            },
        )

    client = _make_client(handler)
    result = await client.scrape(
        "https://example.org/page",
        timeout_seconds=15,
        include_links=False,
    )

    assert captured["url"] == "https://ollama.example/api/web_fetch"
    assert captured["body"] == {"url": "https://example.org/page"}
    assert result == ScrapeResult(
        url="https://example.org/page",
        final_url="https://example.org/page",
        status_code=0,
        title="Example",
        markdown="# Hello\n\nWorld.",
    )


@pytest.mark.asyncio
async def test_scrape_include_links_silently_ignored():
    """Ollama always returns links; we don't surface them. Passing include_links=True
    must not change the request or raise."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"title": "T", "content": "C", "links": []})

    client = _make_client(handler)
    await client.scrape("https://example.org/", timeout_seconds=10, include_links=True)
    assert "include_links" not in captured["body"]
    assert captured["body"] == {"url": "https://example.org/"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: New tests FAIL — `scrape` not implemented.

- [ ] **Step 3: Implement `scrape`**

Append to the `OllamaClient` class in `kc-web/src/kc_web/client.py`:

```python
    async def scrape(
        self,
        url: str,
        timeout_seconds: int,
        include_links: bool,
    ) -> ScrapeResult:
        body: dict[str, Any] = {"url": url}  # include_links intentionally ignored
        http = await self._client()
        try:
            resp = await http.post(
                f"{self._base_url}/web_fetch",
                json=body,
                headers=self._headers(),
                timeout=float(timeout_seconds),
            )
        except httpx.TimeoutException:
            raise asyncio.TimeoutError() from None
        except httpx.HTTPError as e:
            raise WebClientError(0, str(e)) from e
        if resp.status_code >= 400:
            raise WebClientError(resp.status_code, resp.text[:512])
        try:
            data = resp.json()
        except (_json_mod.JSONDecodeError, ValueError) as e:
            raise WebClientError(0, f"invalid_json: {e}") from e
        return ScrapeResult(
            url=url,
            final_url=url,
            status_code=0,
            title=str(data.get("title", "")),
            markdown=str(data.get("content", "")),
        )
```

- [ ] **Step 4: Run tests**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-web/src/kc_web/client.py kc-web/tests/test_ollama_client.py
git commit -m "feat(kc-web): OllamaClient.scrape happy path"
```

---

## Task 7: `OllamaClient` — error mapping (HTTP errors, network, invalid JSON)

Cover non-2xx responses, network failures, and JSON decode failures.

**Files:**
- Modify: `kc-web/tests/test_ollama_client.py` (append tests)
- No client changes expected — the implementation already handles these. Test confirms behavior.

- [ ] **Step 1: Write the failing tests**

Append to `kc-web/tests/test_ollama_client.py`:

```python
from kc_web.client import WebClientError


@pytest.mark.asyncio
async def test_search_401_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 401
    assert "unauthorized" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_429_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 429


@pytest.mark.asyncio
async def test_search_5xx_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 503


@pytest.mark.asyncio
async def test_search_invalid_json_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 0
    assert "invalid_json" in exc_info.value.message


@pytest.mark.asyncio
async def test_scrape_4xx_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.scrape("https://x.example", timeout_seconds=10, include_links=False)
    assert exc_info.value.status == 404


@pytest.mark.asyncio
async def test_search_network_error_raises_web_client_error_status_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 0
    assert "dns failure" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_httpx_timeout_bubbles_as_asyncio_timeout():
    """httpx.TimeoutException must be converted to asyncio.TimeoutError so
    the search.py wait_for wrapper catches it via its existing branch."""
    import asyncio as _asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _make_client(handler)
    with pytest.raises(_asyncio.TimeoutError):
        await client.search("q", max_results=5, freshness="any")


@pytest.mark.asyncio
async def test_scrape_httpx_timeout_bubbles_as_asyncio_timeout():
    import asyncio as _asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _make_client(handler)
    with pytest.raises(_asyncio.TimeoutError):
        await client.scrape("https://x.example", timeout_seconds=5, include_links=False)
```

- [ ] **Step 2: Run tests**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: All PASS (Task 5/6 implementation already covers these paths).

- [ ] **Step 3: Commit**

```bash
git add kc-web/tests/test_ollama_client.py
git commit -m "test(kc-web): OllamaClient error mapping (4xx, 5xx, invalid JSON)"
```

---

## Task 8: `OllamaClient` — max_results clamp + freshness ignored

Confirm the documented quirks behave as designed.

**Files:**
- Modify: `kc-web/tests/test_ollama_client.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `kc-web/tests/test_ollama_client.py`:

```python
@pytest.mark.asyncio
async def test_search_clamps_max_results_to_10():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"results": []})

    client = _make_client(handler)
    await client.search("q", max_results=25, freshness="any")
    assert captured["body"]["max_results"] == 10


@pytest.mark.asyncio
async def test_search_clamps_max_results_floor_to_1():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"results": []})

    client = _make_client(handler)
    await client.search("q", max_results=0, freshness="any")
    assert captured["body"]["max_results"] == 1


@pytest.mark.asyncio
async def test_search_freshness_silently_ignored():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"results": []})

    client = _make_client(handler)
    # Should succeed and not include freshness/tbs in the body.
    await client.search("q", max_results=5, freshness="week")
    assert "freshness" not in captured["body"]
    assert "tbs" not in captured["body"]
    assert captured["body"] == {"query": "q", "max_results": 5}
```

- [ ] **Step 2: Run tests**

Run: `cd kc-web && pytest tests/test_ollama_client.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add kc-web/tests/test_ollama_client.py
git commit -m "test(kc-web): OllamaClient quirks — clamp + freshness ignored"
```

---

## Task 9: Wire backend selector in `build_web_tools` + neutralize tool descriptions

Make `build_web_tools` pick the client based on `cfg.backend`. Strip Firecrawl mentions from the tool descriptions.

**Files:**
- Modify: `kc-web/src/kc_web/tools.py`
- Modify: `kc-web/tests/test_tool_integration.py` (extend)

- [ ] **Step 1: Write the failing tests**

In `kc-web/tests/test_tool_integration.py`, append at the bottom:

```python
from kc_web.client import OllamaClient, FirecrawlClient
from kc_web.config import WebConfig
from kc_web.tools import build_web_tools


def test_build_web_tools_picks_ollama_when_backend_ollama(monkeypatch):
    cfg = WebConfig.with_defaults(backend="ollama", ollama_api_key="sk-o")
    tools = build_web_tools(cfg)
    # We can't introspect the client directly without exposing it; instead,
    # verify the tools build successfully and the JSON tool descriptions are
    # backend-neutral.
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"web_search", "web_fetch"}
    for t in tools:
        assert "Firecrawl" not in t.description
        assert "firecrawl" not in t.description.lower()


def test_build_web_tools_picks_firecrawl_when_backend_firecrawl():
    cfg = WebConfig.with_defaults(backend="firecrawl", firecrawl_api_key="fc-k")
    tools = build_web_tools(cfg)
    assert len(tools) == 2
```

Also, update the assertions in `test_tool_integration.py`'s existing tests that build `WebConfig` — replace any `WebConfig.with_defaults(api_key="...")` calls with `WebConfig.with_defaults(backend="firecrawl", firecrawl_api_key="...")` (since the existing FakeClient pattern there mocks Firecrawl-shape responses). Use `grep -n "with_defaults\|from_env" tests/test_tool_integration.py` to find call sites.

- [ ] **Step 2: Run tests to verify they fail (or already pass if grep update was complete)**

Run: `cd kc-web && pytest tests/test_tool_integration.py -v`
Expected: New tests FAIL — tools.py still constructs `FirecrawlClient` unconditionally and descriptions still say "via Firecrawl".

- [ ] **Step 3: Rewrite `tools.py`**

Replace contents of `kc-web/src/kc_web/tools.py` with:

```python
from __future__ import annotations
from typing import Any

from kc_core.tools import Tool

from kc_web.budget import BudgetStore
from kc_web.client import FirecrawlClient, OllamaClient, WebClient
from kc_web.config import WebConfig
from kc_web.fetch import build_web_fetch_impl
from kc_web.search import build_web_search_impl


_SEARCH_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search query. Use Google operators like `site:docs.python.org` "
                "to scope to a domain. REQUIRED, non-empty."
            ),
        },
        "max_results": {
            "type": "integer",
            "description": "Optional. Default 10, clamped to [1, 25].",
        },
        "freshness": {
            "type": "string",
            "enum": ["any", "day", "week", "month", "year"],
            "description": (
                "Optional. Restrict to results from the last day/week/month/year. "
                "Default `any`. May be ignored by some backends."
            ),
        },
    },
    "required": ["query"],
}


_SEARCH_DESCRIPTION = (
    "Search the web. Returns a list of {title, url, snippet} results. "
    "Read-only, no approval prompt. Counts against the per-session and per-day "
    "budget caps. Use `site:` operator in the query to scope to a domain."
)


_FETCH_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "Absolute http(s) URL to fetch. Local/private hosts (localhost, "
                "127.0.0.1, RFC1918, *.local, *.internal, GCP metadata) are rejected. "
                "REQUIRED."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Optional. Default 30, clamped to [1, 120].",
        },
        "include_links": {
            "type": "boolean",
            "description": (
                "Optional. If true, ask the backend to extract links alongside "
                "markdown. Default false. May be ignored by some backends."
            ),
        },
    },
    "required": ["url"],
}


_FETCH_DESCRIPTION = (
    "Fetch a public web page and return its content as markdown. "
    "Read-only, no approval prompt. Long pages are head+tail truncated to "
    "fit a configured cap. Counts against the per-session and per-day budget "
    "caps. Will not fetch local or private hosts."
)


def build_web_tools(
    cfg: WebConfig,
    *,
    client: WebClient | None = None,
) -> list[Tool]:
    """Build web_search and web_fetch tools.

    `client` is injectable for tests; in production, omit it and the right
    backend client is constructed based on cfg.backend.
    """
    if client is None:
        if cfg.backend == "ollama":
            client = OllamaClient(api_key=cfg.ollama_api_key or "")
        else:
            client = FirecrawlClient(api_key=cfg.firecrawl_api_key or "")

    budget = BudgetStore(
        db_path=cfg.budget_db_path,
        session_id=cfg.session_id,
        session_soft_cap=cfg.session_soft_cap,
        daily_hard_cap=cfg.daily_hard_cap,
    )

    search_impl = build_web_search_impl(cfg, client, budget)
    fetch_impl = build_web_fetch_impl(cfg, client, budget)

    return [
        Tool(
            name="web_search",
            description=_SEARCH_DESCRIPTION,
            parameters=_SEARCH_PARAMS,
            impl=search_impl,
        ),
        Tool(
            name="web_fetch",
            description=_FETCH_DESCRIPTION,
            parameters=_FETCH_PARAMS,
            impl=fetch_impl,
        ),
    ]
```

- [ ] **Step 4: Run the full kc-web suite**

Run: `cd kc-web && pytest -v`
Expected: All PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add kc-web/src/kc_web/tools.py kc-web/tests/test_tool_integration.py
git commit -m "feat(kc-web): backend selector in build_web_tools + neutral tool descriptions"
```

---

## Task 10: Update `kc-web/__init__.py` exports

Surface `OllamaClient` and `WebClientError` at the package level so callers don't have to reach into `kc_web.client` (matches the current export style for `WebConfig` and `build_web_tools`).

**Files:**
- Modify: `kc-web/src/kc_web/__init__.py`

- [ ] **Step 1: Update __init__.py**

Replace contents of `kc-web/src/kc_web/__init__.py` with:

```python
"""KonaClaw web tools (web_search + web_fetch)."""

from kc_web.client import (
    FirecrawlClient,
    FirecrawlError,
    OllamaClient,
    ScrapeResult,
    SearchResult,
    WebClient,
    WebClientError,
)
from kc_web.config import WebConfig
from kc_web.tools import build_web_tools

__all__ = [
    "FirecrawlClient",
    "FirecrawlError",
    "OllamaClient",
    "ScrapeResult",
    "SearchResult",
    "WebClient",
    "WebClientError",
    "WebConfig",
    "build_web_tools",
]
```

- [ ] **Step 2: Verify imports work**

Run: `cd kc-web && python -c "from kc_web import OllamaClient, WebClientError, WebConfig; print('ok')"`
Expected: `ok` printed.

- [ ] **Step 3: Commit**

```bash
git add kc-web/src/kc_web/__init__.py
git commit -m "chore(kc-web): surface OllamaClient + WebClientError at package level"
```

---

## Task 11: Write SMOKE document

Pre-commit the SMOKE doc so Sammy can fill it in as he tests.

**Files:**
- Create: `docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md`

- [ ] **Step 1: Write the SMOKE doc**

Create `docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md`:

```markdown
# Ollama Web Backend — SMOKE Gates

**Spec:** `2026-05-15-ollama-web-backend-design.md`
**Plan:** `2026-05-15-ollama-web-backend.md`
**Owner:** Sammy

All gates require a fresh supervisor restart after editing `~/.konaclaw.env` or `~/KonaClaw/config/secrets.yaml.enc`.

## Prerequisites

1. Add `ollama_api_key` to `~/KonaClaw/config/secrets.yaml.enc` via the Dashboard Secrets tab (or `SecretsStore.save()` directly).
2. In `~/.konaclaw.env`, flip `KC_WEB_ENABLED=true`. Leave `KC_WEB_BACKEND` unset (defaults to `ollama`) for gates 1–4.
3. Restart KonaClawDashboard to source the env.

---

## Gate 1 — Clean supervisor boot (ollama, no firecrawl key)

**Action:** Restart the supervisor.
**Expected:** Supervisor process starts without `RuntimeError`. Log line confirms web tools are enabled. No prompt for `firecrawl_api_key`.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 2 — Kona answers a time-sensitive question via web_search

**Action:** In a Kona chat, ask: "What's the weather in Brooklyn right now?"
**Expected:**
- Audit log shows exactly one `web_search` invocation with `decision=tier` (auto-allowed).
- Response synthesizes content from search snippets.
- No approval prompt surfaces in the dashboard.

**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 3 — Kona fetches a specific page via web_fetch

**Action:** In Kona, ask: "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and summarize the first paragraph."
**Expected:**
- Audit log shows one `web_fetch` invocation, `decision=tier`.
- Response contains content from the article.
- `status_code=0` in the returned JSON (not surfaced as an error to the user).

**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 4 — `freshness` parameter silently ignored

**Action:** Trigger a `web_search` call from Kona that uses `freshness="week"` (e.g., "search for recent news about claude opus 4.7 from the last week"). Inspect the audit row's tool arguments.
**Expected:** Call succeeds with results; no `firecrawl_error` or backend error. The audit row may show `freshness=week` in tool args, but it has no effect on Ollama's behavior.
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

## Gate 5 — Firecrawl regression (CONDITIONAL — only if Firecrawl key available)

**Action:** Add `firecrawl_api_key` to secrets. Set `KC_WEB_BACKEND=firecrawl` in `~/.konaclaw.env`. Restart supervisor. Repeat Gate 2.
**Expected:** Same behavior as Gate 2, but via FirecrawlClient.
**Status:** [ ] PASS / [ ] FAIL / [ ] SKIPPED (no key)
**Notes:**

## Gate 6 — Missing key → clean startup failure

**Action:** Temporarily rename `ollama_api_key` in secrets to `ollama_api_key_X`. Set `KC_WEB_BACKEND=ollama`. Restart supervisor.
**Expected:** Supervisor refuses to start with a `RuntimeError` whose message names `ollama_api_key` and the secrets store path. (Restore the key after testing.)
**Status:** [ ] PASS / [ ] FAIL
**Notes:**

---

## Closeout

- Date: ___
- Final commit: ___
- All gates PASS / N PASS, M SKIPPED: ___
- Defects observed: ___
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md
git commit -m "docs(smoke): Ollama web backend — six manual gates"
```

---

## Task 12: Final test sweep + summary

Run both test suites and confirm zero regressions.

- [ ] **Step 1: Full kc-web suite**

Run: `cd kc-web && pytest -v 2>&1 | tail -20`
Expected: All tests PASS. Net should be ~75 existing + ~20 new = ~95 total.

- [ ] **Step 2: Full kc-supervisor suite**

Run: `cd kc-supervisor && pytest -v 2>&1 | tail -20`
Expected: All ~376 existing tests still PASS (no kc-supervisor tests added by this plan; only main.py wiring changed).

- [ ] **Step 3: Summary commit (optional, only if any tidy-up files remain)**

```bash
git status
# If anything's staged, commit. Otherwise skip.
```

- [ ] **Step 4: Hand off to Sammy for SMOKE**

Tell Sammy: "Implementation merged on `<branch>`. SMOKE doc at `docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md`. Pre-conditions: add `ollama_api_key` to the encrypted secrets store; flip `KC_WEB_ENABLED=true` in `~/.konaclaw.env`. Then restart the supervisor and walk gates 1–6."

---

## Notes for the executor

- **TDD discipline:** Each task writes the test first, runs it to see it fail, implements, runs to see pass. Don't skip the failure step — it's the only proof the test actually exercises the code you're about to write.
- **Frequent commits:** Each task ends with a commit. Don't bundle.
- **Branch:** The current branch when this plan was written is `phase-subagents`. Sammy may ask you to cut a fresh `phase-ollama-web-backend` branch off `main` before starting — confirm before Task 1.
- **Editable reinstall:** Task 4's `pip install -e . --no-deps` matches the established kc-web workflow (see Phase B tools rollout memory; PyPI resolver chokes on local-only `kc-web`).
- **Don't delete FirecrawlClient.** It stays as a peer. Anything that looks like dead code (the `FirecrawlError` alias, the firecrawl-py dep) is intentional.
- **Tool description copy:** Keep `max_results` doc at `[1, 25]` even though Ollama clamps to 10. The model sees a stable contract; the client translates. This is intentional and documented in the spec.
