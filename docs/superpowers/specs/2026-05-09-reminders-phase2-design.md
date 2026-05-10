# Reminders Phase 2 — design spec

**Status:** Brainstormed and approved 2026-05-09. Ready for implementation planning via `superpowers:writing-plans`.

**Predecessors:**
- Phase 1 spec: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md` (merged 2026-05-09)
- Phase 2 handoff (now superseded by this doc): `docs/superpowers/specs/2026-05-09-reminders-phase2-handoff.md`

**Successor:** Phase 3 (`docs/superpowers/specs/2026-05-09-reminders-phase3-handoff.md`) builds the dashboard UI on top of this work.

## Goal

Phase 2 extends Phase 1's reminder system with two orthogonal capabilities, shipped together as a single phase:

1. **Cross-channel scheduling** — the agent can schedule a reminder that fires in a *different* channel than the one it was scheduled from (e.g., schedule on dashboard, fire on Telegram).
2. **Agent-phrased mode** — at fire time, the supervisor re-runs the agent to compose the actual message text instead of shipping a literal payload.

Both features share one new storage column (`mode`) and a small set of new tool args. They ship as one phase because they're conceptually related (both shape how a reminder behaves at fire time) and decomposing them would just add coordination overhead for a single user.

## Non-goals

- Dashboard UI for reminders (Phase 3).
- Per-bubble "from reminder" attribution badges (Phase 3 — requires `messages.scheduled_job_id` migration not added here).
- Approval-gating for cross-channel (replaced by allowlist; see Decisions).
- Multi-user identity / authorization (KonaClaw is single-user; deferred indefinitely).
- A dedicated `outbound_send` agent tool (replaced by "agent's final assistant text is the message"; see Decisions).
- Recursion-depth limits or rate limits on agent-phrased fires (scheduling tools are stripped from the fire-time agent instead; see Section 4).

## Decisions locked during brainstorming

| # | Question | Decision |
|---|---|---|
| 1 | Phase 2 scope | Ship cross-channel + agent-phrased together as one phase (Option A). |
| 2 | Cross-channel safety posture | Per-channel allowlist in config table; allowlisted channels are safe-auto, others are blocked outright. No approval gate. |
| 3 | Agent-phrased conversation model | Same-channel = append to original conversation. Cross-channel = use destination conversation (find/create via `(channel, chat_id, agent)`). |
| 4 | List/cancel scope | Add `scope` arg to both tools. Default flips to `"user"` (all rows); `"conversation"` available as escape hatch. |
| 5 | How agent-phrased message goes out | No new tool. Agent's final assistant text in the fire-time turn becomes the dispatched message. |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Schedule time                                                        │
│                                                                      │
│  agent calls                          ScheduleService                │
│  schedule_reminder(            ──▶    .schedule_one_shot(            │
│    when=…, content=…,                   when=…, content=…,           │
│    target_channel="telegram",           target_channel="telegram",   │
│    mode="agent_phrased",                mode="agent_phrased", …)     │
│  )                                                                   │
│                                                ▼                     │
│                                       resolve via channel_routing    │
│                                       (allowlist gate, chat_id)      │
│                                                ▼                     │
│                                       INSERT scheduled_jobs row      │
│                                       (channel = telegram,           │
│                                        chat_id = routed,             │
│                                        mode = agent_phrased,         │
│                                        conversation_id = scheduling) │
│                                                ▼                     │
│                                       APS add_job → fire_reminder    │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ Fire time                                                            │
│                                                                      │
│  APS trigger ─▶ fire_reminder(job_id) ─▶ ReminderRunner.fire(row)    │
│                                                ▼                     │
│                                       branch on row["mode"]          │
│                                                ▼                     │
│           literal: text = "⏰ " + payload                             │
│           agent_phrased: text = _compose_agent_phrased(row)          │
│                                       (mocked-able internal method)  │
│                                                ▼                     │
│                                       resolve dest_conv_id via       │
│                                       Conversations.get_or_create(   │
│                                         channel, chat_id, agent)     │
│                                                ▼                     │
│                                       dispatch:                      │
│                                         - dashboard: persist direct  │
│                                         - other: connector.send +    │
│                                           persist                    │
│                                                ▼                     │
│                                       update_scheduled_job_after_    │
│                                       fire(status=…)                 │
└──────────────────────────────────────────────────────────────────────┘
```

