# kc-dashboard v0.2.1 polish — design

**Date:** 2026-05-06
**Status:** draft
**Sibling:** [2026-05-06-kc-supervisor-v021-polish-design.md](./2026-05-06-kc-supervisor-v021-polish-design.md) (backend half)

## Scope

Two dashboard surfaces and one minor render change, plus the supervisor HTTP endpoints that back them.

1. **`/connectors` view** — replaces "edit `~/KonaClaw/config/secrets.yaml` and restart" for Telegram, iMessage, Gmail, Calendar, and Zapier. Master-detail UX.
2. **`/connectors/zapier` view** — drill-down zaps management page.
3. **Audit view, denied-row surfacing** — render `decision="denied"` rows distinctly and add a filter chip. Surfaces the audit rows added by sibling spec, item 4.

Information architecture: a single new top-level **Connectors** tab. The existing `Shares` tab is unchanged. The Zaps page lives inside Connectors at `/connectors/zapier` rather than as its own top-level tab.

## Architecture

### IA and routing

```
/                  -> redirect /chat
/chat              (existing)
/agents            (existing)
/connectors        (NEW — master-detail)
/connectors/zapier (NEW — drill-down)
/shares            (existing, unchanged)
/permissions       (existing)
/monitor           (existing)
/audit             (existing, lightly modified)
```

Total top-level tabs: **7**. The Connectors detail panel for Zapier carries a **Manage zaps →** affordance that routes to `/connectors/zapier`.

### `/connectors` (master-detail)

- Left rail: list of 5 connectors with name, icon, status pill (`connected` / `not configured` / `unavailable` for iMessage on non-darwin).
- Right panel: detail for the selected connector. Each connector renders a small typed panel.

| Connector | Panel content |
|---|---|
| Telegram | masked bot-token text input · allowlist editor (chat IDs, add/remove) · Save button |
| iMessage | allowlist editor (handles, add/remove) · Save button · macOS-only notice when supervisor returns `flags.platform_supported: false` |
| Gmail | "Connect with Google" CTA when no token; "Connected as `<email>` · Disconnect" when token present |
| Calendar | mirrors Gmail state — one Google OAuth covers both; copy clarifies that connecting Gmail also connects Calendar |
| Zapier | masked API-key text input · Save button · `Manage zaps →` link · zap count line ("14 zaps available") |

**Secret-entry UX (Telegram, Zapier):** inline edit in the detail panel; masked-stars placeholder when a secret is saved. User clicks the field, types a new value, clicks Save → PATCH writes through `SecretsStore.save()`. After save the field re-displays masked. The dashboard never receives plaintext from `GET /connectors/{name}` — only a `has_value: bool` flag and a length hint.

**Google OAuth flow (server-side, dashboard-triggered):**

```
1. User clicks "Connect with Google" in GooglePanel.
2. Dashboard POST /connectors/google/connect.
3. Supervisor spawns a thread: InstalledAppFlow.run_local_server(...).
   The flow opens a browser tab for the user (same library as today's
   boot path) and listens on a local port for the redirect.
4. Dashboard polls GET /connectors/google/status every 2s. State machine:
     idle → pending → connected (token file written)
                    → idle (flow cancelled / errored, with last_error stamped)
5. On connected, dashboard re-fetches GET /connectors/gmail and
   GET /connectors/calendar to refresh status pills.
```

The token file remains at `~/KonaClaw/config/google_token.json` (no change to its location or scope). `Disconnect` deletes that file and emits a state transition back to `idle`.

### `/connectors/zapier` (zaps page)

Layout:

```
← Connectors  /  ⚡ Zapier  (status pill)

[zaps available 14] [last refresh 2m ago] [MCP transport: Streamable HTTP]

[ search box ............................... ]  [↻ Refresh]  [Add zap on zapier.com ↗]

| Tool name                       | Description                       | Last used | Calls |
| mcp.zapier.gmail_send           | Send a Gmail message via Zapier   | 3h ago    | 7     |
| mcp.zapier.notion_create_page   | Create a Notion page in a database| yesterday | 2     |
| ...                             |                                   |           |       |

[Zapier API key field — masked, editable, Save]
```

