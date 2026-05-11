# Todo + Clarify Tools — Manual SMOKE Checklist

**Date:** 2026-05-11
**Phase:** Tools Rollout — Phase C
**Spec:** docs/superpowers/specs/2026-05-11-todo-clarify-design.md
**Plan:** docs/superpowers/plans/2026-05-11-todo-clarify.md

## Preconditions

- [ ] Latest `main` deployed to local supervisor.
- [ ] Supervisor restarted; logs show no errors at startup.
- [ ] Dashboard reachable at http://localhost:5173.

## Gates

### 1. Todo round-trip via chat

**Action:** In a fresh Kona conversation, type:

> "Start a todo list for my NYC trip: book hotel, find restaurants, get euros"

**Expected:**
- Kona calls `todo.add` three times.
- Right sidebar (under NewsWidget) shows all 3 items.
- Audit log has 3 rows tagged `todo.add` at `tier=SAFE`.

**Actual:**

### 2. Manual tick from sidebar

**Action:** In the sidebar, click the checkbox next to "book hotel."

**Expected:**
- Item displays with strikethrough + reduced opacity.
- Kona's next reply (if you ask "what's left?") shows it as completed via `todo.list`.

**Actual:**

### 3. Hybrid scope (persist=true)

**Action:** Type:

> "Add a persistent reminder to renew my passport — that's a long-term thing, not just this trip"

**Expected:**
- Kona calls `todo.add` with `persist=true`.
- Sidebar shows the item under a "📌 Persistent" sub-header.
- Open a different conversation with Kona → the persistent item appears there too (the trip items do not).

**Actual:**

### 4. clear_done

**Action:** Tick a couple of trip items done, then type:

> "Clear out the completed items."

**Expected:**
- Kona calls `todo.clear_done`.
- Sidebar updates: completed items disappear; passport persistent item stays.

**Actual:**

### 5. Clarify happy path

**Action:** Type:

> "Schedule dinner with mom — give me a few options"

**Expected:**
- Kona calls `clarify` with 3-4 day options.
- An amber-bordered card appears inline in the chat with one button per choice + Skip + countdown.
- Click a choice → tool result has that choice; Kona continues with it.
- Card transitions to a resolved state showing your selection.

**Actual:**

### 6. Clarify skip

**Action:** Trigger a new clarify (any natural way). Click "Skip" instead of a choice.

**Expected:**
- Tool result: `{"choice": null, "reason": "skipped"}`.
- Kona handles it gracefully (asks in free text or moves on).

**Actual:**

### 7. Clarify timeout

**Action:** Ask Kona "what's something you could clarify with me — give me 10 seconds to answer or move on." (Goal: get her to call `clarify` with a short timeout.) If she won't pick a short timeout naturally, ask explicitly: "Call clarify with timeout_seconds=10 and ask which color I prefer."

**Expected:**
- Card shows countdown decrementing.
- After 10 seconds (clamped to 10 minimum), card transitions to "⏱ Timed out — Kona moved on" and buttons disable.
- Kona's tool result is `{"choice": null, "reason": "timeout", "elapsed_ms": ~10000}`.

**Actual:**

### 8. WS reconnect mid-clarify

**Action:** Trigger a clarify with a long timeout (e.g., 120s). While the card is rendered, hard-reload the dashboard tab (Cmd+Shift+R).

**Expected:**
- After reconnect, the same clarify card reappears.
- Countdown continues from the original `started_at` (so it shows less remaining than it did before the reload).
- Clicking a choice still resolves the awaiting tool call on the supervisor side.

**Actual:**

### 9. Dashboard manipulation

**Action:** In the sidebar, click a todo's checkbox → tick it done. Then click its `×` button.

**Expected:**
- Checkbox click: item shows strikethrough; PATCH `/todos/{id}` with `{status: "done"}`.
- Delete click: item removed from sidebar; DELETE `/todos/{id}` returns 204.
- Kona's next `todo.list` reflects both changes.

**Actual:**

### 10. Audit visibility

**Action:** After running gates 1-9, query the supervisor's audit table:

```sql
SELECT tool, decision, substr(args_json, 1, 80) FROM audit
WHERE tool LIKE 'todo.%' OR tool = 'clarify'
ORDER BY id DESC LIMIT 30;
```

**Expected:**
- All `todo.*` and `clarify` calls present.
- All show `decision = "tier"` (auto-allowed, no approval prompts).

**Actual:**

## Result

- [ ] All 10 gates pass.
- [ ] Memory updated with smoke status.
- [ ] If any gate fails, file an issue and do not consider Phase C shipped.
