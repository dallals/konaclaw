# kc-dashboard — Smoke Checklist

Run on the target machine after `npm install` (in kc-dashboard) and `pip install -e .` (in kc-dashboard-server).

## Dev mode

- [ ] `npm run dev` starts Vite on `http://localhost:5173` — open in a browser.
- [ ] All six tab links visible in the top nav.
- [ ] With kc-supervisor running on `:8765`:
  - [ ] Chat: pick an agent, click "Start new", type "hello", see the assistant reply appear.
  - [ ] Agents: list shows configured agents with status pills.
  - [ ] Audit: shows tool calls; Undo button visible only on undoable rows.
  - [ ] Permissions: trigger a destructive action from another client (e.g., a file.delete via curl POST to a chat WS), see it appear with Approve/Deny.
  - [ ] Monitor: shows uptime + agent count.

## Production build

- [ ] `npm run build` — completes; `dist/` produced.
- [ ] `kc-dashboard-server` boots on `:8766` and the same flow above works against the built bundle.

## Vitest + Playwright

- [ ] `npm run test` — all unit tests green.
- [ ] `npm run e2e` — Playwright runs; tests that require the supervisor skip cleanly when it's not up.
  - First-time setup: `npx playwright install` to download the browser bundles.

## Connectors view (v0.2.1)

With kc-supervisor running on `:8765` and the dashboard on `:5173`:

- [ ] Open `/connectors` in the dashboard.
  - [ ] All 5 connectors visible in the left rail (Telegram, iMessage, Gmail, Calendar, Zapier).
  - [ ] Status pills reflect supervisor state (green=connected, gray=not configured, red=error, line=unavailable).
  - [ ] iMessage panel shows the allowlist editor on macOS, "platform unavailable" message off-Darwin.
- [ ] Telegram: paste a bot token → Save → bot starts replying to messages from allowlisted chats without supervisor restart.
- [ ] Click "Connect with Google" → consent in browser tab → return to dashboard → Gmail + Calendar pills both flip green within ~5s (poll loop catches the OAuth completion).
- [ ] Open `/connectors/zapier` (deep-link from Zapier panel's "Manage zaps →"). Verify:
  - [ ] Live `mcp.zapier.*` tools list with Last used + Calls populated from audit.
  - [ ] Search filters by tool name or description.
  - [ ] Refresh button re-runs `registry.load_all()` server-side and refreshes the list.
  - [ ] Editing the API key at the bottom persists through SecretsStore.
- [ ] Trigger a destructive tool from Chat. Click Deny in Permissions. Open `/audit`:
  - [ ] Denied row renders red pill (`bg-bad/20`) with reason in tooltip.
  - [ ] Click "Denied" filter chip → URL updates to `?decision=denied` → only denied rows visible.
  - [ ] Click "All" chip → URL clears → both allowed and denied rows visible.
  - [ ] Click "Allowed" chip → URL `?decision=allowed` → only non-denied rows visible.

Plaintext tokens MUST NOT appear in any HTTP response. Verify by opening DevTools Network tab during a Save and confirming the response body is `{ok: true}` with no `bot_token` / `api_key` field.

## News widget (Chat view)

- [ ] Open Chat tab → News widget visible on the right.
- [ ] Toggle to **Source**, enter `bbc-news`, click Go → headlines render.
- [ ] Click a headline → opens in new tab.
- [ ] Click ⌃ on the widget → collapses to a thin column; reload → still collapsed.
- [ ] Click ⌃ again → expands; last query/mode pre-filled.
- [ ] If `newsapi_api_key` is unset on the supervisor → widget shows
      "News not configured" banner instead of results.

## tokens-per-second metric (added 2026-05-09)

- [ ] Send a single-message reply to any agent. The chat header `Last reply` row should briefly show `~NN t/s · streaming` and then snap to a stable `NN t/s · NNN tok` value. The `TTFB` row appears with `N.NN s`.
- [ ] The completed assistant bubble has a faint mono footer reading `NN t/s · NNN tok · ttfb N.NN s`.
- [ ] Send a message that triggers a tool call (e.g. ask Kona to use a calendar/Gmail tool). The header's `TTFB` row shows `N.NN s · 2 calls` and the bubble badge has `· 2 calls`.
- [ ] Reload the dashboard. The historical assistant bubbles still show their tok/s badges (read from SQLite).
- [ ] Switch the supervisor to point at a provider that does NOT support `stream_options.include_usage` (an old proxy or stub). The header row reads `~NN t/s · estimate`; the bubble shows `— ttfb only · ttfb N.NN s`.