- **Search** filters client-side over `tool` + `description`.
- **Refresh** = cheap path. Calls `POST /connectors/zapier/refresh`, which calls `registry.load_all()` server-side. Catches zaps the supervisor's MCP handle has already discovered but not yet rebuilt into agent tool registries. Does **not** re-handshake with `mcp.zapier.com` — for "I just made a new zap on zapier.com," the user restarts the supervisor (a v0.3 follow-up could surface a "force re-handshake" affordance but is out of scope here).
- **Last used / Calls** are derived from the audit table on the supervisor side: `SELECT tool, MAX(ts), COUNT(*) FROM audit WHERE tool LIKE 'mcp.zapier.%' GROUP BY tool` joined left against the live MCP tool list. Tools never called show `Last used: never`, `Calls: 0`.
- **API key field** appears here as well as on the parent /connectors detail panel — both surfaces edit the same `zapier_api_key` secret. The Zaps page makes it discoverable when the user is already in the zaps context.

### Audit view changes

Existing `GET /audit` returns rows with `decision` in (`allowed`, `denied`). The view today only renders allowed rows because no denied ones existed. After sibling-spec item 4 ships, both kinds appear.

Changes to `kc-dashboard/src/views/Audit.tsx`:

1. **Render denied rows distinctly.** Small red `denied` pill in the decision column. Tooltip on hover shows `result.reason` (the deny reason JSON-stashed by the backend).
2. **Filter chip row.** Three pills above the table: `All` · `Allowed only` · `Denied only`. Defaults to `All`. Persists in URL via search param (`?decision=denied`) so a link to the audit view filtered to denials is shareable.
3. **No Undo button on denied rows.** Existing logic already keys off `undoable=1`; denied rows have `undoable=0`, so no extra guard needed.

**Backend change:** `Storage.list_audit` and `GET /audit` gain an optional `decision: str | None` param so the filter chip can roundtrip via URL. The existing rows already carry `decision` and `result` — only the filter capability is new.

### Frontend pieces (kc-dashboard)

New files:
- `src/views/Connectors.tsx` — master-detail container, fetches `GET /connectors`, hosts the right panel.
- `src/views/Zaps.tsx` — the zapier drill-down page.
- `src/components/connectors/ConnectorList.tsx` — left rail list.
- `src/components/connectors/TelegramPanel.tsx`
- `src/components/connectors/IMessagePanel.tsx`
- `src/components/connectors/GooglePanel.tsx` — handles connect/disconnect/poll-status.
- `src/components/connectors/ZapierPanel.tsx` — used on /connectors detail; reuses `SecretInput`.
- `src/components/connectors/SecretInput.tsx` — masked text input with show/hide toggle and Save button. Reused by Telegram, Zapier panels and the API-key block on /connectors/zapier.
- `src/components/connectors/AllowlistEditor.tsx` — chip list with add/remove. Reused by Telegram, iMessage.
- `src/api/connectors.ts` — fetchers + TanStack Query hooks for the new endpoints.

Edited files:
- `src/main.tsx` — add `/connectors` and `/connectors/zapier` routes.
- `src/App.tsx` — add Connectors nav item.
- `src/views/Audit.tsx` — denied-row rendering + filter chip.
- `src/api/audit.ts` (or wherever the existing audit fetcher lives) — accept optional `decision` filter param.

### New supervisor HTTP surface

