# Reminders Phase 3 — manual smoke gates

Run after merging the implementation. Each gate is a fresh dev environment:
both supervisor and dashboard running, Telegram + iMessage configured.

## SG-1 — One-shot appears in the Reminders tab
Schedule a one-shot ~1 minute out from chat. Open the Reminders tab.
- [ ] Row appears within 30s with status `pending`.
- [ ] `next_fire` shows a sensible countdown (e.g., "in 53s").
- [ ] Channel pill matches the conversation channel.

## SG-2 — Snooze pushes the fire time
- [ ] Click the ⏱ icon, then `+15m`.
- [ ] Row's `next_fire` updates immediately (WS-driven, no manual refresh).
- [ ] Row remains `pending`.

## SG-3 — Cancel from dashboard
- [ ] Click ×, then Confirm.
- [ ] Row stays in the list with status `cancelled` (filter chip "cancelled" reveals it).
- [ ] Verify in chat: `list_reminders` no longer shows this row in active list.

## SG-4 — Bubble linking
Schedule a one-shot ~10s out and let it fire.
- [ ] Bubble appears in Chat with footer `↻ from reminder #N`.
- [ ] Click the footer → Reminders tab opens, row highlighted with a 2s pulse.
- [ ] Row status is `done`.

## SG-5 — Cross-channel realtime
Open two browser tabs to the dashboard.
- [ ] Snooze in tab A.
- [ ] Tab B updates the same row without manual refresh.

## SG-6 — Failed path surfaces correctly
Force a runner failure (e.g., disable the destination connector temporarily, schedule a reminder targeting it, let it fire).
- [ ] Row appears in `failed` filter chip with status `failed`.
- [ ] No retry button is offered (out of scope).
- [ ] If a bubble was persisted before failure, the link still resolves.

## SG-7 — Cron round-trip
- [ ] Schedule a `0 9 * * *` cron from chat.
- [ ] Recurring tab shows it; expand panel shows the cron spec.
- [ ] Snooze button is hidden on cron rows.
- [ ] Cancel inline → row goes `cancelled`; verify cron stops firing (wait one cycle or check APS jobs).

Mark each gate ✅ before merging Phase 3 to main.
