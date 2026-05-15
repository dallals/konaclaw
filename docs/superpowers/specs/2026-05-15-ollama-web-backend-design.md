# Ollama Web Backend for `kc-web` — Design Spec

**Date:** 2026-05-15
**Status:** Spec — awaiting Sammy review before plan
**Supersedes:** none (additive to `2026-05-10-web-tools-design.md`)
**Related:** Phase B (web tools rollout, shipped 2026-05-10), Tools Rollout memory

---

## Problem

`kc-web` ships a single backend, Firecrawl. After the new-computer migration the Firecrawl API key did not come across in `~/KonaClaw/config/secrets.yaml.enc`, and `~/.konaclaw.env` flipped `KC_WEB_ENABLED=false` with a note to re-add the key. Web search is currently dark.

Ollama released a hosted web search API at `https://ollama.com/api/web_search` (and `/api/web_fetch`) with a free tier comfortably above our 500/day Firecrawl cap. Sammy supplied an Ollama API key for this purpose. We want a second backend so we can run on Ollama by default and keep Firecrawl as an alternative without losing the work that landed in Phase B.

## Goals

1. Add an `OllamaClient` that implements the existing `WebClient` Protocol.
2. Make `WebConfig` carry both backend keys and a `backend` selector; default to `ollama`.
3. Keep the `web_search` / `web_fetch` tool surface (names, parameter schemas, JSON return shape) **unchanged** from the model's perspective.
4. Keep `search.py` / `fetch.py` and the budget/url-guard logic backend-agnostic — only mechanical renames.
5. Bump default budget caps to `100/session` and `1000/day`, honoring shared-caps decision.
6. Fail fast at supervisor startup if the selected backend's key is missing.

## Non-goals

- **No automatic fallback** between backends. Picking one means using one. (Explicit user decision.)
- **No per-backend budget tracking**. One shared store, one set of caps. (Explicit user decision.)
- No new MCP server, no new transport, no changes to `kc_sandbox.PermissionEngine`.
- No changes to the `web_search` / `web_fetch` tool parameter schemas exposed to the model.
- No deletion of `FirecrawlClient`. It stays as a peer.

## Backend selection

`WebConfig.backend: Literal["ollama", "firecrawl"]`, source order:

1. `KC_WEB_BACKEND` env var, if set and valid.
2. Default `"ollama"`.

Validation runs at `WebConfig.from_env()`:

- If `backend == "ollama"` and `ollama_api_key` is empty/whitespace → `ValueError`.
- If `backend == "firecrawl"` and `firecrawl_api_key` is empty/whitespace → `ValueError`.
- Invalid `KC_WEB_BACKEND` value (anything outside the literal set) → `ValueError`.