Module: `kc-supervisor/src/kc_supervisor/connectors_routes.py` (new) plus a thin router include in `service.py`.

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/connectors` | list connectors with summary | `[{name, status, has_token, allowlist_count, summary}]` |
| GET | `/connectors/{name}` | detail for one connector | `{name, status, has_token, token_hint?, allowlist?, flags}` |
| PATCH | `/connectors/{name}` | write secret/allowlist; partial body | `{ok: true}` (no plaintext returned) |
| POST | `/connectors/google/connect` | kick OAuth in a background thread | `202 {state: "pending"}` |
| GET | `/connectors/google/status` | poll OAuth state | `{state, since, last_error?}` |
| POST | `/connectors/google/disconnect` | delete `google_token.json` | `{ok: true}` |
| GET | `/connectors/zapier/zaps` | list zapier tools with audit join | `[{tool, description, last_used_ts, call_count}]` |
| POST | `/connectors/zapier/refresh` | call `registry.load_all()` | `{ok: true, refreshed_at}` |

**Plaintext discipline.** The dashboard never receives plaintext secrets after they are saved. `GET /connectors/{name}` returns `has_token: bool` and an optional `token_hint` (last 4 chars only) so the user can verify which token they have without revealing it. `PATCH` accepts plaintext over `localhost:8765` (already trusted in the local-only model), validates, and writes through `SecretsStore.save()`.

**Allowlist storage.** Allowlists today live in `secrets.yaml` (`telegram_allowlist`, `imessage_allowlist`). Same path, just edited via PATCH instead of by hand.

**OAuth state machine.** A small in-memory record on `Deps`:

```python
@dataclass
class GoogleOAuthState:
    state: Literal["idle", "pending", "connected"]
    since: float           # epoch
    last_error: str | None
```

`POST /connectors/google/connect` is a no-op if `state == "pending"` — guards against double-clicks. Real success transitions `pending → connected` via a callback fired after `flow.fetch_token()`.

## Data flow

### Pairing a Telegram bot

```
User opens /connectors → clicks Telegram in left rail.
GET /connectors/telegram → {status: "not configured", has_token: false, allowlist_count: 0}
TelegramPanel renders empty SecretInput + empty AllowlistEditor.

User pastes bot token, clicks Save.
PATCH /connectors/telegram body {bot_token: "8123:..."}
  supervisor: SecretsStore.load() → set telegram_bot_token → SecretsStore.save()
  supervisor: hot-restart TelegramConnector
              (stop existing, build new from updated secrets, start with InboundRouter)
  return 200 {ok: true}

Dashboard re-fetches GET /connectors/telegram → status pill flips to "connected".
User adds "@sammydallal" to allowlist → PATCH /connectors/telegram body {allowlist: ["@sammydallal"]}
  supervisor: same save+restart path.
```

### Google OAuth

```
User clicks "Connect with Google".
POST /connectors/google/connect → 202 {state: "pending"}
  supervisor spawns a thread; InstalledAppFlow.run_local_server(host=...,
  port=0, ...) opens user's browser to consent screen.
Dashboard poll loop (every 2s):
  GET /connectors/google/status → {state: "pending"}
  ...
  flow completes server-side → google_token.json written; state := "connected"
  GET /connectors/google/status → {state: "connected", since: 1778130000}
  Dashboard refetches /connectors/gmail and /connectors/calendar to refresh pills.
```

### Zaps refresh

```
User clicks Refresh on /connectors/zapier.
POST /connectors/zapier/refresh
  supervisor: deps.registry.load_all()  # rebuilds every agent's tool list
  return {ok: true, refreshed_at: now}
Dashboard refetches GET /connectors/zapier/zaps.
```

### Denied-row surfacing in Audit

```
sibling-spec item 4 writes audit rows with decision="denied" when permission is denied.
Audit.tsx now reads each row's decision field.
  decision="allowed" → green pill, optional Undo button if undoable=1 (existing).
  decision="denied"  → red pill, tooltip with result.reason. No Undo button.
