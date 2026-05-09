# News API Integration — Design Spec

**Date:** 2026-05-08
**Status:** Approved (brainstorming)
**Scope:** Add NewsAPI.org integration to KonaClaw with two query axes (topic, publication) — exposed both as agent tools and as a dashboard widget.

## Goal

Let Sammy ask Kona for news in chat ("show me AI news", "what's BBC saying?") and also browse headlines directly in the dashboard, without leaving the app.

## Non-goals (v1)

- Fuzzy publication-name resolution / `news.list_sources` helper
- Multi-language support (English-only via `language=en`)
- Pagination beyond the first 10 results per query
- Saved searches, topic alerts, push notifications
- Cross-process cache sharing (Redis, disk-backed cache, etc.)

## Architecture

A new **News tool-provider** that mirrors the existing Gmail/GCal pattern, plus a thin dashboard widget. Five touchpoints across three subrepos:

| # | Subrepo | What lands |
|---|---------|------------|
| 1 | `kc-connectors` | `news_adapter.py` — exposes `NewsClient` (HTTP + TTL cache) and `build_news_tools()` returning two `Tool` objects |
| 2 | `kc-connectors` | `secrets.yaml.example` adds `newsapi.api_key`; `secrets.py` unchanged (already a generic loader) |
| 3 | `kc-supervisor` | `assembly.py` — when `newsapi.api_key` present, register `news.search_topic` and `news.from_source` as `Tier.SAFE` |
| 4 | `kc-dashboard-server` | `GET /api/news` — server-side `NewsClient` (its own cache instance) |
| 5 | `kc-dashboard` | `NewsWidget.tsx` rendered inside `views/Chat.tsx` as a right-side collapsible panel |

**Two cache instances, not one.** Supervisor and dashboard-server are separate processes; sharing would require disk or IPC. Each gets its own 10-minute in-memory TTL cache. Worst-case waste = a handful of duplicate queries per day, well within NewsAPI's 100/day free tier.

## Tools (the agent's view)

Both tools are `Tier.SAFE` — no approval prompt, the agent calls freely.

### `news.search_topic`

```
parameters:
  query:       string  (required) — free-text, e.g. "climate policy"
  max_results: integer (optional, default 5, hard cap 10)
returns:
  formatted text — numbered list, each line:
  "{n}. {title} — {source_name} ({published_at})\n   {url}"
  or "(no results)" / "(News API quota reached)" / "(news error: {msg})"
```

Backed by `GET https://newsapi.org/v2/everything?q=...&sortBy=publishedAt&pageSize=N&language=en`.

### `news.from_source`

```
parameters:
  source:      string  (required) — NewsAPI source slug, e.g. "bbc-news", "the-verge"
  max_results: integer (optional, default 5, hard cap 10)
returns:
  same formatted text shape as search_topic
```

Backed by `GET https://newsapi.org/v2/top-headlines?sources=...&pageSize=N`. If the slug is invalid, NewsAPI returns `code: sourcesDoesntExist` — the tool surfaces:

```
(unknown source: '{source}'. Examples: bbc-news, the-verge, reuters, associated-press)
```

so the agent can self-correct on its next turn.

### Why slugs, not display names

NewsAPI's `sources` param requires slugs. We do not add a fuzzy resolver in v1 — the agent learns by using the tool, and the error message gives examples. A future `news.list_sources` helper is a one-line addition if needed.

### Output shape rationale

Plain text (not JSON) for the tools because the agent feeds the result into a chat reply. The dashboard widget hits the HTTP endpoint instead, which returns structured JSON, so the widget gets clean fields without parsing strings.

## `NewsClient` (the shared core)

Lives in `kc_connectors.news_adapter`. Used directly by `build_news_tools` (in supervisor process) and by the dashboard server route.

```python
class NewsClient:
    def __init__(
        self,
        api_key: str,
        *,
        ttl_seconds: int = 600,
        http: Optional[Callable] = None,
    ): ...

    def search_topic(self, query: str, max_results: int = 5) -> NewsResult: ...
    def from_source(self, source: str, max_results: int = 5) -> NewsResult: ...
```

- `NewsResult` is a small dataclass:
  - `articles: list[Article]`
  - `cached: bool`
  - `error: Optional[Literal["quota_reached", "unknown_source", "upstream_error"]]`
  - `message: Optional[str]`
- `Article` dataclass: `title: str`, `source: str`, `url: str`, `published_at: str`, `snippet: str`.
- `http` is injectable so tests don't hit the network. Default uses `urllib.request` (no new dependency — matches kc-connectors' lean style).
- Cache key: `(mode, normalized_query_or_source, max_results)`. Normalized = `value.lower().strip()`.
- HTTP timeout: 10 seconds. On timeout/connection error → `error="upstream_error"`.
- NewsAPI returns HTTP 200 with `status: "error"` for app-level failures. Mapping:
  - `code: "rateLimited"` or `"maximumResultsReached"` → `quota_reached`
  - `code: "sourcesDoesntExist"` → `unknown_source`
  - everything else → `upstream_error` with `message` carrying the upstream text

The tool-side formatter in `build_news_tools` wraps `NewsResult` into the text shape — formatting lives next to the schema, not inside the client.

## Dashboard HTTP endpoint

`GET /api/news` on `kc-dashboard-server`.

