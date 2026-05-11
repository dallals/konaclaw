# Web Tools — Design Spec

**Date:** 2026-05-10
**Phase:** Tools Rollout — Phase B
**Status:** Design (pre-plan)

## Summary

Add two web tools to KonaClaw — `web_search` and `web_fetch` — backed by Firecrawl. Both tools are tier `SAFE` (no approval prompt) gated by an upstream URL guard (`web_fetch` only) and per-session / per-day budget counters that hard-stop runaway loops. Closest existing analog: `kc-terminal`'s `terminal_run` (Phase A), which this design mirrors structurally — own subpackage, lazy-imported by `kc-supervisor`, gated by an `_ENABLED` flag for staged rollout.

Phase B of the post-Skills tools rollout. Phase C (todo + clarify) follows as a separate spec → plan → execute cycle.

## Goals

1. Let Kona answer questions that need fresh facts (weather, news, store hours, "did X happen yet") inside a normal chat reply, without an approval prompt on the happy path.
2. Provide a general-purpose web primitive that skills can call (mirrors how Phase A's `terminal_run` unblocked `github-pr-workflow`).
3. Mirror the structural pattern of `kc-terminal` (own subpackage, own tests, clean boundary, lazy import in supervisor assembly).
4. Cap downside: an agent stuck in a loop cannot drain the Firecrawl account or hit private network endpoints.

## Non-Goals

- Multi-provider abstraction. Firecrawl only for v1. Provider swap is a rewrite of one file (`client.py`), not a pluggable interface.
- Domain-scoped search via a `domain_filter` param. The model can use `site:example.com` in the query string.
- Server-side summarization (Firecrawl `extract` mode). Sonnet/Opus does this better and cheaper inline against the truncated markdown.
- Continuation tokens / chunked fetch for long pages. If 32 KB head+tail loses important info often, add in v2.
- Per-call cost estimation or spend-aware budgeting. The session/daily counters are tripwires, not budgets.
- Caching of fetch/search results. YAGNI for v1.
- DNS rebinding defense. Firecrawl resolves on its side; the URL guard catches the obvious cases.

## Architecture

### Package layout

New top-level package, parallel to `kc-terminal`:

```
kc-web/
  pyproject.toml
  src/kc_web/
    __init__.py
    config.py        # WebConfig dataclass, WebConfig.from_env()
    client.py        # FirecrawlClient — thin async wrapper around firecrawl-py
    url_guard.py     # is_public_url(url) -> tuple[bool, str | None]
    budget.py        # SessionCounter + DailyCounter (SQLite-backed)
    truncate.py      # head_tail(text, cap_bytes) — shared helper
    search.py        # web_search impl
    fetch.py         # web_fetch impl
    tools.py         # build_web_tools(cfg) -> list[Tool]
  tests/
    test_url_guard.py
    test_budget.py
    test_search.py
    test_fetch.py
    test_tool_integration.py
```

### Wiring

- `kc-supervisor/pyproject.toml` adds `kc-web` as a dependency.
- `kc_supervisor/assembly.py` imports `build_web_tools` inside a `try` (lazy import — missing package does not break supervisor startup), constructs a `WebConfig` from `KC_WEB_*` env vars, registers each returned `Tool`.
- A new `KC_WEB_ENABLED` env var (default `false` for safety; flipped to `true` once smoke gates pass) controls registration.
- Static `tier_map[tool.name] = Tier.SAFE` for both tools. The URL guard and budget counters are pre-checks inside the tool `impl`, not approval-time gates, so no `tier_resolver` is needed.

### Truncate helper

`kc_web/truncate.py` re-implements the same head-and-tail truncation logic as `kc_terminal/runner._head_tail`. Implementation is short enough that copying is cleaner than introducing a cross-package shared util. If a third caller appears later, factor into a `kc-shared` package then.

## Tool Surface

Two tools, both async-impl, both routed through a single `FirecrawlClient` instance.

### `web_search`

```jsonc
// params
{
  "query":       "string",      // REQUIRED, non-empty
  "max_results": 10,             // optional; default 10, clamped to [1, 25]
  "freshness":   "any"           // optional; one of: "any" | "day" | "week" | "month" | "year"
}

// success return
{
  "query":        "...",
  "results": [
    { "title": "...", "url": "https://...", "snippet": "..." },
    // ...
  ],
  "result_count": 10,
  "duration_ms":  1234
}
```

### `web_fetch`

```jsonc
// params
{
  "url":             "string",   // REQUIRED, http(s) only, must pass url_guard
  "timeout_seconds": 30,          // optional; default 30, clamped to [1, 120]
  "include_links":   false        // optional; passed through to Firecrawl scrape
}

// success return
{
  "url":               "https://...",     // echoed back, post-validation
  "final_url":         "https://...",     // after redirects
  "status_code":       200,
  "title":             "...",
  "content":           "# Page title\n\n...",   // markdown
  "content_truncated": false,
  "duration_ms":       4567
}
```

### Validation rules

**`web_search`:**
- `query` must be a non-empty string.
- `max_results` clamped silently to `[1, 25]`.
- `freshness` validated against the enum; invalid value → `error: invalid_freshness`.

**`web_fetch`:**
- `url` must parse with `urllib.parse.urlparse` and produce a non-empty `hostname`.
- Scheme must be `http` or `https`.
- URL must pass `url_guard.is_public_url(url)`.
- `timeout_seconds` clamped silently to `[1, 120]`.

### Return shape (errors)

Errors are JSON, never raised to the agent:

```jsonc
{ "error": "url_invalid",          "url": "..." }
{ "error": "url_not_http",         "url": "..." }      // file://, ftp://, javascript:, etc.
{ "error": "url_blocked",          "url": "...", "reason": "..." }   // URL guard rejection
{ "error": "missing_query" }
{ "error": "invalid_freshness",    "value": "..." }
{ "error": "session_cap_exceeded", "limit": 50 }
{ "error": "daily_cap_exceeded",   "limit": 500 }
{ "error": "firecrawl_error",      "status": 502, "message": "..." }
{ "error": "timeout",              "elapsed_ms": 30000 }
```

## URL Guard

`url_guard.is_public_url(url, extra_blocked_hosts=()) -> tuple[bool, str | None]` — returns `(allowed, reason_if_blocked)`.

```python
def is_public_url(
    url: str,
    extra_blocked_hosts: Iterable[str] = (),
) -> tuple[bool, str | None]:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False, "unparseable"
    if parsed.scheme not in ("http", "https"):
        return False, "non_http_scheme"
    host = parsed.hostname
    if not host:
        return False, "missing_host"
    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.endswith((".local", ".internal", ".localhost")):
        return False, "local_hostname"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "private_ip"
    except ValueError:
        pass  # not an IP literal, OK
    if host_lower in ("metadata.google.internal", "metadata"):
        return False, "metadata_endpoint"
    if host_lower in set(extra_blocked_hosts):
        return False, "extra_blocked"
    return True, None
```

Called from `web_fetch` impl as `is_public_url(url, cfg.extra_blocked_hosts)`.

### Notes
- The guard runs **only** in `web_fetch`. `web_search` does not pass URLs to Firecrawl — it sends a query string. Result URLs are not re-checked client-side; if the model picks a search result and calls `web_fetch` on it, the guard runs there.
- DNS rebinding is not defended against. Firecrawl resolves the host on its side, so a name that resolves to `127.0.0.1` from Firecrawl's network does not hit the local supervisor. The guard catches the obvious cases (literal IPs, well-known internal names).
- `cfg.extra_blocked_hosts: list[str]` is exact-match host strings. No suffix matching for v1.

## Budget Guardrails

`budget.py` provides session and daily call counters backed by SQLite at `~/.kona/web_budget.sqlite` (path overridable via `KC_WEB_BUDGET_DB`).

### Schema

```sql
CREATE TABLE IF NOT EXISTS web_calls (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc      TEXT    NOT NULL,           -- ISO-8601
  day_utc     TEXT    NOT NULL,           -- "2026-05-10"
  session_id  TEXT    NOT NULL,           -- supervisor process UUID
  tool_name   TEXT    NOT NULL,           -- "web_search" | "web_fetch"
  blocked     INTEGER NOT NULL DEFAULT 0  -- 1 if rejected by cap (still recorded)
);
CREATE INDEX IF NOT EXISTS idx_web_calls_day     ON web_calls (day_utc);
CREATE INDEX IF NOT EXISTS idx_web_calls_session ON web_calls (session_id);
```

### Pre-call check

Runs in each tool's `impl` before any Firecrawl call. Daily cap is checked first because it's the harder limit.

```python
async def check_and_record(tool_name: str) -> tuple[bool, dict | None]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with budget_lock:
        if count_for_day(today) >= cfg.daily_hard_cap:
            record(tool_name, blocked=True)
            return False, {"error": "daily_cap_exceeded", "limit": cfg.daily_hard_cap}
        if count_for_session(session_id) >= cfg.session_soft_cap:
            record(tool_name, blocked=True)
            return False, {"error": "session_cap_exceeded", "limit": cfg.session_soft_cap}
        record(tool_name, blocked=False)
    return True, None
```

### Defaults

| Config | Default | Override |
|---|---|---|
| `cfg.session_soft_cap` | 50 | `KC_WEB_SESSION_SOFT_CAP` |
| `cfg.daily_hard_cap`   | 500 | `KC_WEB_DAILY_HARD_CAP` |

### Concurrency

A module-level `asyncio.Lock` (`budget_lock`) serializes the read-then-write cycle so two concurrent tool calls cannot both squeak past at `cap-1`. SQLite writes inside the lock are fast (sub-millisecond); web calls themselves take seconds, so lock contention is irrelevant in practice.

### Session ID

Generated once at supervisor startup (`uuid.uuid4().hex`) and stored on `WebConfig`. New supervisor process = new session ID = soft-cap counter resets. This is intentional — the soft cap is a "stuck loop" tripwire, not a usage budget.

### Cap-hit behavior

When either cap fires, the tool returns the matching error JSON immediately. No approval flow involvement, no dynamic tier escalation. To recover: bump the env var and restart the supervisor (30 seconds, signals "this isn't normal usage"). The blocked call is still recorded in `web_calls` so the audit shows the attempt.

### Visibility

`kc_web.budget.summary() -> dict` returns:

```python
{
    "session_id": "...",
    "session_count": 17,
    "session_cap": 50,
    "daily_count": 142,
    "daily_cap": 500,
    "day_utc": "2026-05-10",
}
```

Not wired into the dashboard for v1. Available for ad-hoc inspection or a future status pill.

## Configuration

`WebConfig` is constructed by `WebConfig.from_env()` at supervisor startup.

| Field | Env var | Default |
|---|---|---|
| `firecrawl_api_key` | `KC_FIRECRAWL_API_KEY` | (required if `KC_WEB_ENABLED=true`) |
| `session_soft_cap` | `KC_WEB_SESSION_SOFT_CAP` | `50` |
| `daily_hard_cap` | `KC_WEB_DAILY_HARD_CAP` | `500` |
| `fetch_cap_bytes` | `KC_WEB_FETCH_CAP_BYTES` | `32768` |
| `default_search_max_results` | `KC_WEB_SEARCH_DEFAULT_N` | `10` |
| `default_fetch_timeout_s` | `KC_WEB_FETCH_DEFAULT_TIMEOUT` | `30` |
| `budget_db_path` | `KC_WEB_BUDGET_DB` | `~/.kona/web_budget.sqlite` |
| `extra_blocked_hosts` | `KC_WEB_BLOCKED_HOSTS` (comma-sep) | `[]` |
| `session_id` | (generated) | `uuid.uuid4().hex` |

`KC_FIRECRAWL_API_KEY` is registered in the same secret-prefix strip list `kc-terminal` uses (it is already covered by the `KC_` prefix), so terminal subprocesses do not inherit it.

## Tool `impl` Flow

### `web_search`

1. Validate `query` (non-empty string) → return `missing_query` on failure.
2. Validate `freshness` enum → return `invalid_freshness` on failure.
3. Clamp `max_results` to `[1, 25]`.
4. `ok, err = await check_and_record("web_search")` → return `err` on failure.
5. `result = await client.search(query, max_results, freshness)`.
6. On Firecrawl HTTP error → return `firecrawl_error`.
7. On asyncio timeout → return `timeout`.
8. Return success shape with `result_count`, `duration_ms`.

### `web_fetch`

1. Validate `url` parses, has hostname → return `url_invalid` / `missing_host` on failure.
2. Check scheme is `http` / `https` → `url_not_http` on failure.
3. `allowed, reason = url_guard.is_public_url(url)` → `url_blocked` on failure.
4. Clamp `timeout_seconds` to `[1, 120]`.
5. `ok, err = await check_and_record("web_fetch")` → return `err` on failure.
6. `result = await client.scrape(url, timeout_seconds, include_links)`.
7. Apply `head_tail(content, cfg.fetch_cap_bytes)` → set `content_truncated` accordingly.
8. On Firecrawl HTTP error → return `firecrawl_error`.
9. On asyncio timeout → return `timeout` with `elapsed_ms`.
10. Return success shape.

## Testing

`kc-web/tests/` runs alongside existing pytest harnesses. No real Firecrawl calls in tests — all use a fake `FirecrawlClient`. Live API exercise happens in the smoke gates.

### `test_url_guard.py` (~15 cases)

- `https://example.com`, `http://8.8.8.8` → allowed.
- `http://localhost`, `http://127.0.0.1`, `http://10.0.0.1`, `http://192.168.1.1`, `http://[::1]`, `http://169.254.169.254` → blocked, reason matches.
- `file:///etc/passwd`, `javascript:alert(1)`, `ftp://x` → `non_http_scheme`.
- `https://foo.local`, `https://bar.internal`, `https://x.localhost`, `https://metadata.google.internal` → blocked.
- Missing host (`https://`) → `missing_host`.
- Unparseable URL → `unparseable`.
- `extra_blocked_hosts=["evil.com"]` → `https://evil.com` blocked, `https://evil.com.allowed.com` not blocked (exact match only).

### `test_budget.py` (~10 cases)

- Empty DB → first call allowed, count = 1.
- 49 calls in session → 50th allowed; 51st → `session_cap_exceeded`.
- Daily cap: 500 calls across multiple session IDs → 501st rejected even with fresh session.
- Day rollover: counts reset after `day_utc` change (inject a clock).
- Concurrent calls under the lock: 100 parallel `check_and_record` calls when only 5 slots remain → exactly 5 succeed.
- Blocked calls still recorded (`blocked=1` row written).
- `summary()` returns correct counts after a mix of allowed and blocked calls.

### `test_search.py` (~8 cases)

- Mocked client `search` returning fixture results → params passed through, return shape correct (`results`, `result_count`, `duration_ms`).
- `max_results=0` → clamped to 1; `max_results=100` → clamped to 25.
- `freshness="invalid"` → `invalid_freshness` error.
- `query=""` → `missing_query` error.
- Firecrawl 429 → `firecrawl_error` with `status=429`.
- Firecrawl 5xx → `firecrawl_error` with status echoed.
- `asyncio.TimeoutError` from client → `timeout` response.
- Budget cap pre-empts Firecrawl call (assert client not invoked).

### `test_fetch.py` (~10 cases)

- Mocked client returning short markdown → returned as-is, `content_truncated=false`.
- Long markdown (>cap) → head+tail with marker, `content_truncated=true`, marker contains dropped byte count.
- `url="http://localhost"` → `url_blocked`, no Firecrawl call.
- `url="file:///etc/passwd"` → `url_not_http`, no Firecrawl call.
- `url="not a url"` → `url_invalid`.
- Redirect handled: client returns `final_url` differing from `url` → both echoed in response.
- `timeout_seconds=0` → clamped to 1; `timeout_seconds=999` → clamped to 120.
- Firecrawl 4xx with body → `firecrawl_error` with status and message.
- `asyncio.TimeoutError` → `timeout` with `elapsed_ms`.
- Budget cap pre-empts Firecrawl call.

### `test_tool_integration.py` (~7 cases)

- `build_web_tools(cfg)` returns two tools named `web_search` and `web_fetch`.
- Both register at `tier=Tier.SAFE` in the supervisor's `tier_map`.
- Audit hook: each call writes a row in the supervisor's audit table.
- Budget enforcement engages through the tool layer (not just the unit test).
- Disabled config (`KC_WEB_ENABLED=false`) → `build_web_tools` returns `[]`.
- Missing `KC_FIRECRAWL_API_KEY` with `KC_WEB_ENABLED=true` → `WebConfig.from_env()` raises a clear error at startup (fail fast).
- Tool factories are idempotent — calling twice produces functionally equivalent tools (no shared mutable state leak).

### Assembly integration

Tests added to `kc-supervisor/tests/test_assembly.py`:

- `web_search` and `web_fetch` registered when `KC_WEB_ENABLED=true` and `KC_FIRECRAWL_API_KEY` set.
- Absent when `KC_WEB_ENABLED` unset or `false`.
- Lazy import: missing `kc_web` package does not break supervisor startup (caught import error, no tools registered, warning logged).

### No external network in tests

All unit and integration tests use a fake `FirecrawlClient`. Real API exercise lives in the smoke gates.

## Manual Smoke Gates (post-merge)

Run after merging to `main`, on Sammy's machine, with `KC_FIRECRAWL_API_KEY` set in the supervisor env via the Dashboard PATCH route:

1. **Search happy path:** `web_search query="claude opus 4.7 release date"` — returns ~10 results, no approval prompt, `result_count` matches array length.
2. **Fetch happy path:** `web_fetch url="https://example.com"` — returns the canonical "Example Domain" markdown, no prompt, `status_code=200`.
3. **URL guard rejection:** `web_fetch url="http://localhost:3000"` — `url_blocked`, reason `local_hostname`, no Firecrawl call (verify by checking budget counter did not increment beyond the blocked-row record).
4. **Scheme rejection:** `web_fetch url="file:///etc/passwd"` — `url_not_http`, no Firecrawl call.
5. **Truncation:** `web_fetch url="https://en.wikipedia.org/wiki/Claude_Shannon"` — `content_truncated=true`, head + `[TRUNCATED N bytes]` marker + tail visible in `content`.
6. **Session soft cap:** in a single supervisor session, fire 50 fetches in a loop (any cheap public URL), then one more — last one returns `session_cap_exceeded` with `limit=50`.
7. **End-to-end chat:** ask Kona "what's the weather in Brooklyn right now?" — she calls `web_search`, optionally `web_fetch`s a result, and answers. No approval prompts. Audit log shows the calls with `tier=SAFE`.

Document pass/fail in `docs/superpowers/specs/2026-05-1X-web-tools-SMOKE.md` matching the Phase A precedent.

## Rollout

1. Ship `kc-web` with `KC_WEB_ENABLED=false` as the default — the package is built and tested but tools are not exposed to agents until smoke passes.
2. Set `KC_FIRECRAWL_API_KEY` in supervisor env via the Dashboard PATCH secrets route.
3. Run all 7 smoke gates against the running supervisor.
4. After all gates pass, flip `KC_WEB_ENABLED=true` in supervisor runtime config.
5. Document the tools in `kc-web/README.md`: parameter shapes, tier policy, URL guard rules, budget caps, env vars.
6. (Out of scope for Phase B but worth noting): downstream skills can now call `web_fetch` for issue/PR URL summaries, doc lookups, etc.

## Decisions Locked

| Decision | Choice |
|---|---|
| Backend | Firecrawl, both for search and fetch |
| Tool surface | Two distinct tools: `web_search`, `web_fetch` |
| Tier policy | `SAFE` for both, gated by URL guard (`web_fetch` only) and budget counters |
| Result shape (fetch) | Raw markdown, head+tail truncated, 32 KB default cap, no server-side summarization |
| Result shape (search) | Top-N (default 10, max 25) `{title, url, snippet}` |
| URL guard | Block non-http schemes, `localhost`, `*.local` / `*.internal` / `*.localhost`, private/loopback/link-local IPs, GCP metadata names |
| Domain filter (search) | None — model uses `site:` operator |
| Cost guardrails | Per-session soft cap (50, reject) + per-day hard cap (500, reject), SQLite-backed |
| Budget overflow | Reject with error JSON; recover by bumping env var and restarting supervisor |
| Caching | None |
| Provider abstraction | None — Firecrawl directly. Provider swap is a rewrite of `client.py` |
| Package shape | New `kc-web` subpackage, lazy-imported by `kc-supervisor` |
| Enable flag | `KC_WEB_ENABLED` (default `false`, flip after smoke) |

## Out of Scope (future work)

- **Phase C:** todo + clarify tools.
- Multi-provider abstraction (Exa, Tavily, native httpx). Add only when there is a concrete reason to switch.
- `extract` mode / server-side summarization. Add only if 32 KB head+tail proves insufficient with real workloads.
- Continuation tokens / chunked fetch.
- Caching of fetch/search results.
- Spend-aware budgeting (per-call cost estimation, monthly USD cap).
- Dashboard surface for the budget counters.
- DNS rebinding defense.
- Bumping tier to MUTATING on cap-hit (one-by-one approval of overflow). Reject is simpler and signals "stop digging" more clearly.
