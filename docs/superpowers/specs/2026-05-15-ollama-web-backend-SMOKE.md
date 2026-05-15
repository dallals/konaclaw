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
