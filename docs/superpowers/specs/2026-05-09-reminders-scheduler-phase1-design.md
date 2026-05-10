# Reminders & cron scheduler — Phase 1 design

**Date:** 2026-05-09
**Scope:** Give Kona durable reminder + cron scheduling so she can fire messages back into the same conversation (Telegram, dashboard, iMessage) at a future time. Phase 1 of a three-phase rollout (Phase 2: cross-channel + agent-phrased mode; Phase 3: dashboard UI).
**Affected subrepos:** `kc-supervisor` only. (`kc-connectors` already exposes the `Connector.send` interface used at fire time; `kc-core` is untouched — these are supervisor-side tools.)

## Goal

After this lands, the following exchange works on any channel where Kona is reachable:

> **Sammy:** Remind me at 5pm today to go to dinner.
>
> **Kona:** Done — I'll ping you here at 5:00 PM PT today (id 42).
>
> _…at 17:00 the supervisor pushes a Telegram message:_
>
> **Kona:** ⏰ Don't forget — dinner!

The same flow works for cron-style recurring reminders:

> **Sammy:** Every weekday at 9am, remind me to check email.
>
> **Kona:** Scheduled (id 43, every weekday at 9:00 AM).

Reminders survive supervisor restarts, fire late if missed within 24h, and persist as `AssistantMessage` rows in the conversation history.

## Non-goals

- Cross-channel scheduling ("remind me on Telegram" from the dashboard) — Phase 2.
- Agent-phrased mode (re-run the model at fire time to compose the message) — Phase 2.
- Tools on agents other than Kona.
- Dashboard UI for browsing / editing / cancelling jobs — Phase 3.
- Approval-tier gating on the scheduling tools.
- Snooze / in-place reschedule (Phase 1 requires cancel + re-schedule).

## Architecture

```
┌─ Agent runtime (Kona) ──────────────────────────────────┐
│ 4 new tools registered via AuditingToolRegistry         │
│ at safe-auto tier (audited, not approval-gated):        │
│   schedule_reminder(when, content)                       │
│   schedule_cron(cron, content)                           │
│   list_reminders(active_only=True)                       │
│   cancel_reminder(id_or_description)                     │
│                                                          │
│ Tools call into ScheduleService injected at assembly.   │
└──────────────────────────────────────┬──────────────────┘
                                       │
                                       ▼
┌─ ScheduleService (kc_supervisor.scheduling) ────────────┐
│ Wraps APScheduler (AsyncIOScheduler) +                   │
│ SQLAlchemyJobStore over the same kc.db SQLite file.      │
│                                                          │
│ schedule_one_shot(when_str, content, conv_id, agent)     │
│   → dateparser → DateTrigger → INSERT scheduled_jobs +   │
│     APS add_job (misfire_grace_time=86400)               │
│ schedule_cron(spec, content, conv_id, agent)             │
│   → croniter validate → CronTrigger(coalesce=True) →     │
│     INSERT + APS add_job                                 │
│ list / cancel — query scheduled_jobs; cancel removes     │
│   both APS job and DB row.                               │
└──────────────────────────────────────┬──────────────────┘
                                       │ APS triggers fire callback
                                       ▼
┌─ ReminderRunner (kc_supervisor.scheduling.runner) ──────┐
│ Inputs: scheduled_jobs.id                                │
│ 1. SELECT row (status, channel, chat_id, payload, ...)   │
│ 2. ConnectorRegistry.get(channel).send(chat_id, content) │
│ 3. ConversationManager.append(conv_id,                   │
│      AssistantMessage(content=payload))                  │
│ 4. UPDATE scheduled_jobs:                                │
│      - one-shot: status='done', last_fired_at=now,       │
│        attempts++                                        │
│      - cron: status stays 'pending', last_fired_at=now,  │
│        attempts++ (APS reschedules automatically)        │
│ 5. Audit row written via existing audit_tools            │
│ Errors at step 2 → status='failed', audit row, no retry. │
│ Errors at step 3 → log warn, attempts++, do not roll     │
│   back the connector send.                               │
└──────────────────────────────────────────────────────────┘

Lifecycle: kc_supervisor.main creates ScheduleService alongside
Storage during the FastAPI startup hook, calls scheduler.start().
SQLAlchemyJobStore rehydrates pending APS jobs from the same DB
file. On shutdown, scheduler.shutdown(wait=False) is called.
```