## Schema changes

Both changes are additive and applied via the existing idempotent `Storage.init()` path.

### `scheduled_jobs.mode` (new column)

```sql
ALTER TABLE scheduled_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'literal';
```

- Values: `'literal'` | `'agent_phrased'`.
- Default keeps existing rows unchanged.
- `init()` wraps the ALTER in a `PRAGMA table_info(scheduled_jobs)` check so re-runs are no-ops.
- Rollback: drop column. Reversible.

### `channel_routing` (new table)

```sql
CREATE TABLE IF NOT EXISTS channel_routing (
    channel          TEXT PRIMARY KEY,
    default_chat_id  TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1
);
```

- One row per channel that's allowed as a cross-channel target.
- `enabled = 0` means "in the table but turned off" — toggle without losing the chat_id.
- Empty table = no cross-channel allowed (safe default).
- Bootstrap: a small CLI helper (`kc-supervisor channel-routing add <channel> <chat_id>`) seeds entries. Plan picks exact CLI surface.
- The plan's first task seeds Sammy's row: `telegram → 8627206839, enabled=1`.

### Storage methods added

- `Storage.get_channel_routing(channel: str) -> dict | None` — returns `{default_chat_id, enabled}` or `None`.
- `Storage.upsert_channel_routing(channel, default_chat_id, enabled)` — used by the CLI helper.
- `Storage.list_channel_routing() -> list[dict]` — for debugging and Phase 3 inspection.

### Schema changes NOT made in Phase 2

- `messages.scheduled_job_id` — Phase 3's "from reminder" badge depends on this. Out of scope here.
- `scheduled_jobs.originating_conversation_id` — considered, rejected. `conversation_id` retains "where scheduled" semantics; runtime resolution handles "where to fire."

## Tool surface changes

All in `kc-supervisor/src/kc_supervisor/scheduling/tools.py`. Phase 1 tools gain optional args; defaults preserve Phase 1 behavior exactly.

### `schedule_reminder` and `schedule_cron`

Both gain two optional args:

| Arg | Type | Default | Values |
|---|---|---|---|
| `target_channel` | string | `"current"` | `"current"`, `"telegram"`, `"dashboard"`, `"imessage"` |
| `mode` | string | `"literal"` | `"literal"`, `"agent_phrased"` |

Tool descriptions updated:
- For `target_channel`: "Use only when the user explicitly asks to be reminded somewhere other than this conversation. Channels not in the configured allowlist will raise."
- For `mode`: "When `agent_phrased`, the `content` argument is interpreted as an *internal trigger description for you*, not the literal text the user sees. At fire time you will be re-invoked to compose the actual message."

### `list_reminders` and `cancel_reminder`

Both gain one optional arg:

| Arg | Type | Default | Values |
|---|---|---|---|
| `scope` | string | `"user"` | `"user"`, `"conversation"` |

**Default flips from Phase 1's implicit conversation scope.** `"user"` = all rows in `scheduled_jobs`. `"conversation"` = current behavior (filter by current conversation_id).

`list_reminders` output rows now also include `channel` and `mode` fields so the agent can describe reminders accurately ("you have a Telegram reminder at 5pm and an agent-phrased dashboard cron every Monday").

`cancel_reminder` ambiguity matcher operates over the widened scope — same `{ambiguous: true, candidates: [...]}` response shape, just with potentially more candidates.

## Service API changes

`ScheduleService.schedule_one_shot` and `ScheduleService.schedule_cron` add `target_channel: str = "current"` and `mode: str = "literal"`.

Resolution logic at schedule time:

```python
if target_channel == "current":
    use_channel  = ctx["channel"]
    use_chat_id  = ctx["chat_id"]
else:
    routing = storage.get_channel_routing(target_channel)
    if routing is None:
        raise ValueError(f"channel {target_channel!r} not configured (no routing entry)")
    if not routing["enabled"]:
        raise ValueError(f"channel {target_channel!r} is disabled")
    use_channel  = target_channel
    use_chat_id  = routing["default_chat_id"]

storage.add_scheduled_job(
    kind=…, agent=ctx["agent"],
    conversation_id=ctx["conversation_id"],   # always the SCHEDULING conversation
    channel=use_channel, chat_id=use_chat_id,
    payload=content, mode=mode, …
)
```