`kc-supervisor/main.py` catches the `ValueError` and surfaces a startup message naming the missing secret (matching today's fail-fast pattern for the Firecrawl key).

## Architecture

```
kc_web/
  client.py
    WebClient (Protocol, unchanged)
    SearchResult, ScrapeResult (unchanged)
    WebClientError (NEW — generic; supersedes FirecrawlError)
    FirecrawlError = WebClientError  (alias, kept for one cycle)
    FirecrawlClient (rename internal raise sites; behavior unchanged)
    OllamaClient (NEW)
  config.py
    WebConfig (REFACTORED — see below)
  tools.py
    build_web_tools (REFACTORED — pick client by cfg.backend)
  search.py, fetch.py
    (only edit: import WebClientError instead of FirecrawlError;
     return string "backend_error" instead of "firecrawl_error")
```

No new files outside `kc-web`. `kc-supervisor/main.py` gains a couple of lines to read both keys + the env var and pass them through.

## `OllamaClient` contract

```python
class OllamaClient:
    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = "https://ollama.com/api",
    ) -> None: ...

    async def search(
        self, query: str, max_results: int, freshness: str
    ) -> list[SearchResult]: ...

    async def scrape(
        self, url: str, timeout_seconds: int, include_links: bool
    ) -> ScrapeResult: ...
```

### Search behavior

- POST `{base_url}/web_search` with `Authorization: Bearer <api_key>` and JSON body `{"query": query, "max_results": clamp(max_results, 1, 10)}`.
- **Silently clamps** `max_results` to `[1, 10]` (Ollama hard cap). Tool description still advertises `[1, 25]` for stable model contract.
- **Silently ignores** `freshness` (Ollama has no equivalent). Emits a debug log line on non-`"any"` values for observability, but does not error.
- Maps response `results[]` → `[SearchResult(title=r.title, url=r.url, snippet=r.content)]`.
- Empty results return `[]`, not an error.

### Scrape behavior

- POST `{base_url}/web_fetch` with same auth header, JSON body `{"url": url}`.
- `timeout_seconds` enforced via the httpx client `timeout` config (Ollama has no request-side timeout knob).
- **Silently ignores** `include_links` — Ollama always returns links and `ScrapeResult` has no `links` field. (Mirrors the existing FirecrawlClient behavior noted in the Phase B deferred-followups.)
- Maps response → `ScrapeResult(url=url, final_url=url, status_code=0, title=r.title, markdown=r.content)`. The `status_code=0` is honest — Ollama does not report it — and matches the existing contract documented in `client.py` ("best-effort; 0 if SDK doesn't report").

### Error mapping

| HTTP / network condition | Raised exception |
|---|---|
| 2xx | normal return |
| Non-2xx | `WebClientError(status=resp.status_code, message=resp.text[:512])` |
| `httpx.TimeoutException` | bubbles as `asyncio.TimeoutError` (caller is wrapped in `asyncio.wait_for` already) |
| Other `httpx.HTTPError` / `httpx.RequestError` | `WebClientError(status=0, message=str(e))` |
| JSON decode failure on a 2xx body | `WebClientError(status=0, message="invalid_json: ...")` |

The `httpx.AsyncClient` is owned by `OllamaClient` if not injected; closed in an `aclose()` method. Tests inject a stub via `MockTransport` or `respx`.

## `WebConfig` refactor

```python
@dataclass(frozen=True)
class WebConfig:
    backend: Literal["ollama", "firecrawl"]
    ollama_api_key: str | None
    firecrawl_api_key: str | None
    session_soft_cap: int           # default raised to 100
    daily_hard_cap: int             # default raised to 1000
    fetch_cap_bytes: int            # unchanged: 32 KB
    default_search_max_results: int # unchanged: 10
    default_fetch_timeout_s: int    # unchanged: 30
    budget_db_path: Path            # unchanged
    extra_blocked_hosts: tuple[str, ...]  # unchanged
    session_id: str = field(default_factory=_gen_session_id)
```

### `from_env` signature

```python
@classmethod
def from_env(
    cls,
    *,
    ollama_api_key: str | None = None,
    firecrawl_api_key: str | None = None,
    backend: str | None = None,
) -> "WebConfig":
    ...
```

- `backend` argument wins; if `None`, read `KC_WEB_BACKEND` env var; if absent, default `"ollama"`.
- All other env overrides (`KC_WEB_SESSION_SOFT_CAP`, `KC_WEB_DAILY_HARD_CAP`, `KC_WEB_FETCH_CAP_BYTES`, `KC_WEB_SEARCH_DEFAULT_N`, `KC_WEB_FETCH_DEFAULT_TIMEOUT`, `KC_WEB_BUDGET_DB`, `KC_WEB_BLOCKED_HOSTS`) keep their current semantics. Defaults for the two caps are raised in `with_defaults()`.
- Validation: selected backend's key must be non-empty (whitespace-only is treated as empty).

### Backwards compatibility note

`firecrawl_api_key` is no longer a required positional/keyword argument with no default. Callers that today do `WebConfig.from_env(api_key=...)` will break. In-repo there is exactly one caller (`kc-supervisor/main.py`) which we update in the same plan. No external callers exist.

## `build_web_tools` refactor

```python
def build_web_tools(
    cfg: WebConfig,
    *,
    client: WebClient | None = None,
) -> list[Tool]:
    if client is None:
        if cfg.backend == "ollama":
            client = OllamaClient(api_key=cfg.ollama_api_key or "")
        else:
            client = FirecrawlClient(api_key=cfg.firecrawl_api_key or "")
    # ... rest unchanged
```

The `or ""` is defensive — `from_env` already validates the key is non-empty for the selected backend, so this branch only fires if a caller bypassed validation.

## Tool descriptions

Strip backend names. New copy:

- `web_search`: *"Search the web. Returns a list of {title, url, snippet} results. Read-only, no approval prompt. Counts against the per-session and per-day budget caps. Use `site:` operator in the query to scope to a domain."*
- `web_fetch`: *"Fetch a public web page and return its content as markdown. Read-only, no approval prompt. Long pages are head+tail truncated to fit a configured cap. Counts against the per-session and per-day budget caps. Will not fetch local or private hosts."*

The `max_results` description stays `"Optional. Default 10, clamped to [1, 25]."` even though Ollama internally caps at 10. Rationale: stable contract for the model; backend translates.

## JSON-contract change at the tool layer

`search.py` and `fetch.py` today return `{"error": "firecrawl_error", "status": ..., "message": ...}` on backend failure. This becomes `{"error": "backend_error", "status": ..., "message": ...}`. The model surfaces error strings as text, never branches on them — safe — but worth recording so the next reader isn't surprised.

All other tool-layer JSON keys (`timeout`, `url_blocked`, `budget_exceeded`, `result_count`, `content_truncated`, etc.) are unchanged.

## Supervisor wiring (`kc-supervisor/main.py`)

Pseudocode for the changed block:

```python
ollama_key = secrets.get("ollama_api_key", "")
firecrawl_key = secrets.get("firecrawl_api_key", "")
backend = os.environ.get("KC_WEB_BACKEND", "ollama")
if os.environ.get("KC_WEB_ENABLED", "").lower() == "true":
    try:
        web_config = WebConfig.from_env(
            ollama_api_key=ollama_key,
            firecrawl_api_key=firecrawl_key,
            backend=backend,
        )
    except ValueError as e:
        raise SystemExit(f"KC_WEB_ENABLED=true but {e}") from e
```

Error message guidance: when the missing key triggers it, name the specific secret (`ollama_api_key` or `firecrawl_api_key`) and the file (`~/KonaClaw/config/secrets.yaml.enc`) so Sammy doesn't have to dig.

## Testing

Mirror existing kc-web test layout.

| Test file | Net change |
|---|---|
| `tests/test_ollama_client.py` | **NEW** — ~12 cases against `httpx.MockTransport` (or `respx`): happy search, happy scrape, max_results clamp to 10, freshness silently ignored, include_links silently ignored, 401 → WebClientError(401), 429 → WebClientError(429), 5xx → WebClientError(5xx), network failure → WebClientError(0), timeout bubbles, invalid JSON → WebClientError(0), Bearer header set correctly. |
| `tests/test_config.py` | **EXTEND** — ~6 cases: backend defaults to ollama, `KC_WEB_BACKEND=firecrawl` honored, invalid `KC_WEB_BACKEND` rejected, ollama-backend-no-ollama-key rejected, firecrawl-backend-no-firecrawl-key rejected, both keys present is fine, raised default caps. |
| `tests/test_tools.py` | **EXTEND** — ~2 cases: build_web_tools picks OllamaClient when backend=ollama, FirecrawlClient when backend=firecrawl. |
| `tests/test_search.py` | **EDIT** — rename `FirecrawlError` import to `WebClientError`; update one error-string assertion from `"firecrawl_error"` to `"backend_error"`. ~3 lines edited. |
| `tests/test_fetch.py` | **EDIT** — same pattern as test_search. |
| `tests/test_firecrawl_client.py` (if exists) | **EDIT** — rename error references. |
| `kc-supervisor/tests/test_main_web_wiring.py` (or equivalent) | **EXTEND** — ~3 cases: ollama-only secrets boot clean; firecrawl-only secrets + `KC_WEB_BACKEND=firecrawl` boot clean; backend selected with missing key → SystemExit. |

Net delta: ~20 new tests, ~6 files edited mechanically. All 75 existing kc-web tests + 376 kc-supervisor tests must stay green.

## SMOKE plan (preview — full doc lands alongside execution)

`docs/superpowers/specs/2026-05-15-ollama-web-backend-SMOKE.md` will gate the rollout. Six gates:

1. Fresh supervisor boot with `KC_WEB_BACKEND` unset, `ollama_api_key` present, `firecrawl_api_key` absent → clean start, no errors.
2. Kona answers "weather in Brooklyn right now" → `web_search` invoked once, audit row shows `decision=tier`, no approval prompt, structured answer returned.
3. Kona fetches a known page (Wikipedia article or example.com) → `web_fetch` invoked, `content` populated, `status_code=0` returned but not surfaced as an error.
4. Direct invocation of `web_search` with `freshness="week"` → returns results without error (parameter silently ignored).
5. **Conditional — only if a Firecrawl key is available.** Set `KC_WEB_BACKEND=firecrawl` + Firecrawl key present → search via Firecrawl still works (regression check). Skip if no key; gate documents what the verification would look like.
6. `KC_WEB_BACKEND=ollama` with `ollama_api_key` absent → supervisor refuses to start with a clear message naming the missing key.

## Deferred / out of scope

- Auto-fallback between backends (rejected as YAGNI for now).
- Per-backend budget caps (rejected as YAGNI; current shared store is enough).
- Surfacing `links[]` from `web_fetch` results — `ScrapeResult` has no `links` field today and adding one is a separate cross-cutting change.
- Tool-description awareness of the active backend (rejected — neutral copy keeps the contract stable).
- Migrating any existing skill (`research/arxiv-lookup`) to use the new neutral tool descriptions — already neutral from the model's view.

## Risks

1. **Ollama free-tier rate limit unknown precisely.** Docs imply generous limits but the exact request-per-minute ceiling isn't published. If we hit it under normal use, the 429 → `WebClientError(429)` path is correct but noisy. Mitigation: keep `KC_WEB_DAILY_HARD_CAP=1000` (one ceiling below any plausible Ollama limit); revisit if SMOKE shows otherwise.
2. **`status_code=0` from Ollama fetch may confuse downstream consumers.** None today read it, but if a future skill expects a non-zero status it will misinterpret. Mitigation: documented in `client.py` and called out here.
3. **Tool-layer error key change** (`firecrawl_error` → `backend_error`). Logs and audit rows that grep on the old string will silently miss the new one. Mitigation: ripgrep for `firecrawl_error` in the repo during plan execution; update any references.
4. **`OllamaClient` owning its httpx client.** Long-lived `AsyncClient` instances need explicit shutdown. Mitigation: `aclose()` method on the client; supervisor doesn't currently teardown kc-web cleanly, so this is a soft requirement (matches FirecrawlClient which also doesn't shut down).

## Open questions

None — all foundational decisions resolved during brainstorm:

- Both backends supported, no fallback. ✓
- Shared budget caps, defaults raised to 100/1000. ✓
- Approach A: polymorphic WebClient, neutral tool descriptions, backend-specific quirks local to each client. ✓
