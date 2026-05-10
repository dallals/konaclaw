# Reminders Phase 3 — Dashboard view, snooze/cancel, bubble linking

**Status:** Design — approved by Sammy 2026-05-10. Ready for implementation plan.

**Predecessors:**
- Phase 1: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md` (merged)
- Phase 2: `docs/superpowers/specs/2026-05-09-reminders-phase2-design.md` (merged)
- Pre-brainstorm handoff: `docs/superpowers/specs/2026-05-09-reminders-phase3-handoff.md`

## Goal

Make scheduled reminders a first-class dashboard surface. Today the only way to see or manage a reminder is through chat tools (`list_reminders` / `cancel_reminder`). Phase 3 adds a dedicated Reminders view for browsing, snoozing, and cancelling, plus a small chat affordance that links a fired-reminder bubble back to its source row.

## Scope

In scope:
- New dashboard view at `/reminders` with tabs (All / One-shot / Recurring), status + channel filter chips, sortable list, expand-on-click audit panel.
- Snooze (the only edit operation in v1) and cancel for one-shot reminders.
- "From reminder #N" footer on AssistantMessage bubbles created by `ReminderRunner.fire`, deep-linking into the Reminders tab.
- WebSocket lifecycle events (`reminder.created / cancelled / snoozed / fired / failed`) so the Reminders and Chat tabs auto-update.
- Backing HTTP endpoints, `ScheduleService` extensions, and one additive DB migration.

Explicitly out of scope:
- Create-from-dashboard form (creation stays in chat for v1).
- Reword / retarget / cron-spec edits.
- Retry buttons for `failed` / `missed` reminders (recovery is "create a new reminder from chat").
- Pagination beyond a default 200-row server cap.
- Multi-user authorization beyond existing user-scoping.

## Architecture

Three layers, each gets a Phase 3 addition.

**Frontend (kc-dashboard)**
- `kc-dashboard/src/views/Reminders.tsx` — new view, registered as nav tab `07 — REMINDERS` in `App.tsx`, route `/reminders`.
- `kc-dashboard/src/ws/useReminderEvents.ts` — WS subscriber that invalidates the `["reminders", filters]` React Query cache on each lifecycle event.
- `kc-dashboard/src/views/Chat.tsx` — small change: render a "from reminder #N" footer when an `AssistantMessage` carries `scheduled_job_id`.

**Backend (kc-supervisor)**
- New REST routes in `http_routes.py`: `GET /reminders`, `DELETE /reminders/{id}`, `PATCH /reminders/{id}`.
- `ScheduleService` gains `list_all_reminders(filters)` and `snooze_reminder(id, when_utc)`.
- `ReminderRunner.fire` populates `messages.scheduled_job_id` when persisting the AssistantMessage.
- New publisher `kc-supervisor/src/kc_supervisor/realtime/reminders.py` exposes `publish_reminder_event(event_type, reminder_row)`. `ScheduleService` and `ReminderRunner` call it after their DB transactions commit.

**Data**
- One additive migration (`messages.scheduled_job_id`, nullable, FK to `scheduled_jobs(id)` with `ON DELETE SET NULL`, partial index where not null).
- No changes to `scheduled_jobs` — all needed columns exist from Phase 1/2.

## Data model & migration

The kc-supervisor storage layer (`storage.py`) uses SQLite with a single `Storage.init()` method that runs the base `SCHEMA` script then applies idempotent `ALTER TABLE ADD COLUMN` calls gated by `PRAGMA table_info` checks (current pattern: `pinned`, `title`, `undone_at`, `usage_json`, `mode`). Phase 3 follows the same pattern — no separate migration files.

Two co-located edits in `storage.py`:

1. Update the base `messages` `CREATE TABLE` in `SCHEMA` so fresh DBs get the column from the start.
2. Add an idempotent ALTER + index creation inside `Storage.init()`:

```sql
-- inside Storage.init(), after the existing message column ALTERs
ALTER TABLE messages ADD COLUMN scheduled_job_id INTEGER NULL
  REFERENCES scheduled_jobs(id) ON DELETE SET NULL;

-- and:
CREATE INDEX IF NOT EXISTS idx_messages_scheduled_job_id
  ON messages(scheduled_job_id)
  WHERE scheduled_job_id IS NOT NULL;