The `scheduling/context.py` contextvar stays untouched — `target_channel` is a tool argument, never a contextvar override.

`ScheduleService.list_reminders` and `cancel_reminder` add `scope: str = "user"`. When `scope="user"`, the storage call drops the `conversation_id` filter.

Validation at service entry:
- `mode` ∈ `{"literal", "agent_phrased"}` → raise `ValueError` otherwise.
- `target_channel` ∈ `{"current", "telegram", "dashboard", "imessage"}` (string-level check before routing lookup).
- `scope` ∈ `{"user", "conversation"}`.

## Runner changes

`ReminderRunner.fire` (in `kc-supervisor/src/kc_supervisor/scheduling/runner.py`) gains two responsibilities: branch on `mode` for text composition, and resolve the destination conversation for cross-channel rows.

### Text composition

```python
row = self.storage.get_scheduled_job(job_id)
if row is None:
    logger.warning("ReminderRunner.fire: job %s not found; skipping", job_id)
    return

if row["mode"] == "agent_phrased":
    text = self._compose_agent_phrased(row)
    if text is None:
        self.storage.update_scheduled_job_after_fire(
            job_id, fired_at=time.time(), new_status="failed",
        )
        return
else:
    text = PREFIX + (row["payload"] or "")
```

### Destination conversation resolution

Both modes use the same resolver:

```python
dest_conv_id = self.conversations.get_or_create(
    channel=row["channel"], chat_id=row["chat_id"], agent=row["agent"],
)
```

The `(channel, chat_id, agent)` triple has a deterministic mapping (see existing `messaging_routes`-style table at `storage.py:70-73`). `dest_conv_id == row["conversation_id"]` for same-channel rows; differs for cross-channel.

This fixes a latent Phase 1 bug: literal cross-channel would have persisted to the originating conversation. Phase 1 didn't have cross-channel so the bug was unreachable; Phase 2 fixes it by construction.

### Dispatch

Existing branches, with the resolved `dest_conv_id` and computed `text`:

```python
if row["channel"] == "dashboard":
    self.conversations.append(dest_conv_id, AssistantMessage(content=text))
else:
    connector = self.connector_registry.get(row["channel"])
    self._run_coro(connector.send(row["chat_id"], text))
    self.conversations.append(dest_conv_id, AssistantMessage(content=text))

new_status = "done" if row["kind"] == "reminder" else "pending"
self.storage.update_scheduled_job_after_fire(job_id, fired_at=time.time(), new_status=new_status)
```

### `_compose_agent_phrased(row)`

Returns the agent's final assistant text or `None` on failure.

1. Resolve `dest_conv_id` (same call as above; can be hoisted by the caller to avoid two lookups).
2. Build a synthetic trigger message — user-role, **not persisted**:
   ```
   [Internal trigger — scheduled at {scheduled_iso}, fired at {now_iso}] {payload}
   ```
3. Run an agent turn on `dest_conv_id` with:
   - **Tool list with scheduling tools stripped** — no `schedule_reminder`, `schedule_cron`, or `cancel_reminder`. `list_reminders` may stay (read-only). Prevents fire-time loops.
   - **System-prompt addendum** prepended:
     > You are responding to a scheduled reminder you set for the user. The trigger description follows. Compose a single friendly message — do not mention this is a reminder fire unless the trigger asks you to.
   - Same agent invocation entry point as a normal user turn. Plan resolves the exact API.
4. Read the final assistant message:
   - Non-empty text → return it.
   - Empty / tool-call-only termination / exception → return `None`.

The synthetic trigger is **never persisted** — only the agent's composed text is appended to `dest_conv_id`.

### Connector registry guarantee

Fix `kc-supervisor/src/kc_supervisor/main.py` so `ConnectorRegistry` is **always** constructed at boot, even if no connectors are configured. This resolves Phase 1's I1 issue and ensures cross-channel scheduling toward an as-yet-unwired connector fails predictably at fire time (rather than blowing up at boot or being silently dropped).

## Conversation/destination resolution rules — summary table

