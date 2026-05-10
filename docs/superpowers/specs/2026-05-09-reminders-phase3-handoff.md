# Reminders Phase 3 — handoff (not yet brainstormed)

**Status:** **Pre-brainstorm.** This is a handoff doc capturing what we agreed during Phase 1 brainstorming about Phase 3's scope. A future session must run `superpowers:brainstorming` over this scope and produce a real design spec before any code.

**Predecessors:**
- Phase 1 spec at `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md` (merged 2026-05-09).
- Phase 2 must ship before Phase 3 — Phase 3 surfaces and edits the cross-channel reminders Phase 2 introduces.

## Scope (agreed at Phase 1 brainstorming)

Phase 3 adds a **dashboard UI** for browsing, editing, and cancelling scheduled jobs. Today (Phase 1) the only way to see or cancel a reminder is via the chat (`list_reminders` / `cancel_reminder` tools). Phase 3 makes scheduling a first-class dashboard surface.

Likely components (subject to brainstorming):

- **A new dashboard view** (`kc-dashboard/src/views/Reminders.tsx`?) listing all `scheduled_jobs` rows for the current user, filterable by status (`pending` / `done` / `cancelled` / `failed` / `missed`) and channel.
- **HTTP endpoints** in `kc-supervisor/src/kc_supervisor/http_routes.py`:
  - `GET /reminders` — list (analogous to existing `/conversations` endpoints).
  - `POST /reminders` — create (one-shot or cron).
  - `DELETE /reminders/{id}` — cancel.
  - `PATCH /reminders/{id}` — edit (snooze, reword, change channel) — see open questions.
- **Sidebar / nav entry** for Reminders alongside Chat, Agents, Audit, etc.
- **Per-bubble badge linking back to the source reminder** — e.g., when a fired reminder appears as an `AssistantMessage` in chat, show a "from reminder #42" footer linking to its row in the Reminders view. Phase 1's spec mentioned this as a Phase 3 feature.

## Constraints from Phase 1 (load-bearing)

- The `scheduled_jobs` table already exists with all the columns Phase 3 needs (`id`, `kind`, `agent`, `conversation_id`, `channel`, `chat_id`, `when_utc`, `cron_spec`, `payload`, `status`, `attempts`, `last_fired_at`, `created_at`).
- `ScheduleService.list_reminders(conversation_id, active_only)` exists but is conversation-scoped. Phase 3 needs a `list_all_reminders(active_only, status_filter, channel_filter)` API. Add it as a new method; don't change the existing one.
- Cancellation already routes through `ScheduleService.cancel_reminder` which removes both the DB row and the APS job atomically. Phase 3's DELETE endpoint reuses that path.
- Editing a reminder is more complex — APScheduler doesn't support in-place trigger edits; the implementation path is "cancel + reschedule" with the same `id` (or a new id and a redirect). Brainstorming should pick one.
- The `from reminder` link on chat bubbles requires a join from `messages` to `scheduled_jobs`. We don't currently store the `scheduled_job_id` on `messages` — Phase 3 likely needs an additive migration adding `messages.scheduled_job_id INTEGER NULL` populated by `ReminderRunner.fire`.

## Open questions for the Phase 3 brainstorming session

- What does "edit" mean? Concrete operations: snooze (push the fire time later), reword (change `payload`), retarget (change channel). Pick a minimum set.
- Does the Reminders view show ALL conversations' reminders for the user, or scope to the active conversation? (Recommendation: all, with a "this conversation only" toggle.)
- Cron reminders need a different visual treatment than one-shots (next-fire vs fixed-time). Mockup ideas?
- For failed/missed status: should the UI offer a "retry now" button that re-schedules the same payload immediately?
- Do we surface the audit trail (who scheduled it, when) per row, or hide it behind an expand affordance?

## Pre-flight before brainstorming

- Re-read the Phase 1 spec and the Phase 2 handoff to understand which fields exist by then.
- Re-read `kc-dashboard/src/views/` for the visual style of existing views (Chat.tsx, Agents.tsx, Audit.tsx). Phase 3 should match.
- Check the existing dashboard sidebar / nav structure to know where the Reminders entry goes.
- Confirm Phase 2 has shipped (Phase 3 builds on Phase 2's cross-channel + agent-phrased work).

## Pointers

- Phase 1 spec: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md`
- Phase 2 handoff: `docs/superpowers/specs/2026-05-09-reminders-phase2-handoff.md`
- Existing dashboard views: `kc-dashboard/src/views/Chat.tsx`, `Agents.tsx`, `Audit.tsx`
- Existing sidebar wiring: `kc-dashboard/src/App.tsx` (or wherever the router is configured)