```
Query params:
  mode:        "topic" | "source"  (required)
  q:           string  (required if mode=topic)
  source:      string  (required if mode=source)
  max_results: integer (optional, default 5, cap 10)

Response 200:
  {
    "articles": [
      {
        "title": "...",
        "source": "BBC News",
        "url": "...",
        "published_at": "2026-05-08T12:34:00Z",
        "snippet": "..."
      },
      ...
    ],
    "cached": true
  }

Response 4xx/5xx:
  {
    "error": "quota_reached" | "unknown_source" | "upstream_error" | "missing_param" | "not_configured",
    "message": "human-readable"
  }
```

If `newsapi.api_key` is missing from `secrets.yaml`, the route returns `503 { "error": "not_configured", "message": "News not configured. Add newsapi.api_key to secrets.yaml." }`.

The server reuses the same `NewsClient` class as the supervisor (imported from `kc_connectors.news_adapter`) — so cache, retry, and error mapping live in one place. Different process, different cache instance, same code path.

## Dashboard widget

`NewsWidget.tsx` rendered inside `views/Chat.tsx` as a right-side collapsible panel.

```
┌── News ─────────────── [⌃] ─┐
│ ( Topic ) ( Source )         │   ← segmented toggle
│ ┌────────────────────┐ [Go]  │
│ │ climate policy     │       │   ← input; Enter or click Go
│ └────────────────────┘       │
│                              │
│ 1. EU agrees on…             │   ← title (link, opens in new tab)
│    BBC News · 2h ago         │   ← source · relative time
│                              │
│ 2. White House…              │
│    Reuters · 4h ago          │
│ …                            │
└──────────────────────────────┘
```

- **State:** local React state (current mode, current query, results, loading, error). No entry in the global store — this is leaf-level UI.
- **Persistence:** last query + last mode + collapsed-or-not are persisted to `localStorage` so the widget rehydrates on reload. Nothing stored server-side.
- **Open by default.** It is the whole reason the widget exists. The user can collapse with `⌃` and the collapsed state persists.
- **Errors:**
  - `quota_reached` → "Daily news quota reached. Try again tomorrow."
  - `unknown_source` → "Unknown source. Try: bbc-news, the-verge, reuters, associated-press."
  - `upstream_error` → "Couldn't reach news service."
  - `not_configured` → "News not configured. Add `newsapi.api_key` to secrets.yaml."
- **Empty state:** "No articles. Try a broader topic or different source."
- **Loading:** spinner inline next to the Go button; results area dims.

## Configuration

- `~/KonaClaw/config/secrets.yaml` gains:
  ```yaml
  newsapi:
    api_key: ""   # https://newsapi.org/register — free tier: 100 req/day
  ```
- `secrets.yaml.example` updated to match.
- No new env vars. No new dependencies.
- If `api_key` is empty/missing:
  - Supervisor: tools are not registered (silent skip, matches Gmail/GCal pattern).
  - Dashboard: widget renders the `not_configured` message.

## Error-handling matrix

| Failure | NewsClient maps to | Tool returns | Server returns | Widget shows |
|---|---|---|---|---|
| HTTP 429 / `rateLimited` | `quota_reached` | "(News API quota reached)" | 429 `quota_reached` | "Daily news quota reached. Try again tomorrow." |
| `sourcesDoesntExist` | `unknown_source` | "(unknown source: '{x}'. Examples: …)" | 400 `unknown_source` | "Unknown source. Try: bbc-news, the-verge, reuters, associated-press." |
| Network / timeout / 5xx | `upstream_error` | "(news error: {msg})" | 502 `upstream_error` | "Couldn't reach news service." |
| Missing `api_key` | n/a (skip) | tool not registered | 503 `not_configured` | "News not configured. Add `newsapi.api_key` to secrets.yaml." |
| Empty result set | n/a | "(no results)" | 200 with `articles: []` | "No articles. Try a broader topic or different source." |

## Testing plan

| Layer | What | Where |
|---|---|---|
| `NewsClient` unit | cache hit/miss; TTL expiry; 4 error mappings; `max_results` cap; query normalization | `kc-connectors/tests/test_news_adapter.py` |
| Tools unit | both tools format results correctly; error mapping → user-facing string; missing api_key → tools not registered | same file |
| Supervisor wiring | `assemble_agent` registers `news.*` only when `newsapi.api_key` set; tier=SAFE; absent key = silent skip | `kc-supervisor/tests/test_assembly_news.py` |
| Server route | `/api/news` happy path + 4 error responses (`quota_reached`, `unknown_source`, `upstream_error`, `not_configured`); mocked `NewsClient` | `kc-dashboard-server/tests/test_news_route.py` |
| Widget E2E | Playwright: type topic → see results; toggle to source mode; quota error banner; collapsed state persists; localStorage rehydration | `kc-dashboard/tests/news-widget.spec.ts` |

Mocking strategy mirrors existing tests: Gmail uses an injected fake service object; the news layer uses an injected `http` callable on `NewsClient`.

## Rollout

- Purely additive. No migrations.
- README updates in `kc-connectors`, `kc-dashboard`.
- `SMOKE.md` in `kc-connectors` and `kc-dashboard` gain a manual checklist for the widget and for tool invocation in chat.
- No version bump beyond the next routine one for each subrepo.

## Open questions

None at spec-approval time. The four scoping decisions (NewsAPI.org, agent-tool + dashboard widget, widget on Chat view, 10-minute caching) are settled.
