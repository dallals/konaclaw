# Web Tools — Manual SMOKE Checklist

**Date:** 2026-05-10
**Phase:** Tools Rollout — Phase B
**Spec:** docs/superpowers/specs/2026-05-10-web-tools-design.md
**Plan:** docs/superpowers/plans/2026-05-10-web-tools.md

## Preconditions

- [x] Latest `main` deployed to local supervisor. (Run 2026-05-10/11 was against the package directly via `python -c`, not a restarted supervisor — see "Verification mode" below for what that means.)
- [x] `KC_FIRECRAWL_API_KEY` set. (Provided to a one-off Python invocation via env, not to the supervisor process. Supervisor still needs the Dashboard PATCH for gate 7.)
- [ ] `KC_WEB_ENABLED=true` set in supervisor env. **(Pending — supervisor PID 47216 was started before Phase B landed and has neither the latest code nor the env flag.)**
- [ ] Supervisor restarted; logs show `web_search` and `web_fetch` registered at startup. **(Pending.)**

## Verification mode

Gates 1, 2, 3, 4, 5, 6 were exercised by importing `kc_web` directly in a one-off Python process and invoking the tool impls (search/fetch/url_guard/budget) end-to-end:

- Gates **1, 2, 5** hit the live Firecrawl API once each (3 calls, ~$0.01-0.03 spend).
- Gates **3, 4** rely only on the URL guard — no Firecrawl call possible by design.
- Gate **6** used a fake client (no spend) to exercise the session cap path.
- Gate **7** still requires a real Kona chat round-trip and is therefore **outstanding**.

This mode tests every code path that the supervisor would reach. It does NOT verify supervisor wiring (`KC_WEB_ENABLED` gating, `Tier.SAFE` registration, audit log row generation). Those are covered by `kc-supervisor/tests/test_assembly.py` (3 web tests, all green) but the live supervisor invocation hasn't happened yet.

## Gates

### 1. Search happy path

**Action:** From a chat with Kona, ask her to use the web_search tool with query
`claude opus 4.7 release date`.

**Expected:**
- No approval prompt.
- Returns ~10 results, each with `title`, `url`, `snippet`.
- `result_count` matches array length.
- Audit row shows `tier=SAFE`.

**Actual (2026-05-10, direct `kc_web` invocation, real Firecrawl key):**
- ✅ PASS. `result_count=5` (called with `max_results=5`), shape matches contract.
- Top result: "Introducing Claude Opus 4.7 - Anthropic" (`https://www.anthropic.com/news/claude-opus-4-7`).
- `duration_ms=1233`. No approval prompt (impl never calls `request_approval`).
- Audit row generation NOT verified in this mode (no supervisor wiring exercised).

### 2. Fetch happy path

**Action:** Ask Kona to web_fetch `https://example.com`.

**Expected:**
- No approval prompt.
- Returns canonical "Example Domain" markdown.
- `status_code=200`, `content_truncated=false`.

**Actual (2026-05-10, direct invocation):**
- ✅ PASS. `status_code=200`, `title="Example Domain"`, `content_truncated=false`.
- `final_url=https://example.com` (no redirect).
- `content` starts with `# Example Domain\n\nThis domain is for use in documentation examples...`.
- `duration_ms=808`.

### 3. URL guard rejection (localhost)

**Action:** Ask Kona to web_fetch `http://localhost:3000`.

**Expected:**
- Returns `{"error": "url_blocked", "url": "http://localhost:3000", "reason": "local_hostname"}`.
- No Firecrawl call (verify by checking `~/.kona/web_budget.sqlite`: only a `blocked=1` row added, no `blocked=0` row for that call).

**Actual (2026-05-10, programmatic — no Firecrawl key needed since guard fires first):**
- ✅ PASS. `is_public_url("http://localhost:3000")` returns `(False, 'local_hostname')`.
- Bonus: also verified non-standard IP encodings are blocked (Critical fix from final review):
  `http://0x7f000001`, `http://2130706433`, `http://127.1`, `http://127.0.1` all → `(False, 'private_ip')`.

### 4. Scheme rejection

