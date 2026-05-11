# Web Tools — Manual SMOKE Checklist

**Date:** 2026-05-10
**Phase:** Tools Rollout — Phase B
**Spec:** docs/superpowers/specs/2026-05-10-web-tools-design.md
**Plan:** docs/superpowers/plans/2026-05-10-web-tools.md

## Preconditions

- [ ] Latest `main` deployed to local supervisor.
- [ ] `KC_FIRECRAWL_API_KEY` set in supervisor env via Dashboard PATCH secrets route.
- [ ] `KC_WEB_ENABLED=true` set in supervisor env.
- [ ] Supervisor restarted; logs show `web_search` and `web_fetch` registered at startup.

## Gates

### 1. Search happy path

**Action:** From a chat with Kona, ask her to use the web_search tool with query
`claude opus 4.7 release date`.

**Expected:**
- No approval prompt.
- Returns ~10 results, each with `title`, `url`, `snippet`.
- `result_count` matches array length.
- Audit row shows `tier=SAFE`.

**Actual:**

### 2. Fetch happy path

**Action:** Ask Kona to web_fetch `https://example.com`.

**Expected:**
- No approval prompt.
- Returns canonical "Example Domain" markdown.
- `status_code=200`, `content_truncated=false`.

**Actual:**

### 3. URL guard rejection (localhost)

**Action:** Ask Kona to web_fetch `http://localhost:3000`.

**Expected:**
- Returns `{"error": "url_blocked", "url": "http://localhost:3000", "reason": "local_hostname"}`.
- No Firecrawl call (verify by checking `~/.kona/web_budget.sqlite`: only a `blocked=1` row added, no `blocked=0` row for that call).

**Actual:**

### 4. Scheme rejection

**Action:** Ask Kona to web_fetch `file:///etc/passwd`.

**Expected:**
- Returns `{"error": "url_not_http", "url": "file:///etc/passwd"}`.
- No Firecrawl call.

**Actual:**

### 5. Truncation

**Action:** Ask Kona to web_fetch `https://en.wikipedia.org/wiki/Claude_Shannon`.

**Expected:**
- `content_truncated=true`.
- Marker `[TRUNCATED N bytes]` visible in `content`.
- Head and tail of the markdown both visible around the marker.

**Actual:**

### 6. Session soft cap

**Action:** In a single supervisor session, fire 50 web_fetch calls in a loop
(any cheap public URL like `https://example.com`), then one more.

**Expected:**
- Calls 1-50 succeed.
- Call 51 returns `{"error": "session_cap_exceeded", "limit": 50}`.

**Actual:**

### 7. End-to-end chat

**Action:** Ask Kona "what's the weather in Brooklyn right now?"

**Expected:**
- She calls `web_search`.
- Optionally follows up with `web_fetch` on a result.
- Answers with a current weather number.
- No approval prompts at any point.
- Audit log shows the tool calls with `tier=SAFE`.

**Actual:**

## Result

- [ ] All 7 gates pass.
- [ ] Memory updated with smoke status.
- [ ] If any gate fails, file an issue and do not consider Phase B shipped.