```

The ALTER must be guarded by the same `cols = {r["name"] ...}` pattern used for `usage_json`. SQLite requires the FK column have a NULL default when added via ALTER (it does), and `PRAGMA foreign_keys = ON` is already set per-connection so `ON DELETE SET NULL` is enforced.

Rationale:
- **Nullable.** Historic messages predate the column; reminders that were cancelled before firing never produce a message. NULL is the steady-state norm.
- **`ON DELETE SET NULL`.** `scheduled_jobs` rows aren't deleted today (cancelled rows stick around with `status='cancelled'`). If cleanup is added later, the bubble link gracefully degrades.
- **Partial index.** Most messages are user-typed and have no `scheduled_job_id`. Partial keeps the index small. SQLite 3.8+ supports partial indexes; the bundled SQLite easily clears that.

`ReminderRunner.fire` populates the column when persisting the `AssistantMessage`. No backfill — pre-Phase-3 fired reminders simply lack a bubble link.

## HTTP API

All endpoints user-scoped by default (Phase 1 default; user resolved from auth context).

### `GET /reminders`

Query params (all optional, repeatable for status and channel):
- `status` — `pending` | `done` | `cancelled` | `failed` | `missed`. Default: all.
- `channel` — `dashboard` | `telegram` | `imessage`. Default: all.
- `kind` — `oneshot` | `cron`. Default: both.
- `conversation_id` — scope to a single conversation. Not surfaced in the v1 Reminders UI; available for future Chat-tab integration.
- `limit` — default 200, max 500.
- `offset` — default 0.

Response: `{ "reminders": [...], "total": N }`. Each reminder row contains all `scheduled_jobs` columns plus a denormalized `agent_label` and a server-computed `next_fire_at` (equal to `when_utc` for one-shots; computed from APS trigger for crons).

Sort: `next_fire_at ASC NULLS LAST, created_at DESC`.

### `DELETE /reminders/{id}`

Routes through existing `ScheduleService.cancel_reminder` (removes APS job + sets row `status='cancelled'`). Returns 204. 404 if the row doesn't exist or belongs to a different user. 409 if `status` is already terminal.

### `PATCH /reminders/{id}`

Body: `{ "when_utc": "2026-05-10T17:30:00Z" }`. The only edit in v1 (snooze).

Server validates: row exists and is user-scoped, `status='pending'`, `kind='oneshot'`, `when_utc` is in the future. Rejections:
- 404 — not found / cross-user.
- 409 with `code: "already_fired"` — status no longer pending.
- 409 with `code: "cron_not_snoozable"` — row is a cron.
- 422 — body malformed or `when_utc` is in the past.

`snooze_reminder(id, when_utc)` mutates the row and calls `apscheduler.modify_job(trigger=DateTrigger(when_utc))` inside a single DB transaction. APS uses the SQLAlchemy jobstore against the same DB, so its modify is itself a DB write — the outer transaction wraps it, giving atomic commit/rollback. A code comment guards the transaction boundary against future refactors.

Returns 200 with the updated reminder row.

## WebSocket lifecycle events

Reuses the existing WS hub (no new socket, no new auth path).

Event types:
- `reminder.created` — emitted by `ScheduleService.schedule_reminder` / `schedule_cron` after commit.
- `reminder.cancelled` — emitted by `ScheduleService.cancel_reminder` after commit.
- `reminder.snoozed` — emitted by `ScheduleService.snooze_reminder` after commit.
- `reminder.fired` — emitted by `ReminderRunner.fire` after the AssistantMessage is persisted.
- `reminder.failed` — emitted by `ReminderRunner` on the exception path.

Uniform payload:

```json
{
  "type": "reminder.created",
  "user_id": "sammy",
  "reminder": { /* full row, same shape as GET /reminders entry */ },
  "ts": 1715000000
}
```

The full row in every event keeps the dashboard simple: each event is a "set this id to this state" upsert into the React Query cache. No diff merging.

**Server side**

`publish_reminder_event` is called *after* the DB transaction commits. Pre-commit publishing risks broadcasting a state that rolls back. `ScheduleService` and `ReminderRunner` both consume this publisher.

**Client side**

`useReminderEvents.ts` subscribes to `reminder.*` and on each event invalidates `["reminders", filters]`. Strategy is "invalidate, don't merge" — the server is the source of truth and the list is small. The Reminders view also keeps `refetchInterval: 30s` as a safety net; WS-driven invalidation is the fast path, polling is the floor.

`Chat.tsx` does NOT subscribe to `reminder.fired`. The existing AssistantMessage WS event already brings the new bubble into the Chat tab; Phase 3's Chat-side job is just to render the badge when `scheduled_job_id` is set on the message.

**Failure handling.** WS drops are tolerated by the 30s polling floor. No event-replay protocol — if a user is offline when an event fires, the next refetch picks up the state.

## UI structure

`Reminders.tsx` mirrors the visual style of `Audit.tsx` and `Agents.tsx` exactly: mono fonts for metadata, display font for headings, registration marks at corners, `border-line` and `bg-panel` palette tokens (no hardcoded hex). Theme handled by the existing `ThemeToggle`.

**Layout, top to bottom:**

1. **Tabs row** — `All / One-shot / Recurring`, mono uppercase letterspacing matching existing tab style. Counts shown in muted parens. Selecting a tab sets the `kind` filter.
2. **Filter chips row** — toggleable chips for status (`pending` / `done` / `cancelled` / `failed` / `missed`) and channel (`DASH` / `TG` / `IMSG`). Multi-select within a group, AND across groups. Filter state lives in URL search params.
3. **List** — sortable rows, default sort by `next_fire_at` ascending. Each row contains:
   - `next_fire` column — countdown for one-shots ("in 12 min"), schedule expression for crons ("9am daily").
   - Kind badge — `ONE-SHOT` or `CRON`. Shown in the All tab; redundant in the kind-specific tabs but retained for consistency.
   - Payload — truncated, full text on hover.
   - Channel pill — `DASH` / `TG` / `IMSG`.
   - Status pill — only shown for non-`pending` rows.
   - Inline action icons (right-aligned): snooze (clock+arrow), cancel (×). Both hidden for non-pending rows; snooze additionally hidden for cron rows.
4. **Expand-on-click audit panel** — clicking the row anywhere outside the action icons toggles an inline expanded panel below it. Contents:
   - Created at + creating agent + creating channel.
   - For crons: raw `cron_spec` plus a human-readable rendering ("Mon-Fri at 9:00").
   - Fire history: `last_fired_at`, `attempts`. For crons: next 3 upcoming fire times computed client-side from the cron expression.
   - For fired one-shots: deep link to the resulting chat bubble (uses the new `messages.scheduled_job_id` index in reverse).

**Snooze interaction**

Clicking the snooze icon opens a small popover anchored to the row (not a modal — the list stays visible). Contents:
- Quick chips: `+15m`, `+1h`, `+1d`, `Tomorrow 9am`.
- Custom date/time input below the chips.
- Confirm sends `PATCH /reminders/{id}`. Row updates via the `reminder.snoozed` WS echo.

**Cancel interaction**

Click × → inline confirm rendered in the row ("Cancel this reminder?" with Confirm / Keep buttons). No modal. Confirm sends `DELETE`.

**Empty / loading / error states**

- No reminders at all → centered "No reminders" subtitle, no CTA (creation is via chat).
- Filters return zero but reminders exist → "No reminders match these filters" with a "Clear filters" link.
- Loading → 3 row skeletons.
- Error → top banner with retry, list keeps last-good data.

**Bubble badge (Chat.tsx)**

When an `AssistantMessage` has `scheduled_job_id` set, render a small footer beneath the bubble: `↻ from reminder #42`. Click navigates to `/reminders?highlight=42`, which scrolls the Reminders view to that row and applies a 2-second pulse highlight. Renders nothing when `scheduled_job_id` is null. No other Chat changes.