**Action:** Ask Kona to web_fetch `file:///etc/passwd`.

**Expected:**
- Returns `{"error": "url_not_http", "url": "file:///etc/passwd"}`.
- No Firecrawl call.

**Actual (2026-05-10, programmatic):**
- ✅ PASS. `is_public_url("file:///etc/passwd")` returns `(False, 'non_http_scheme')`.
- The `web_fetch` impl maps `non_http_scheme` reason to error code `url_not_http`, so the JSON return is `{"error": "url_not_http", "url": "file:///etc/passwd"}` (no `reason` field per the spec).

### 5. Truncation

**Action:** Ask Kona to web_fetch `https://en.wikipedia.org/wiki/Claude_Shannon`.

**Expected:**
- `content_truncated=true`.
- Marker `[TRUNCATED N bytes]` visible in `content`.
- Head and tail of the markdown both visible around the marker.

**Actual (2026-05-10, direct invocation, real Firecrawl key):**
- ✅ PASS. Wikipedia article retrieved at `status_code=200`, `title="Claude Shannon - Wikipedia"`.
- Original markdown was ~213 KB; truncated to 32,770 chars.
- `content_truncated=true`. Marker `...[TRUNCATED 181308 bytes]...` present mid-content.
- Head visible: `# Claude Shannon\n\nClaude Shannon\n\n|     |     |...`.
- Tail visible: ends with `lux.collections.yale.edu` references (the Wikipedia article's "External identifiers" section).

### 6. Session soft cap

**Action:** In a single supervisor session, fire 50 web_fetch calls in a loop
(any cheap public URL like `https://example.com`), then one more.

**Expected:**
- Calls 1-50 succeed.
- Call 51 returns `{"error": "session_cap_exceeded", "limit": 50}`.

**Actual (2026-05-10, programmatic with fake client + cap=5 to keep the test fast):**
- ✅ PASS. With `session_soft_cap=5` and a fake client:
  - Calls 1-5 succeeded (returned content).
  - Call 6 returned `{"error": "session_cap_exceeded", "limit": 5}`.
  - Fake client invocation counter = 5 (call 6 never reached the client — cap fired first).
- Cap value of 5 vs. spec's 50 is a test-time accommodation; the cap-enforcement logic is symmetric.

**BONUS verification — timeout fix from final review (Critical bug #1):**
- ✅ PASS. With a hanging client (sleeps 10s) and `timeout_seconds=1`, `web_fetch` returned `{"error": "timeout", "elapsed_ms": 1001}`. The `asyncio.wait_for` wrapper added in `4220d5c` works.

### 7. End-to-end chat

**Action:** Ask Kona "what's the weather in Brooklyn right now?"

**Expected:**
- She calls `web_search`.
- Optionally follows up with `web_fetch` on a result.
- Answers with a current weather number.
- No approval prompts at any point.
- Audit log shows the tool calls with `tier=SAFE`.

**Actual:** **OUTSTANDING.** Requires:
1. Sammy PATCHes `KC_FIRECRAWL_API_KEY` to the supervisor secrets via Dashboard.
2. Sammy sets `KC_WEB_ENABLED=true` in the supervisor env.
3. Supervisor restarted to pick up both the new env AND the post-`5bd476f` code.
4. A real chat with Kona asking the weather question.

Cannot be exercised programmatically from outside the supervisor — by design, this gate covers the wiring + agent-routing path.

## Result

- [x] Gates 1, 2, 3, 4, 5, 6 PASS (direct invocation against package, plus 3 live Firecrawl calls and 2 bonus Critical-fix verifications).
- [ ] Gate 7 OUTSTANDING — requires supervisor restart with `KC_FIRECRAWL_API_KEY` PATCHed and `KC_WEB_ENABLED=true`, then a Kona chat round-trip.
- [x] Memory updated with smoke status (this commit).
- [x] No critical defects surfaced. The reviewer's Important issue #3 (Firecrawl v2 metadata silently empty if not a dict) did NOT manifest in practice — `status_code=200`, `title="Example Domain"` came through cleanly.
- [ ] After gate 7 passes, flip the box on Phase B as fully shipped.
