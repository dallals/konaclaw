# Subagents — Manual SMOKE Checklist

**Date:** 2026-05-11
**Phase:** Subagents (ephemeral templates)
**Spec:** docs/superpowers/specs/2026-05-11-subagents-design.md
**Plan:** docs/superpowers/plans/2026-05-11-subagents.md
**Implementation branch:** `phase-subagents`

## Preconditions

- [ ] Branch `phase-subagents` merged to local supervisor.
- [ ] `export KC_SUBAGENTS_ENABLED=true` added to `~/.konaclaw.env`.
- [ ] Supervisor restarted; logs show no errors at startup. Look for `Reaped 0 in-flight subagent_runs row(s) on startup` (or similar) — confirms reap path ran.
- [ ] Dashboard reachable at http://localhost:5173.
- [ ] Tab `09 — Subagents` is visible in the top nav.
- [ ] `~/KonaClaw/subagent-templates/` exists and contains four YAMLs: `web-researcher.yaml`, `coder.yaml`, `email-drafter.yaml`, `scheduler.yaml`.

## Gates

### 1. Authoring round-trip via dashboard

**Action:**
1. Open the Subagents tab.
2. Click `+ New Template`.
3. In the modal, replace the default YAML with:
   ```yaml
   name: gate1-test
   description: Smoke gate template — just says hi.
   model: claude-opus-4-7
   system_prompt: |
     You are a smoke-test subagent. Respond with a single short greeting and stop.
   tools:
     skill_view: {}
   timeout_seconds: 60
   max_tool_calls: 5
   ```
4. Click `Save`.

**Expected:**
- Modal closes; new `gate1-test` card appears in the grid.
- `~/KonaClaw/subagent-templates/gate1-test.yaml` exists on disk with the body you typed.
- In a fresh chat conversation, ask Kona: "What subagent templates do you have?" — `gate1-test` is in the answer.

**Actual:**

---

### 2. Inline trace block renders in chat

**Action:** In a fresh Kona conversation, ask:

> "Spawn the `web-researcher` subagent to find the current weather in Berlin and tell me what it returns."