Filter chip [All | Allowed only | Denied only] toggles a `decision`
query param on GET /audit (extended from today's agent+limit-only signature).
```

## Error handling

- **PATCH /connectors/{name} with bad body** → 422 with field error. Detail panel shows inline error under the field; secret is not saved.
- **Telegram connector restart fails** (e.g., bot token rejected by Telegram API at startup) → 200 still returned for PATCH (secret was saved), but `GET /connectors/telegram` returns `status: "error", error: "telegram API rejected token"`. UI surfaces this in the detail panel with a red banner.
- **OAuth flow errors** (user cancels, network timeout) → state transitions back to `idle` with `last_error` stamped. UI shows error message and re-enables the Connect button.
- **/connectors/zapier/zaps when Zapier MCP is not configured** → returns `[]` and `{status: "not configured"}` in the parent /connectors detail. Refresh button disabled.
- **Audit endpoint returns row with malformed `result` JSON** (defensive) → render `decision="denied"` pill with a generic "no reason recorded" tooltip rather than crashing.

## Testing

### Frontend (vitest, ~7 new in kc-dashboard)

- `Connectors.test.tsx` — list renders 5 connectors; clicking one updates detail panel.
- `SecretInput.test.tsx` — masked display when `has_value: true`; PATCH fires on Save with plaintext; field re-masks on success.
- `GooglePanel.test.tsx` — state machine: idle → click triggers POST → pending → polled → connected; cancel from server flips back to idle.
- `Zaps.test.tsx` — table sorts by Last used; search filters across name + description; Refresh button calls the right endpoint.
- `Audit.test.tsx` — denied row renders red pill + reason tooltip; filter chip persists in URL.

### Backend (pytest, ~5 new in kc-supervisor)

- `test_connectors_list.py` — `GET /connectors` shape with various secret presence combos.
- `test_connectors_secret_masking.py` — `GET /connectors/telegram` never returns plaintext; `PATCH` saves through SecretsStore.
- `test_connectors_google_oauth_state.py` — POST `/connectors/google/connect` returns 202; double-click is a no-op while pending; status transitions correctly.
- `test_connectors_zapier_zaps.py` — `/connectors/zapier/zaps` joins audit (last_used_ts, call_count) correctly; missing MCP returns `[]`.
- `test_connectors_zapier_refresh.py` — `POST /connectors/zapier/refresh` invokes `registry.load_all()`.

### Smoke (manual, in `SMOKE.md`)

- Open `/connectors`, paste a fresh Telegram bot token, save; bot starts replying without supervisor restart.
- Open `/connectors/zapier`, click Refresh; tool count updates if a zap was added since last reload.
- Open `/connectors`, click Connect with Google; complete OAuth in browser tab; pill flips to connected.
- Trigger a tool that requires approval; click Deny; open Audit view; verify row shows red `denied` pill with reason tooltip; filter to Denied only and confirm.

## Out of scope (deferred)

- iMessage token rotation / pairing per se — only allowlist editing (matches today's reality; iMessage uses the user's Messages.app session via AppleScript).
- Per-zap install-history view — audit table covers this transversally; a dedicated view is not worth the surface area.
- Multi-Google-account support — one google_token.json, one Gmail+Calendar identity.
- OAuth scope editing — scopes hard-coded as today; v0.3+.
- Force re-handshake with Zapier MCP from the dashboard — restart-supervisor for now.
- Connector pairing UI for filesystem **Shares** — that view stays a stub until the umbrella spec's "Shares pairing" item lands.

## Cross-references

- [2026-05-02-konaclaw-design.md](./2026-05-02-konaclaw-design.md) — umbrella spec.
- [2026-05-06-kc-supervisor-v021-polish-design.md](./2026-05-06-kc-supervisor-v021-polish-design.md) — sibling spec; provides the encrypted SecretsStore that PATCH writes through, plus the denied audit rows that `Audit.tsx` now surfaces.
- `kc-dashboard/src/views/Chat.tsx` — reference master-detail pattern that `Connectors.tsx` mirrors.
