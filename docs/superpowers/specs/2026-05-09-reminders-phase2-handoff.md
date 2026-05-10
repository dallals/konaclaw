# Reminders Phase 2 — handoff (not yet brainstormed)

**Status:** **Pre-brainstorm.** This is a handoff doc capturing what we agreed during Phase 1 brainstorming about Phase 2's scope. A future session must run `superpowers:brainstorming` over this scope and produce a real design spec before any code.

**Predecessor:** Phase 1 spec at `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md` (merged 2026-05-09).

## Scope (agreed at Phase 1 brainstorming)

Phase 2 adds two capabilities Phase 1 deliberately deferred:

1. **Cross-channel scheduling.** Today (Phase 1) reminders fire back into the conversation they were scheduled from. Phase 2 lets the agent specify a different recipient channel — e.g., Sammy asks Kona on the dashboard to remind him on Telegram. Requires:
   - A `channel` parameter on the agent tools (`schedule_reminder`, `schedule_cron`) accepting `"current"` (Phase 1 default), `"telegram"`, `"dashboard"`, `"imessage"`. `"current"` continues to mean "the conversation I'm in."
   - A config table mapping `channel → default chat_id` for explicit-channel scheduling. Sammy's Telegram chat id is already known: `8627206839` (per memory).
   - Permission/approval gating for cross-channel — at minimum, log audit entries; consider raising the tier from safe-auto to mutating-with-approval since prompt injection on Channel A can no longer be contained to Channel A.

2. **Agent-phrased mode.** Today reminders fire the *literal* `payload` string with a `⏰ ` prefix. Phase 2 adds an opt-in mode where, at fire time, the supervisor re-runs the agent with a system prompt like *"It's 5pm — you scheduled a reminder for Sammy about dinner. Compose a friendly message and send it via outbound_send."* The agent generates the actual message text. Requires:
   - A `mode` column already exists on `scheduled_jobs` (added speculatively in Phase 1 — verify; if not, additive ALTER).
   - A new outbound-send tool the agent invokes during the fire-time turn.
   - Cost / latency tradeoff: every fire becomes one full agent turn with model call. Default should remain `literal`; `agent_phrased` is opt-in via tool arg.

## Constraints from Phase 1 (load-bearing)

- `scheduled_jobs.kind` is currently `'reminder' | 'cron'`. Don't add a third kind for "agent_phrased" — instead use the `mode` column, since both reminder and cron rows can be agent-phrased.
- `ScheduleService.schedule_one_shot` and `schedule_cron` currently accept `(when/cron, content, conversation_id, channel, chat_id, agent)`. Phase 2 adds an optional `target_channel` parameter that, when set, overrides the conversation's channel and resolves `chat_id` from the config table.
- The contextvar in `kc_supervisor/scheduling/context.py` plumbs the *invocation* context. Phase 2 needs to keep that pristine — the cross-channel target is a tool argument, not a contextvar override.
- The dashboard channel has no `Connector`; `ReminderRunner.fire` branches on `row["channel"] == "dashboard"` and persists directly. Cross-channel scheduling toward dashboard from a Telegram-originated turn must hit this same branch correctly.
- APScheduler jobs use a module-level `fire_reminder(job_id)` function. Don't add a second fire path; route everything through `fire_reminder` and branch internally on `mode`.
- `connector_registry` may be `None` at boot (gated in `main.py`). If Phase 2 mandates cross-channel, also fix `main.py` to always construct a registry (possibly empty), so reminders scheduled before the user wires up Telegram still have somewhere to dispatch to. (See I1 in the Phase 1 final review for context.)

## Open questions for the Phase 2 brainstorming session

- Is cross-channel gated on a per-channel allowlist (e.g., Sammy explicitly opts in to Telegram-as-destination) or is `"telegram"` always allowed if a `TelegramConnector` exists?
- For agent-phrased mode, what's the system prompt template? Does the agent see the original conversation history or only a stub?
- Should the agent-phrased fire create a new conversation or append to the original? (Recommendation: append, so all turns including the agent-phrased reminder live in one log.)
- Does cancel-by-description match across channels (Phase 2 list_reminders may show reminders for OTHER channels too — does the agent need a `scope=all` mode added in Phase 2?)
- Audit posture: keep safe-auto, or upgrade cross-channel scheduling to require approval?

## Recommended phasing within Phase 2

If Phase 2 ends up large during brainstorming, consider splitting:

- **Phase 2a:** Cross-channel only. Adds `target_channel` arg, config table, audit upgrade if any.
- **Phase 2b:** Agent-phrased mode only. Adds `mode` column writes, outbound-send tool, fire-time agent turn.

Each is independently shippable and useful.

## Pre-flight before brainstorming

- Re-read the Phase 1 spec and the Phase 1 final-review notes (in conversation history at merge SHA — pull via `git log` on main).
- Re-read `kc-supervisor/src/kc_supervisor/scheduling/runner.py` and `service.py` to refresh on the production wiring.
- Confirm whether the `mode` column was actually added to `scheduled_jobs` in Phase 1 (grep the migration). If not, Phase 2's first task is to add it.

## Pointers

- Phase 1 spec: `docs/superpowers/specs/2026-05-09-reminders-scheduler-phase1-design.md`
- Phase 1 plan: `docs/superpowers/plans/2026-05-09-reminders-scheduler-phase1.md`
- Phase 3 handoff: `docs/superpowers/specs/2026-05-09-reminders-phase3-handoff.md`
- Sammy's Telegram chat id: `8627206839` (per memory)