**Expected:**
- Kona calls `spawn_subagent(template="web-researcher", task="...")`.
- A collapsible `▾ subagent: web-researcher` block renders inline below the user message (or inside Kona's reply area).
- Header initially shows `running…` and a `⏹ Stop` button.
- As the subagent fires tool calls, `subagent_tool` rows appear inside the block — at minimum a `web_search` row, often a `web_fetch` row too.
- On completion, header transitions to `✓ ok · N tools · X.Xs`; the final reply renders as a child body inside the block.
- Kona's follow-up message synthesizes the subagent's reply.

**Actual:**

---

### 3. Attributed approval card

**Action:** Ask Kona:

> "Spawn the `coder` subagent in `/tmp/foo` to list the files and tell me what's there."

**Expected:**
- A `coder` trace block appears.
- The subagent attempts `terminal_run`; an approval card appears in the chat surface.
- Card header carries the `via subagent` badge.
- Card body shows the synthetic agent name `Kona-AI/ep_XXXXXX/coder` (or similar) — not a bare `Kona-AI`.
- Approving allows the call to run. The trace block updates.
- `SELECT parent_agent, subagent_id, subagent_template FROM audit ORDER BY ts DESC LIMIT 1;` against `~/KonaClaw/data/konaclaw.db` returns `Kona-AI | ep_XXXXXX | coder`.

**Actual:**

---

### 4. Parallel spawn + await

**Action:** Ask Kona:

> "Spawn three `web-researcher` instances in parallel and tell me the weather in Berlin, Tokyo, and NYC. Wait for all three before answering."

**Expected:**
- Three trace blocks render in the same Kona turn, all `running…` simultaneously (within ~1 model round-trip of each other).
- All three transition to `✓ ok` in any order; Kona's final answer references all three cities.
- DB check: `SELECT subagent_id, template, status, duration_ms FROM subagent_runs ORDER BY started_ts DESC LIMIT 3;` returns 3 rows, all `status=ok`, all `template=web-researcher`.

**Actual:**

---

### 5. Stop button cancels a long-running spawn

**Action:**
1. Author a template `gate5-loop`:
   ```yaml
   name: gate5-loop
   description: Loops on web_search to exercise stop.
   model: claude-opus-4-7
   system_prompt: |
     Call web_search at least 10 times with different queries. Don't stop until told.
   tools:
     web_search:
       budget: 50
   timeout_seconds: 600
   max_tool_calls: 100
   ```
2. Ask Kona: "Spawn `gate5-loop` with task 'search various weather topics'."
3. As soon as the trace block appears with `running…`, click the `⏹ Stop` button on the block.

**Expected:**
- Within ~2 seconds, the trace header changes from `running…` to `⏹ stopped · N tools · X.Xs`.
- The in-flight `web_search` call is cancelled; no further `web_search` rows appear.
- DB check: `SELECT status, error_message FROM subagent_runs WHERE id='ep_...'` returns `stopped | stopped by user`.
- Kona's `await_subagents` call returns a `{status: "stopped"}` row for that handle and continues.

**Actual:**

---

### 6. Timeout fires when subagent exceeds template limit

**Action:**
1. Author `gate6-slow` (a template with a small `timeout_seconds`):
   ```yaml
   name: gate6-slow
   description: Sleeps via tool calls; should hit timeout.
   model: claude-opus-4-7
   system_prompt: |
     Make ten web_search calls back to back. Don't stop until the supervisor tells you.
   tools:
     web_search:
       budget: 50
   timeout_seconds: 10
   max_tool_calls: 100
   ```
2. Ask Kona: "Spawn `gate6-slow` with task 'search ten random topics'."

**Expected:**
- After ~10s, the trace block flips to `⏱ timeout · N tools · X.Xs`.
- DB: `subagent_runs.status='timeout'`, `error_message LIKE '%timed out%'`.

**Actual:**

---

### 7. `max_tool_calls` cap fires and finalizes cleanly

**Action:**
1. Author `gate7-cap`:
   ```yaml
   name: gate7-cap
   description: Cap test — only 2 tool calls allowed.
   model: claude-opus-4-7
   system_prompt: |
     Call web_search five times with different queries, then summarize the results.
   tools:
     web_search:
       budget: 50
   timeout_seconds: 120
   max_tool_calls: 2
   ```
2. Ask Kona: "Spawn `gate7-cap` with task 'search five topics'."

**Expected:**
- Trace block shows exactly 2 `web_search` rows.
- Subsequent tool attempts return the synthetic `"error: max_tool_calls cap reached (2)"` string.
- Final status: `✓ ok` — the subagent absorbs the cap-reached error and produces a reply that acknowledges the cap (e.g. "I hit the tool-call cap after 2 searches; here's what I found...").

**Actual:**

---

### 8. Seed templates end-to-end

Run each seed template once on a representative prompt. All four should reach `✓ ok` with sensible output.

| Seed | Prompt | Expected outcome |
|------|--------|------------------|
| `web-researcher` | "Use `web-researcher` to find what the weather in Berlin is today." | 1-2 `web_search`/`web_fetch` calls; reply has a weather summary with at least one source URL. |
| `coder` | "Use `coder` in `/tmp/koa-smoke` to create `hello.sh` that prints today's date, and run it." | Approval card for `terminal_run` (attributed); after approval, file exists and ran; reply summarizes. |
| `email-drafter` | "Use `email-drafter` to draft a reply to my most recent email from mom — keep it warm and short." | Subagent uses Zapier Gmail tools to read recent thread; final reply is the draft body. (Requires Zapier Gmail connection live.) |
| `scheduler` | "Use `scheduler` to schedule a 30-minute walk with mom tomorrow afternoon." | Subagent calls `clarify` for time-of-day; after answering, calls gcal `create_detailed_event`; final reply confirms event. |

**Actual:**

- [ ] web-researcher
- [ ] coder
- [ ] email-drafter
- [ ] scheduler

---

### 9. Restart resilience — interrupted reap

**Action:**
1. Spawn any long-running subagent (e.g. `gate5-loop` from Gate 5).
2. While the trace shows `running…`, kill the supervisor: `Ctrl-C` (or `kill <pid>`).
3. Restart the supervisor.
4. Check the logs at startup and query the DB.

**Expected:**
- Supervisor startup logs include `Reaped 1 in-flight subagent_runs row(s) on startup`.
- DB: `SELECT id, status, error_message FROM subagent_runs ORDER BY started_ts DESC LIMIT 1;` → `ep_XXXXXX | interrupted | supervisor restarted mid-run`.
- Dashboard `Subagents → Active Runs` panel is empty (no zombie state).
- The `subagent-templates` tab still lists all templates correctly.

**Actual:**

---

## Closeout

When all 9 gates pass:

1. Append a results-table to this file (one row per gate: gate#, date, commit SHA, PASS/FAIL, notes).
2. Update the memory record (`project_konaclaw_*`) — Subagents shipped.
3. Open the v0.2.2 (or next milestone) followups for any deferred items captured during SMOKE:
   - Live WS broadcast of subagent frames (currently only buffered; reconnect replay works, real-time during a connected session does not).
   - `_completed` cache miss on stopped-instance race in `SubagentRunner` (latent edge — only manifests when `await_one` is called significantly after `stop`).
   - `SubagentBroadcaster` to mirror `TodoBroadcaster` for live frame fanout to all connected dashboards.
   - Dashboard live polling of the trace buffer in lieu of broadcast (workaround until broadcaster lands).