| Scenario | `row.conversation_id` | `row.channel` | `dest_conv_id` | Where text goes |
|---|---|---|---|---|
| Same-channel literal (Phase 1 unchanged) | scheduling conv | scheduling channel | == row.conversation_id | original conversation |
| Same-channel agent-phrased | scheduling conv | scheduling channel | == row.conversation_id | original conversation, agent text only |
| Cross-channel literal | scheduling conv | target channel | dest channel's conv | destination conversation |
| Cross-channel agent-phrased | scheduling conv | target channel | dest channel's conv | destination conversation, agent text only |

**Allowlist is gated at schedule time only.** A row scheduled while `telegram` was enabled continues to fire even if `channel_routing.telegram.enabled` is later flipped to 0. To kill an in-flight reminder, `cancel_reminder` is the right tool.

## Error handling

### Schedule-time errors (raised as `ValueError`, agent surfaces to user)

| Condition | Error |
|---|---|
| `target_channel` not in `channel_routing` | `"channel 'X' not configured (no routing entry)"` |
| `target_channel` in routing but `enabled=0` | `"channel 'X' is disabled"` |
| `target_channel` value not in known set | `"unknown channel 'X'"` |
| `mode` not in known set | `"unknown mode 'X'"` |
| `target_channel="current"` + `mode="agent_phrased"` on dashboard | allowed; no special case |
| Phase 1 errors (past time, bad cron, oversize payload) | unchanged |

### Fire-time outcomes (terminal `status` on the row)

| Outcome | `status` | Side effect |
|---|---|---|
| Literal, send + persist OK | `done` (reminder) / `pending` (cron) | message delivered |
| Literal, connector send fails | `failed` | logged; no message |
| Literal, send OK but persist fails | `done` / `pending` | logged; user got message but no chat record |
| Agent-phrased, agent returns text + dispatch OK | `done` / `pending` | message delivered |
| Agent-phrased, agent fails or returns empty | `failed` | logged; **silent — no fallback** |
| Agent-phrased, agent OK but connector fails | `failed` | logged; no message |
| Cross-channel, `get_or_create(dest)` fails | `failed` | logged; no message |
| Job missing at fire (cancelled mid-flight) | n/a | warning log, return |

`attempts` increments on any fire (Phase 1 behavior, unchanged).

**No literal-fallback for failed agent-phrased fires.** The payload is internal-trigger text, not user-facing. Sending it would expose internal phrasing. Failed rows are surfaced via Phase 3's dashboard for manual retry.

## Telemetry / logging

Additions over Phase 1:

