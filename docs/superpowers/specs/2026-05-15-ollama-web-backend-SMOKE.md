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
**Status:** [x] PASS / [ ] FAIL
**Notes:** Confirmed via dashboard launcher output: `Application startup complete.` + `Uvicorn running on http://127.0.0.1:8765`. No `RuntimeError` from the web config. A pre-existing Telegram `httpx.ConnectError` was visible but unrelated to web tools.

## Gate 2 — Kona answers a time-sensitive question via web_search

**Action:** In a Kona chat, ask: "What's the weather in Brooklyn right now?"
**Expected:**
- Audit log shows exactly one `web_search` invocation with `decision=tier` (auto-allowed).
- Response synthesizes content from search snippets.
- No approval prompt surfaces in the dashboard.

**Status:** [x] PASS / [ ] FAIL
**Notes:** Required explicit prompt ("Use web_search to find...") because a separate `mcp.perplexity.*` server is also registered and Kona prefers it for generic search prompts. Once routed, `web_search` returned real results from weathershogun.com / news12.com (e.g., 62°F, humidity 52%, wind 11 mph for 2026-05-15). No approval prompt.

## Gate 3 — Kona fetches a specific page via web_fetch

**Action:** In Kona, ask: "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and summarize the first paragraph."
**Expected:**
- Audit log shows one `web_fetch` invocation, `decision=tier`.
- Response contains content from the article.
- `status_code=0` in the returned JSON (not surfaced as an error to the user).

**Status:** [x] PASS / [ ] FAIL
**Notes:** Worked end-to-end through Kona via the Ollama backend.

## Gate 4 — `freshness` parameter silently ignored

**Status:** [ ] PASS / [ ] FAIL / [x] SKIPPED
**Notes:** Skipped per Sammy at shipping. Coverage exists via `test_search_freshness_silently_ignored` in `kc-web/tests/test_ollama_client.py` (asserts the request body does NOT contain `freshness` or `tbs` keys), so manual verification was deemed redundant.

## Gate 5 — Firecrawl regression (CONDITIONAL — only if Firecrawl key available)

**Status:** [ ] PASS / [ ] FAIL / [x] SKIPPED
**Notes:** Skipped per Sammy at shipping. `firecrawl_api_key` is in the secrets store; backend switch is `KC_WEB_BACKEND=firecrawl` + restart. The `FirecrawlClient` code path is unchanged from pre-Ollama and was passing in production previously; existing unit tests + `test_build_web_tools_picks_firecrawl_when_backend_firecrawl` cover the dispatch.

## Gate 6 — Missing key → clean startup failure

**Status:** [ ] PASS / [ ] FAIL / [x] SKIPPED
**Notes:** Skipped per Sammy. The validation logic in `WebConfig.from_env` is covered by `test_from_env_ollama_without_key_raises` and `test_from_env_whitespace_key_treated_as_missing`. The supervisor wrap-and-raise to `RuntimeError` is mechanical pass-through.

---

## Closeout

- Date: 2026-05-15
- Final commit: `0a2b6d1` (SMOKE doc), implementation range `7661cce..0a2b6d1` (11 commits)
- Gates: 3 PASS (1, 2, 3), 3 SKIPPED (4, 5, 6 — all covered by unit tests or deemed mechanical)
- Defects observed: None on the new Ollama path. Pre-existing Telegram NetworkError visible in supervisor stdout, unrelated.