## Error handling & edge cases

**Snooze races a fire.** `snooze_reminder` re-reads `status` inside its transaction; if no longer `pending`, returns 409 with `code: "already_fired"`. UI shows a toast and refetches.

**Snooze races a cancel** (same user, two tabs). Same 409 protection. UI toast: "This reminder was already cancelled."

**WS event for a reminder the client hasn't fetched yet.** Cache invalidation strategy means we never merge partial state. The next `GET /reminders` (triggered by invalidation) is the source of truth. If the reminder doesn't match active filters, it simply doesn't appear.

**APS `modify_job` failure after DB row update.** Both writes are in the same transaction (APS's SQLAlchemy jobstore writes to the same DB). On APS exception, the transaction rolls back and `PATCH` returns 500. DB and APS stay consistent. A code comment guards the transaction boundary.

**Cron next-fire goes stale between fetches.** Sort order is briefly wrong until the 30s poll or `reminder.fired` event refreshes. Acceptable; not worth client-side recomputation.

**Bubble badge points at a no-longer-existent reminder.** `ON DELETE SET NULL` handles row deletion. Cancelled rows still exist with `status='cancelled'`, so the badge link still works and shows historical state — that's correct.

**Snooze popover dismissed without confirming.** No optimistic update was made; closing is a no-op.

**Filters yield zero results.** Chips remain enabled even when their group has zero matching rows — disabling traps the user.

**Many reminders.** Server caps at `limit=200` (max 500). Footer shows "Showing 200 of N — increase limit" if hit. Realistic single-user volume won't hit this; cap exists to bound runaway responses.

## Testing strategy

**Backend (kc-supervisor) — unit tests**

- `ScheduleService.list_all_reminders` — each filter dimension and combinations; user-scoping enforced; sort order correct.
- `ScheduleService.snooze_reminder` — happy path; rejects non-pending status, past `when_utc`, cron rows; verifies APS `modify_job` called with the new trigger; verifies DB row + APS jobstore stay consistent on rollback.
- `ReminderRunner.fire` — persisted AssistantMessage row has `scheduled_job_id` set.
- Lifecycle event publisher — each of `created / cancelled / snoozed / fired / failed` emits with correct payload after commit (fake WS hub records calls); no event on rollback.

**Backend (kc-supervisor) — HTTP integration**

- `GET /reminders` — each filter, each combo, user-scope enforcement (request as user A, verify user B's rows aren't returned).
- `DELETE /reminders/{id}` — happy, 404 (not found / cross-user), 409 (already terminal).
- `PATCH /reminders/{id}` — happy, 404, 409 (cron / non-pending), 422 (bad body / past time).

**Migration test**

Run `Storage.init()` against (a) a fresh DB and (b) a populated pre-Phase-3 DB. Assert: `scheduled_job_id` column exists in both, defaults NULL on legacy rows, partial index exists, second `init()` call is a no-op (idempotent), and an insert into `messages` referencing a non-existent `scheduled_jobs.id` raises an FK violation.

**Frontend (kc-dashboard) — component tests**

- `Reminders.tsx` — renders rows from mocked `GET /reminders`; tab switches update the kind filter; chip clicks update URL search params; expand toggle reveals audit panel; snooze popover sends correct PATCH; cancel inline-confirm sends DELETE.
- `useReminderEvents.ts` — invalidates the right query key on each event type.
- `Chat.tsx` regression — AssistantMessage with `scheduled_job_id` renders the footer; without it, no footer.
- Empty / loading / error states each render correctly.

**End-to-end smoke gates** (manual; new file `docs/superpowers/specs/2026-05-10-reminders-phase3-SMOKE.md`)

1. Schedule a one-shot 1m out from chat → appears in Reminders tab within 30s, `pending` status.
2. Click snooze → +15m chip → row updates immediately via WS, fire time pushed.
3. Wait for fire → bubble appears in Chat with "from reminder #N" footer; click footer → Reminders tab opens with row highlighted; row status is `done`.
4. Schedule a cron from chat → appears in Recurring tab; cancel inline → row goes `cancelled`; verify cron stops firing.
5. Two browsers open: snooze in tab A → tab B updates without manual refresh (WS push verified).
6. Failed path: induce a runner exception (env knob or test hook) → row appears in `failed` filter; bubble link still works.

**Out of test scope**

Performance / load (single-user app); cross-user authorization beyond basic user-scope unit checks (no multi-user feature in v1); APS internal correctness (trust the library; we test our boundary).

## File-level change inventory

**kc-supervisor**
- `src/kc_supervisor/storage.py` — update `messages` CREATE in `SCHEMA`; add guarded `ALTER TABLE messages ADD COLUMN scheduled_job_id` and partial index inside `Storage.init()`.
- `src/kc_supervisor/schedule_service.py` — add `list_all_reminders`, `snooze_reminder`; wire `publish_reminder_event` into `schedule_reminder`, `schedule_cron`, `cancel_reminder`, `snooze_reminder`.
- `src/kc_supervisor/reminder_runner.py` — populate `messages.scheduled_job_id` when persisting AssistantMessage; emit `reminder.fired` / `reminder.failed`.
- `src/kc_supervisor/realtime/reminders.py` — new file, `publish_reminder_event` publisher.
- `src/kc_supervisor/http_routes.py` — `GET /reminders`, `DELETE /reminders/{id}`, `PATCH /reminders/{id}`.
- Tests under `kc-supervisor/tests/` matching the testing strategy above.

**kc-dashboard**
- `src/App.tsx` — add tab `07 — REMINDERS` and `/reminders` route.
- `src/views/Reminders.tsx` — new view.
- `src/views/Chat.tsx` — bubble badge for AssistantMessage with `scheduled_job_id`.
- `src/ws/useReminderEvents.ts` — new WS subscriber.
- `src/api/reminders.ts` — typed client for the three endpoints.
- Tests under `src/views/__tests__/` and `src/ws/__tests__/`.

## Pointers

- Phase 1 spec: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md`
- Phase 2 spec: `docs/superpowers/specs/2026-05-09-reminders-phase2-design.md`
- Phase 3 handoff: `docs/superpowers/specs/2026-05-09-reminders-phase3-handoff.md`
- Existing dashboard views matching this style: `kc-dashboard/src/views/Audit.tsx`, `Agents.tsx`
- Sidebar/tab wiring: `kc-dashboard/src/App.tsx`