- Per-fire log line: `mode={literal,agent_phrased} channel={...} dest_conv_id={...} cross_channel={true,false}`.
- Agent-phrased turn duration logged (it's a model call; surface for cost visibility).
- No new metrics endpoint — structured log lines are sufficient for Phase 2.

## Concurrency

- Two cron rows firing at the same minute, both agent-phrased on the same conversation: agent turns are serialized by the conversation lock (existing supervisor invariant — confirm in plan, fall back to per-conversation `asyncio.Lock` if not).
- No new concurrency primitive introduced in Phase 2.

## Cost note

Every agent-phrased fire is one full agent turn including any tool calls it makes. A `0 9 * * *` cron with `mode="agent_phrased"` = ~365 model calls/year. The plan documents this; no enforcement mechanism. The default remains `mode="literal"` precisely because of this cost.

## Backward compatibility

- All Phase 1 rows have `mode='literal'` (default) and existing `(channel, chat_id)` semantics unchanged. They keep firing identically.
- Tool calls without the new args resolve to Phase 1 behavior (`target_channel="current"`, `mode="literal"`, `scope="user"` for list/cancel — note the scope default *change* is the only behavior shift for callers omitting the arg).

## Testing strategy

Mirrors Phase 1's structure (storage / service / tools / runner test files all exist).

### `test_scheduling_storage.py` (additions)

- `mode` column round-trips on insert/get/list.
- Phase 1-style rows (no `mode` in INSERT) get default `'literal'`.
- `channel_routing` upsert/get/list/disable.
- `init()` is idempotent — second call doesn't re-add `mode` column or fail.

### `test_scheduling_service.py` (additions)

- `schedule_one_shot(target_channel="telegram")` with routing entry → row has `channel='telegram'`, `chat_id=<routed>`.
- `schedule_one_shot(target_channel="telegram")` without routing entry → raises with clear message.
- Same for disabled routing.
- `schedule_one_shot(mode="agent_phrased")` → row has `mode='agent_phrased'`.
- `list_reminders(scope="user")` returns rows from other conversations.
- `list_reminders(scope="conversation")` matches Phase 1 behavior.
- `cancel_reminder` ambiguity matcher with widened candidate set returns `ambiguous` correctly.
- Validation: bad `mode`, bad `target_channel`, bad `scope`, unknown channel.

### `test_scheduling_tools.py` (additions)

- New optional args appear in tool schemas.
- Tools forward `target_channel` / `mode` / `scope` to service correctly.
- Default values match Phase 1 behavior when args omitted (with the documented `scope` default change).

### `test_scheduling_runner.py` (new file or additions to existing)

- `mode='literal'` row → existing PREFIX behavior unchanged.
- `mode='agent_phrased'` row, agent returns text → text dispatched (no PREFIX), persisted to `dest_conv_id`.
- `mode='agent_phrased'` row, agent returns empty → `status='failed'`, no dispatch.
- `mode='agent_phrased'` row, scheduling tools NOT in agent's tool list during the fire-time turn.
- Cross-channel literal: dispatch to destination channel/chat_id, persist to destination conversation.
- Cross-channel agent-phrased: same, with composed text.
- Dashboard-as-destination from non-dashboard origin: takes the dashboard direct-persist branch.
- Connector failure mid-fire → `status='failed'`.

### Mocks/fakes

- Reuse Phase 1's fake `Conversations` and fake `ConnectorRegistry`.
- Extend fake `Conversations` with `get_or_create(channel, chat_id, agent)`.
- For agent-phrased tests, mock the agent invocation path — return canned text. Don't run a real model in unit tests.

### Integration test (one, end-to-end)

- Schedule from dashboard for telegram with `mode="literal"`, fire, assert message hits the fake telegram connector AND lands in the destination telegram conversation.
- Same flow with `mode="agent_phrased"` using a mocked agent — assert composed text dispatched.

### Manual smoke gates (post-merge, recorded for handoff)

1. Schedule literal cross-channel dashboard → telegram, verify it fires correctly.
2. Schedule agent-phrased same-channel on dashboard, verify the composed text differs run-to-run.
3. Schedule agent-phrased cross-channel telegram → dashboard, verify dashboard bubble shows the composed message.
4. Disable telegram in `channel_routing`, verify new schedules raise but in-flight rows still fire.

### Test-count target

Roughly +40-50 tests across the four files (Phase 1 added 51).

## Open implementation questions for the planner

These are intentionally left for `superpowers:writing-plans` to resolve — they're tactical, not architectural:

1. Exact CLI surface for `kc-supervisor channel-routing` (subcommand structure, flag style).
2. Which form of destination resolution to use in the runner: explicit branch on "is this same-channel?" vs always-call `get_or_create` and compare ids. Both work; pick whichever reads cleaner with the existing `ConversationManager` API.
3. Exact entry point for invoking the agent at fire time (which method on the supervisor / runner). Plan should grep for whatever a normal user-message turn calls and reuse it.
4. Whether the `_compose_agent_phrased` agent turn needs an explicit timeout (recommend yes; pick a value — 60s seems reasonable). If no timeout primitive exists, defer to a follow-up.
5. Whether to log `payload` content in the per-fire log line, or just lengths. (Recommend lengths only — payloads may contain personal text.)

## Pointers

- Phase 1 spec: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md`
- Phase 1 plan: `docs/superpowers/plans/2026-05-09-reminders-scheduler-phase1.md`
- Phase 2 handoff (superseded): `docs/superpowers/specs/2026-05-09-reminders-phase2-handoff.md`
- Phase 3 handoff: `docs/superpowers/specs/2026-05-09-reminders-phase3-handoff.md`
- Sammy's Telegram chat id: `8627206839`
- Existing scheduling code:
  - `kc-supervisor/src/kc_supervisor/scheduling/runner.py`
  - `kc-supervisor/src/kc_supervisor/scheduling/service.py`
  - `kc-supervisor/src/kc_supervisor/scheduling/tools.py`
  - `kc-supervisor/src/kc_supervisor/scheduling/context.py`
  - `kc-supervisor/src/kc_supervisor/storage.py` (lines 76-93 for the table)