**Key invariants:**
- The `scheduled_jobs` row is the source of truth; the APS job is derived from it. APS's internal `apscheduler_jobs` table is implementation detail, not queried by application code.
- Cancellation removes both the APS job and the DB row in one transaction-bounded operation.
- `conversation_id` foreign key is `ON DELETE CASCADE` — deleting a chat deletes pending reminders.
- Reminders only fire into the conversation they were scheduled from (Phase 1 lock; Phase 2 will allow explicit `channel` overrides).
- Each reminder produces exactly one `AssistantMessage` row per fire (and one for cron repeats).

## Wire protocol — agent tools

All four tools are registered through `AuditingToolRegistry` at the **safe-auto** tier (logged in audit but not approval-gated). Reasoning: with same-channel-only delivery, the worst-case prompt-injection outcome is the user pinging themselves with content the agent composed — annoying but not dangerous. Phase 2's cross-channel work will revisit gating for the cross-channel case specifically.

Tools are registered **only on Kona** in Phase 1, via an `agent_name` allowlist in `assembly.py`.

### `schedule_reminder(when, content) → dict`

```
when:    str  — natural-language time. Examples: "5pm today", "in 2 hours",
                "tomorrow at 9am", "next Friday at noon". Resolved server-side
                via dateparser using the user's known timezone (read from the
                same source as the system-prompt timezone hint at
                ws_routes.py:96-104).
content: str  — literal text to send when the reminder fires. Capped at 4000
                chars (Telegram's hard limit is 4096; we leave headroom for the
                "⏰ " prefix described below).

Returns: {
  "id": int,
  "fires_at": "2026-05-09T17:00:00-07:00",      # ISO 8601 with offset
  "fires_at_human": "Sat May 9 5:00 PM PT",     # for the agent to echo back
  "kind": "reminder"
}

Raises ValueError when:
  - dateparser returns None for `when`
  - resolved time is in the past (< now + 5 seconds)
  - content is empty or > 4000 chars
```

### `schedule_cron(cron, content) → dict`

```
cron:    str  — standard 5-field cron string. Validated via croniter.is_valid.
                Sub-minute schedules are not supported (5-field cron has
                minute granularity by definition).
content: str  — same constraints as schedule_reminder.

Returns: {
  "id": int,
  "next_fire": "2026-05-10T09:00:00-07:00",
  "next_fire_human": "Sun May 10 9:00 AM PT",
  "human_summary": "every weekday at 9:00 AM",  # via cron-descriptor
  "kind": "cron"
}

Raises ValueError when:
  - cron spec fails croniter.is_valid
  - content is empty or > 4000 chars
```

### `list_reminders(active_only=True) → dict`

```
active_only: bool — if True, only status='pending'; otherwise includes
                    'done', 'cancelled', 'failed', 'missed'.

Returns: {
  "reminders": [
    {
      "id": 42,
      "kind": "reminder",
      "fires_at_human": "Sat May 9 5:00 PM PT",   # for one-shot
      "next_fire_human": null,                     # null on one-shot
      "content": "Don't forget dinner",
      "status": "pending",
      "human_summary": null
    },
    {
      "id": 43,
      "kind": "cron",
      "fires_at_human": null,
      "next_fire_human": "Mon May 11 9:00 AM PT",
      "content": "Check email",
      "status": "pending",
      "human_summary": "every weekday at 9:00 AM"
    },
    ...
  ]
}
```

The list is scoped to **the current conversation** — the conversation_id from
which the tool is invoked. Phase 2 will add a "scope=all" variant.

### `cancel_reminder(id_or_description) → dict`

```
id_or_description: str — either an integer ID as a string ('42') or a free-text
                         fragment matched substring-insensitive against payload.
                         If the input is purely numeric, treated as ID; else as
                         description.

Returns when input is an ID with a single match: {
  "cancelled": [{"id": 42, "content": "..."}],
  "ambiguous": false
}

Returns when input is a description matching multiple pending: {
  "cancelled": [],
  "ambiguous": true,
  "candidates": [{"id": 42, "content": "..."}, {"id": 43, "content": "..."}]
}
The agent should ask the user to disambiguate.

Raises ValueError when:
  - input is a numeric ID with no matching pending row in the current conv
  - input is a description matching no pending row
  - input is empty
```

Cancellation only operates on `status='pending'` rows in the current conversation. Cancelling a row that's already done/cancelled is treated as no-match.

## SQLite schema (additive migration)

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                  -- 'reminder' | 'cron'
    agent TEXT NOT NULL,                 -- which agent scheduled it
    conversation_id INTEGER NOT NULL,
    channel TEXT NOT NULL,               -- 'telegram' | 'dashboard' | 'imessage'
    chat_id TEXT NOT NULL,
    when_utc REAL,                       -- unix epoch UTC; NULL for cron
    cron_spec TEXT,                      -- 5-field cron; NULL for one-shot
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|cancelled|failed|missed
    attempts INTEGER NOT NULL DEFAULT 0,
    last_fired_at REAL,
    created_at REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON scheduled_jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_conv ON scheduled_jobs(conversation_id);
```

Migration runs in `Storage.init()` after the existing migrations, following the additive pattern established by the recent `messages.usage_json` migration.

APScheduler will create its own tables (`apscheduler_jobs` etc.) automatically via `SQLAlchemyJobStore` against the same DB file. We do not query those tables from application code — they're APS implementation detail.

## UX — fired-reminder format

The connector send payload is the literal `payload` from the row, prefixed with `⏰ ` (clock emoji + space) so the user can distinguish a fired reminder from a normal Kona reply. The persisted `AssistantMessage.content` is the **same prefixed string** so the dashboard renders it identically to a live fire.

Example:
- User scheduled with `content="Don't forget dinner"`.
- At fire time, Telegram receives: `⏰ Don't forget dinner`.
- The dashboard's chat history shows the same string in an assistant bubble.

(The 4000-char cap on `content` leaves headroom for the prefix without breaching the 4096 Telegram limit.)

## Time parsing & timezone handling

- `ScheduleService` reads the user's timezone the same way `ws_routes.py:96-98` does: `datetime.now().astimezone()` returns a tz-aware datetime in the supervisor host's local timezone, and `tzlocal.get_localzone()` (Python stdlib via `zoneinfo` / `tzlocal` package) yields the IANA name for `dateparser`. Phase 1 is single-user, so supervisor host TZ = user TZ; Phase 2 (cross-channel) may need to introduce a per-conversation TZ override but Phase 1 does not.
- `dateparser.parse(when, settings={"TIMEZONE": tz, "RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future"})` resolves the input. The `PREFER_DATES_FROM` flag biases ambiguous inputs (e.g. "Friday at 9am") toward the next future occurrence rather than the most recent past one.
- The resolved `datetime` is stored as a UTC unix epoch float in `when_utc`, but the `fires_at_human` echo back to the agent uses the user's TZ for readability.
- For cron jobs, APScheduler's `CronTrigger.from_crontab(spec, timezone=tz)` uses the same TZ.

## Error handling

| Scenario | Behavior |
| --- | --- |
| `when` cannot be parsed (`dateparser` returns None). | `ValueError("could not parse 'when': ...")`. Tool result is the exception; agent surfaces a clarification request to the user. |
| `when` resolves to the past (or within 5 s of now). | `ValueError("'when' resolves to the past: ...")`. |
| `cron` spec fails validation. | `ValueError("invalid cron: ...")`. |
| `content` empty or > 4000 chars. | `ValueError("content must be 1-4000 chars")`. |
| `cancel_reminder` ID not found. | `ValueError("no reminder with id N")`. |
| `cancel_reminder` description with no match. | `ValueError("no reminder matched 'X'")`. |
| `cancel_reminder` description matching multiple. | Returns `{"ambiguous": true, "candidates": [...]}` without cancelling. |
| Supervisor down when one-shot was due, restart within 24h. | APS misfire_grace_time=86400 fires the job late on resume. |
| Supervisor down > 24h after a one-shot's due time. | APS marks the job missed. We update DB row to `status='missed'`. The agent's `list_reminders(active_only=False)` surfaces these so the agent can apologise on next user interaction. |
| Supervisor down across multiple cron firings. | APS coalesce=True collapses the missed firings into one fire on resume. |
| Conversation deleted between scheduling and firing. | FK ON DELETE CASCADE removes the `scheduled_jobs` row. APS still has its job entry — on next service start, `ScheduleService` reconciles by walking APS jobs and dropping any whose mirror DB row is missing. (Reconcile also runs as a background tick every 60s in case a delete happens while the service is up.) |
| Connector raises at fire time (Telegram 403, network error). | Catch in `ReminderRunner`. Set `status='failed'`, increment `attempts`, write audit row with the error. No retry — the user-facing failure mode is "the reminder didn't fire," which `list_reminders(active_only=False)` will surface. |
| `ConversationManager.append` raises during fire (DB lock). | Log warning, increment `attempts`, do NOT roll back the connector send. The user got the message; the missing history row is best-effort. |
| Cron spec fires very frequently (e.g. `* * * * *`). | No automatic rejection — supported but documented in the tool description as "minute-resolution; high-frequency cron will spam". Sub-minute is impossible (5-field cron). |

## Testing

### Unit tests — `kc-supervisor/tests/test_scheduling.py` (new)

- Schema migration: fresh DB has `scheduled_jobs`; pre-existing DB without it is ALTER'd cleanly.
- `dateparser` integration: parse "5pm today", "in 2 hours", "tomorrow at 9am", "next Friday at noon" with frozen-clock fixture; assert resolved values. Past-time and gibberish raise `ValueError`.
- `ScheduleService.schedule_one_shot` writes a row with the right fields, registers an APS job. Assert `fires_at` matches the parsed time and `fires_at_human` formats with the user's TZ.
- `ScheduleService.schedule_cron` writes a row + APS job. `croniter` validation rejects malformed specs. `human_summary` produces a readable English string.
- `cancel_reminder` by ID: cancels DB row + APS job. Idempotent on already-cancelled (returns no-match `ValueError`).
- `cancel_reminder` by description: case-insensitive substring match. Multi-match returns `ambiguous=True` and cancels nothing.
- `list_reminders`: scoped to current conversation, filtered by `active_only`, ordered by `when_utc` for one-shots and computed `next_fire` for crons.
- Payload size cap: 4001 chars → `ValueError`. Empty content → `ValueError`.

### Fire-handler tests — `kc-supervisor/tests/test_reminder_runner.py` (new)

- Frozen clock + fake `Connector` with `AsyncMock`. Trigger `ReminderRunner.fire(job_id)` directly. Asserts:
  - `connector.send` called once with the right `chat_id` and prefixed `⏰ ...` payload.
  - `AssistantMessage` persisted in the right `conversation_id` with the same prefixed content.
  - One-shot: status flips to `done`, `last_fired_at` set, `attempts=1`.
  - Cron: status stays `pending`, `last_fired_at` set, `attempts=1`.
  - Connector raises `RuntimeError` → status `failed`, audit row written, no exception leaks to APS.
  - `ConversationManager.append` raises `sqlite3.OperationalError` → connector send still happened, status flips correctly, log captured (use `caplog`).

### Restart / rehydration tests — `kc-supervisor/tests/test_schedule_rehydrate.py` (new)

- Schedule a reminder with frozen clock, simulate restart by tearing down `ScheduleService` and constructing a new one against the same DB. Assert APS rehydrates the pending job and `next_fire` is preserved.
- One-shot misfire grace: schedule for "5 minutes from now", tear down, fast-forward 1h, restart — fires once on resume. Tear down again, fast-forward 25h, restart — `status='missed'`, no fire.
- Cron coalesce: schedule `*/30 * * * *`, tear down, fast-forward 3h, restart — fires exactly once on resume.
- Reconcile policy is **DB-row-is-source-of-truth**: at startup and on a 60-second background tick, `ScheduleService` walks both stores. (1) APS jobs whose mirror DB row is missing → drop the APS job. (2) DB rows with `status='pending'` whose APS job is missing → re-create the APS job from the row. Test both directions: write a `scheduled_jobs` row, manually delete it from SQLite, restart service — APS job is dropped. Manually delete an APS job (using the APS API), wait one reconcile tick — APS job is re-created and fires normally.

### Tool integration tests — `kc-supervisor/tests/test_scheduling_tools.py` (new)

- Real `Agent` instance with all four tools registered. Drive `chat_stream` with a `tool_calls` block invoking `schedule_reminder("in 1 hour", "test")`. Assert tool result has `id`, `fires_at`, etc., and a `scheduled_jobs` row exists.
- Same flow for `schedule_cron`.
- `cancel_reminder` ambiguous path: schedule two reminders both containing "dinner", call `cancel_reminder("dinner")`, assert `ambiguous=True` with both candidates and zero cancellations.
- Tools registered ONLY on Kona: build an Agent for a different name, assert the four tool names are NOT present in its tool registry.

### Manual smoke (added to `kc-supervisor/SMOKE.md`)

- Schedule a reminder via Kona on Telegram for "in 2 minutes" → 2 min later Telegram pings with `⏰ ...`.
- Schedule the same via the dashboard chat → reminder lands as a bubble in the same conversation, prefixed `⏰`.
- Schedule `0 9 * * *` (daily 9am), tomorrow at 9am the reminder fires.
- Restart supervisor while a reminder is pending ~5 min in the future → reminder still fires after restart (within 24h).
- Cancel by description: schedule "dinner reminder" then say "cancel the dinner one" → agent calls `cancel_reminder("dinner")`, returns success, next `list_reminders` shows none pending.
- Disambiguate flow: schedule two reminders both mentioning "meeting", say "cancel the meeting reminder" → agent gets `ambiguous=True`, asks user which one, then cancels by ID.
- Inspect SQLite: `SELECT * FROM scheduled_jobs` after each step shows the expected status transitions.

## New dependencies

Added to `kc-supervisor/pyproject.toml`:

- `apscheduler>=3.10` — the scheduler.
- `sqlalchemy>=2.0` — required by APScheduler's `SQLAlchemyJobStore`. Used **only** for APS's job-store tables; existing supervisor code continues to use raw `sqlite3` for `messages` / `conversations` / `audit` / `scheduled_jobs`. Both engines write to the same `kc.db` file in WAL mode (no schema collision; APS uses its own `apscheduler_*` table prefix).
- `dateparser>=1.2` — natural-language time parsing.
- `croniter>=2.0` — cron validation + `next_fire` computation for the human-readable echo.
- `cron-descriptor>=1.4` — generates the English `human_summary` ("every weekday at 9:00 AM") from a cron spec.

## Risks & open items

- **APScheduler in-process** means a long-running tool call would delay reminder fires by however long the agent's I/O takes. Currently no Kona tool is CPU-bound (everything is HTTP / DB / file I/O), and APScheduler runs on the FastAPI event loop so concurrent tasks share fairly. Not a blocker; flagged so future tools that do heavy work get reviewed against this.
- **SQLAlchemy + raw `sqlite3` writing to the same DB file** is supported (both use SQLite's WAL journal). The two engines manage independent connections and never share table namespaces. Worth one integration test in `test_schedule_rehydrate.py` that confirms our additive `scheduled_jobs` migration and the APS-managed tables coexist after a fresh `Storage.init()` + `scheduler.start()`.
- **Reconcile policy** is documented above (DB row is source of truth, reconcile re-creates missing APS jobs). The alternative ("APS is source of truth, drop DB row") would be wrong because the user owns the schedule, not APS — flagging here so future maintainers don't flip it.
- **Single-user TZ assumption** — Phase 1 takes the supervisor host's local TZ as the user's TZ. This breaks if the supervisor is ever deployed to a different host than the user (e.g. server hosting). Phase 2 will revisit if cross-channel introduces a multi-user reality; for now the assumption is honest because the supervisor runs on Sammy's Mac.
