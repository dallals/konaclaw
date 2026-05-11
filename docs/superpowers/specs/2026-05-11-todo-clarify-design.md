# Todo + Clarify Tools — Design Spec

**Date:** 2026-05-11
**Phase:** Tools Rollout — Phase C
**Status:** Design (pre-plan)

## Summary

Two conversation-internal tools for KonaClaw agents:

- **`todo.*`** — a six-op task list Kona reads and writes during a conversation. Items are conversation-scoped by default; passing `persist=true` lifts an item to agent-scope so it survives across conversations and supervisor restarts. Backed by a new `todos` table in the supervisor's SQLite. Surfaces as a right-side sidebar in the Chat view (parallel to `NewsWidget`).
- **`clarify`** — a single tool that asks the user a multiple-choice question and blocks until they click an answer (or skip, or timeout). Mirrors the existing `ApprovalBroker` pattern. Surfaces as an inline card in the chat transcript (parallel to `ApprovalCard`).

Both tools live as subpackages inside `kc-supervisor` (parallel to `kc_supervisor.scheduling/`), since both are conversation-coupled and need direct access to supervisor internals (`conversation_id`, current agent, storage, WebSocket).

Phase C of the post-Skills tools rollout. Phases A (terminal_run) and B (web_search / web_fetch) shipped earlier in May 2026.

## Goals

1. Give Kona a scratch-pad for in-conversation task tracking distinct from time-based reminders (which fire at a scheduled time and are persistent).
2. Give Kona a way to ask the user a finite-choice question with a click-not-type UX, faster than a free-text round-trip.
3. Reuse existing patterns: `ApprovalBroker` for the blocking interaction, `NewsWidget` for the sidebar shape, `scheduling/` for the subpackage layout.
4. Surface both tools in the dashboard so the user can directly manipulate state (tick a todo, click a clarify) without going through Kona.

## Non-Goals

- Cross-agent todo visibility. Agent-scoped means *this agent's* persistent items, not "all agents share one list."
- `todo.reopen` op. Re-opening completed items is not a v1 workflow.
- Drag-to-reorder. Items are ordered by `created_at`; manual re-prioritization is out for v1.
- Multi-select clarify. If you need to pick multiple, ask twice.
- Structured `{value, label}` clarify choices. Plain strings only.
- Free-text "Other..." input in clarify. If the answer isn't in the choices, the user can type a regular chat message — Kona isn't blocking the keyboard.
- "Due date" or "snooze" on todos. Those are reminder concepts; don't conflate.
- Persisting clarify state across supervisor restarts. The awaiting tool call dies with the supervisor; persisting the pending question is engineering for an unreachable case.
- Enable flag (`KC_TODOS_ENABLED` / `KC_CLARIFY_ENABLED`). These are conversation primitives with no external cost or security surface; they're always on for Kona once shipped, like scheduling.

## Architecture

### Package layout

Two new subpackages inside `kc-supervisor`, parallel to `kc_supervisor.scheduling/`:

```
kc-supervisor/src/kc_supervisor/
  todos/
    __init__.py
    storage.py        # SQLite CRUD against the new `todos` table
    tools.py          # build_todo_tools(storage, current_context) -> list[Tool]
  clarify/
    __init__.py
    broker.py         # ClarifyBroker (mirrors ApprovalBroker)
    tools.py          # build_clarify_tool(broker, current_context) -> Tool
```

### Wiring

In `kc_supervisor/assembly.py`, both go through the existing `current_context` helper (which already exists for `scheduling`) and register only on `Kona` (matching the scheduling-tools precedent):

```python
if cfg.name == "kona":
    from kc_supervisor.todos.tools import build_todo_tools
    from kc_supervisor.clarify.tools import build_clarify_tool
    for t in build_todo_tools(todos_storage, current_context):
        registry.register(t); tier_map[t.name] = Tier.SAFE
    clarify_tool = build_clarify_tool(clarify_broker, current_context)
    registry.register(clarify_tool); tier_map[clarify_tool.name] = Tier.SAFE
```

The `todos_storage` and `clarify_broker` instances are built once in `main.py` (like `ApprovalBroker` and `ScheduleService` are today) and passed through `Deps` → `AgentRegistry` → `assemble_agent` as kwargs.

### SQLite migration

The `todos` table is added to the existing supervisor DB at `~/KonaClaw/data/konaclaw.db`. Schema is created (idempotent) inside `Storage.init()` alongside the existing tables. No new database file. No new top-level package.

### Why all SAFE tier

Every tool (six todo ops + one clarify) is `Tier.SAFE`:
- Todos touch only the supervisor's own DB; no external API, no filesystem outside the supervisor's data dir, no network egress.
- Clarify pushes a WebSocket frame and awaits a future; nothing destructive happens. The user explicitly chooses an option — the tool just relays that choice back to the model.

`Tier.SAFE` means no approval prompt. Asking the user to approve every `todo.add` would be ridiculous; asking them to approve every `clarify` (which itself is an approval-like interaction) would be a UX dead-end.

## Todo Tool Surface

### Schema

```sql
CREATE TABLE IF NOT EXISTS todos (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  agent           TEXT    NOT NULL,
  conversation_id INTEGER NULL,                           -- NULL = agent-scoped (persistent)
  title           TEXT    NOT NULL,
  notes           TEXT    NOT NULL DEFAULT '',
  status          TEXT    NOT NULL CHECK (status IN ('open','done')) DEFAULT 'open',
  created_at      REAL    NOT NULL,
  updated_at      REAL    NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_todos_agent_conv ON todos (agent, conversation_id);
CREATE INDEX IF NOT EXISTS idx_todos_status     ON todos (status);
```

`conversation_id` is nullable: when set, the todo is conversation-scoped; when NULL, it's agent-scoped (persistent across conversations and restarts). `FOREIGN KEY ... ON DELETE CASCADE` ensures that deleting a conversation auto-deletes its conversation-scoped todos but leaves the agent-scoped ones intact.

### Operations

Six tools. Naming follows the existing `gmail.*` / `gcal.*` convention (lowercase, dot-separated).

**`todo.add`** — create an item.

```jsonc
// params
{ "title":   "string",   // REQUIRED, non-empty after strip
  "notes":   "string",   // optional, default ""
  "persist": false }     // optional, default false → conversation-scoped

// success return
{ "id":              42,
  "title":           "Pack sunscreen",
  "notes":           "Heather allergic — Neutrogena brand",
  "status":          "open",
  "scope":           "conversation" | "agent",
  "conversation_id": 40,                          // null if scope=agent
  "created_at":      "2026-05-11T16:00:00Z" }
```

When `persist=true`, `conversation_id` is set to NULL in the row.

**`todo.list`** — list visible items.

```jsonc
// params
{ "status": "open" | "done" | "all",                       // optional, default "open"
  "scope":  "all" | "conversation" | "agent" }              // optional, default "all"

// success return
{ "items": [ { ...same shape as add return... }, ... ],
  "count": 3 }
```

`scope="all"` is the most useful default: returns rows where `agent = <current> AND (conversation_id = <current_conv> OR conversation_id IS NULL)`. Each item includes `scope` so Kona can refer to them differently in conversation ("the persistent one about your passport").

Ordering: `created_at ASC` (oldest first). Stable across calls.

**`todo.complete`** — mark an item done.

```jsonc
// params
{ "id": 42 }

// success return
{ "id": 42, "status": "done", "completed_at": "2026-05-11T16:05:00Z" }
```

Sets `status = 'done'` and `updated_at = now`. Idempotent — completing a `done` item is a no-op success.

**`todo.update`** — edit title and/or notes (NOT status).

```jsonc
// params
{ "id":    42,
  "title": "string",   // optional
  "notes": "string" }  // optional (at least one of title/notes must be present)

// success return
// full item shape
```

Status changes go through `todo.complete` (or `todo.delete`); `todo.update` is for renames and note edits. This separation keeps the surface explicit — accidentally passing `status: "done"` in an update won't mark something done.

**`todo.delete`** — remove the row entirely.

```jsonc
// params
{ "id": 42 }

// success return
{ "id": 42, "deleted": true }
```

Hard delete. No soft-delete / archive for v1.

**`todo.clear_done`** — sweep all completed items in the requested scope.

```jsonc
// params
{ "scope": "all" | "conversation" | "agent" }   // optional, default "all"

// success return
{ "deleted_count": 4 }
```

`scope="all"` deletes done items where the row is in this conversation OR is agent-scoped for this agent. `scope="conversation"` is conversation-only. `scope="agent"` is agent-scoped-only. Useful when wrapping up: "clear out everything we finished."

### Error returns (all six tools)

All error returns are JSON, never raised to the agent:

```jsonc
{ "error": "missing_title" }              // add: empty / whitespace-only title
{ "error": "missing_id" }                 // complete/update/delete: missing id param
{ "error": "not_found",     "id": 42 }    // complete/update/delete: id doesn't exist
{ "error": "wrong_agent",   "id": 42 }    // tried to operate on another agent's todo
{ "error": "wrong_conversation", "id": 42 }   // tried to operate on conv-scoped todo from a different conversation (does NOT apply to agent-scoped todos)
{ "error": "invalid_status", "value": "..." }  // list: bad status param
{ "error": "invalid_scope",  "value": "..." }  // list/clear_done: bad scope param
{ "error": "missing_fields" }             // update: neither title nor notes provided
```

### Access control

A todo row is only addressable from the agent that created it. If `Research-Agent` somehow tried to complete one of Kona's todos by id, the supervisor returns `wrong_agent` (the row check at `agent = current_agent` catches it). Same for cross-conversation access of conversation-scoped items.

## Clarify Tool Surface

### Tool

One tool, one shape.

```jsonc
// params
{ "question":         "string",   // REQUIRED, non-empty after strip
  "choices":          ["string", ...],   // REQUIRED, 2-8 items, no duplicates
  "timeout_seconds":  300 }       // optional; default 300; clamped to [10, 600]

// success return (user clicked a choice)
{ "choice":        "Tuesday",
  "choice_index":  1,
  "elapsed_ms":    4523 }

// return on user skip
{ "choice": null, "reason": "skipped" }

// return on timeout
{ "choice": null, "reason": "timeout", "elapsed_ms": 300000 }

// validation errors
{ "error": "missing_question" }
{ "error": "missing_choices" }
{ "error": "too_few_choices",   "count": 1, "minimum": 2 }
{ "error": "too_many_choices",  "count": 9, "maximum": 8 }
{ "error": "duplicate_choices", "values": ["Tuesday"] }
```

### ClarifyBroker

Mirrors `ApprovalBroker` (in `kc_supervisor/approvals.py`). Lives in `kc_supervisor/clarify/broker.py`.

```python
class ClarifyBroker:
    """In-memory broker for pending clarification requests.

    Each request_clarification() allocates a future. The clarify tool's impl
    awaits the future via asyncio.wait_for(timeout=timeout_seconds). The
    dashboard's WS handler calls resolve() when the user clicks an option
    or skip, which completes the future.

    Subscribers are WS connections that receive clarify_request frames
    when the broker is asked to publish a new question.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingClarify] = {}
        self._subscribers: list[Callable[[dict], None]] = []

    async def request_clarification(
        self,
        *,
        conversation_id: int,
        agent: str,
        question: str,
        choices: list[str],
        timeout_seconds: int,
    ) -> dict: ...

    def resolve(
        self,
        request_id: str,
        *,
        choice: str | None,
        reason: str = "answered",
    ) -> None: ...

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]: ...

    def pending_for_conversation(self, conversation_id: int) -> list[dict]: ...
```

`pending_for_conversation` is used by the dashboard's `/ws/chat/{conversation_id}` connect handler to re-send any in-flight clarify requests to a reconnecting client (e.g., page reload during a clarify).

### WebSocket frames

Extend the existing `/ws/chat/{conversation_id}` protocol with two new frame types. Don't introduce a separate WS endpoint.

**Server → client** (when a clarify call starts):

```jsonc
{ "type":             "clarify_request",
  "request_id":       "abc123",
  "conversation_id":  40,
  "question":         "Which day works?",
  "choices":          ["Monday", "Tuesday", "Wednesday"],
  "timeout_seconds":  300,
  "started_at":       "2026-05-11T16:00:00Z" }
```

**Client → server** (when the user clicks):

```jsonc
{ "type":       "clarify_response",
  "request_id": "abc123",
  "choice":     "Tuesday" }

// or skip
{ "type":       "clarify_response",
  "request_id": "abc123",
  "choice":     null,
  "reason":     "skipped" }
```

The server's WS handler routes the response to `broker.resolve(request_id, choice=...)`. The broker completes the future. The tool returns the resolution payload to the model.

### Audit

Each resolved clarification (answered / skipped / timed out) writes one row to the existing `audit` table:

```
tool      = "clarify"
agent     = current agent name
args_json = {"question": "...", "choices": [...], "timeout_seconds": 300}
result    = {"choice": "...", "reason": "...", "elapsed_ms": ...}   // the future's resolution
decision  = "tier"   // SAFE-tier, auto-allowed
```

Visible in the existing audit log surface.

### Reconnection

If the dashboard's WS drops mid-clarify (network blip, page reload), the awaiting tool call on the server side keeps running — the future hasn't resolved yet. When the client reconnects to `/ws/chat/{conversation_id}`, the WS handler queries `broker.pending_for_conversation(N)` and re-sends each in-flight `clarify_request` frame. The dashboard re-renders the card. From the user's perspective: card briefly disappeared, then reappeared with the original countdown still ticking.

If the supervisor crashes during a clarify, the awaiting tool call dies with the process and the broker state goes too. On the dashboard side, the WS reconnect to the new supervisor sees no pending clarifies, so any rendered card disappears. From the user's perspective: card vanishes. From the model's perspective: the next conversation turn after restart starts fresh; the model can ask again if it still cares.

## HTTP Routes (new)

These mirror Kona's tool surface so the dashboard can manipulate state without going through chat.

```
GET    /todos?conversation_id=N&status=open                # returns {items: [...], count: N}
POST   /todos                                              # body: {title, notes?, persist?}, returns the row
PATCH  /todos/{id}                                         # body: {title?, notes?, status?}, returns the row
DELETE /todos/{id}                                         # returns 204
DELETE /todos?scope=conversation&conversation_id=N&status=done   # bulk; returns {deleted_count: N}
```

Note: `PATCH /todos/{id}` allows `status` (unlike the agent tool surface which routes status through `complete`). This is the dashboard's direct-manipulation path — the checkbox in the sidebar fires PATCH with `{status: "done"}`.

**Auth/scoping:** routes take the conversation_id as a query param and the supervisor injects the agent from the conversation row. No external auth — the supervisor binds to 127.0.0.1 only.

No new routes for clarify — all WebSocket.

## Dashboard Surfaces

### TodoWidget (right sidebar in Chat view)

New files:

```
kc-dashboard/src/
  api/todos.ts                            # typed wrapper
  components/TodoWidget.tsx               # the sidebar
  components/TodoItem.tsx                 # one row (checkbox, title, notes, edit/delete affordances)
  views/Chat.tsx                          # mount alongside NewsWidget
  tests/components/TodoWidget.test.tsx
  tests/components/TodoItem.test.tsx
```

Behavior:
- Stacks vertically with `NewsWidget` in the right sidebar (NewsWidget on top, TodoWidget below). Both collapsible independently. Persist collapsed state in `localStorage["kc.todos.collapsed"]`.
- On active conversation change, fetches `GET /todos?conversation_id=N` and renders the result. Empty conversation shows italic empty-state text.
- Real-time updates: subscribes to a new WS event `{type: "todo_event", action: "added"|"updated"|"deleted", item: {...}}` emitted by the supervisor on every mutation. The widget mutates its local state on each event; no need to poll.
- Each `TodoItem` renders:
  - Checkbox (toggles via `PATCH /todos/{id}` with `{status: "done" | "open"}`)
  - Title (clickable → opens inline edit popover with title + notes textarea)
  - Notes preview (first line, truncated, only if non-empty)
  - Pin icon (📌) if `scope === "agent"`
  - Delete affordance on hover (calls `DELETE /todos/{id}`)
- Items grouped: conversation-scoped on top (just listed), agent-scoped below under a "📌 Persistent" header. Optional — could just be a single list with pin markers. Going with grouped for clarity.

### ClarifyCard (inline in chat transcript)

New file:

```
kc-dashboard/src/components/ClarifyCard.tsx
kc-dashboard/tests/components/ClarifyCard.test.tsx
```

Plus a small change to `kc-dashboard/src/views/Chat.tsx` to mount it alongside the existing `ApprovalCard` map.

Behavior:
- Styling: amber-bordered card (parallel to `ApprovalCard`'s red border for approvals — different color so user can tell at a glance which is which).
- Layout: question text (bold) at top, choices as horizontal button row (wrapping if many), Skip button to the right, countdown text at the bottom (`⏱ 4:23 remaining`).
- Click a choice or Skip → fires the corresponding `clarify_response` WS frame. Card transitions to a resolved state showing the choice in a muted color (`✓ Tuesday`). Doesn't disappear — stays in the transcript as history.
- Countdown timer: client-side, decrementing once per second. When it hits 0, card transitions to `⏱ Timed out — Kona moved on`. The supervisor side already resolved the future; this is cosmetic only.
- Multiple pending clarifies in one conversation: in theory possible (Kona's tool layer is single-threaded so it shouldn't happen in practice, but the data model supports it). Renders them in order received. Each is independent.

### No new routes for clarify

The clarify card lives entirely inside the existing chat WebSocket connection. No dashboard navigation, no separate "Clarifications" tab. It's a transient interaction; once resolved, the audit table is the only record.

## Testing

### Supervisor tests

`kc-supervisor/tests/test_todos.py` (~14 cases):
- CRUD: add → list → complete → delete works.
- `persist=true` writes NULL conversation_id; `persist=false` writes the current conversation_id.
- `list` with `scope="all"` returns conv-scoped + agent-scoped UNION.
- `list` with `scope="conversation"` returns only conv-scoped.
- `list` with `scope="agent"` returns only agent-scoped.
- `list` with `status="done"` returns only completed items.
- `update` accepts title-only, notes-only, both.
- `update` with neither title nor notes → `missing_fields` error.
- `complete` is idempotent (calling on a done item returns success without changing updated_at).
- `delete` returns `not_found` for nonexistent id.
- `wrong_agent`: agent A's tool tries to operate on agent B's todo → error, no DB mutation.
- `wrong_conversation`: conv 40's tool tries to operate on conv 41's conv-scoped todo → error.
- `wrong_conversation` does NOT apply to agent-scoped todos — any of agent A's conversations can address agent A's persistent items.
- `clear_done` with scope="all", "conversation", "agent" — counts match expected.

`kc-supervisor/tests/test_clarify_broker.py` (~10 cases):
- `request_clarification` allocates a request_id, future is initially unresolved.
- Subscriber callback fires with the `clarify_request` frame when published.
- `resolve(request_id, choice="X")` completes the future with `{choice: "X", choice_index, elapsed_ms}`.
- `resolve(request_id, choice=None, reason="skipped")` completes with the skipped payload.
- Timeout: `request_clarification` with `timeout_seconds=0.1`, don't resolve, future resolves to timeout payload.
- `resolve` on a nonexistent request_id is a no-op (doesn't raise).
- `resolve` on an already-resolved future is a no-op (doesn't raise).
- `pending_for_conversation(N)` returns only pending requests for that conversation.
- After resolution, `pending_for_conversation` no longer returns the resolved request.
- Concurrent `request_clarification` calls each get a unique request_id.

`kc-supervisor/tests/test_clarify_tool.py` (~8 cases):
- Tool builder returns a Tool with name `clarify`.
- Tool's impl calls `broker.request_clarification` with the parsed args.
- Missing question → `missing_question` error.
- Missing choices → `missing_choices` error.
- 1 choice → `too_few_choices`.
- 9 choices → `too_many_choices`.
- Duplicates → `duplicate_choices` error with the duplicates listed.
- `timeout_seconds` clamped to `[10, 600]`.

`kc-supervisor/tests/test_assembly.py` — add 4 cases:
- `todo.*` tools register on Kona at Tier.SAFE.
- `clarify` tool registers on Kona at Tier.SAFE.
- Neither registers on Research-Agent (matches scheduling-tools precedent).
- Tools are present iff `Storage` has the migrated `todos` table (sanity check that migration ran).

`kc-supervisor/tests/test_http_todos.py` (~10 cases):
- GET /todos returns expected JSON shape.
- POST /todos creates a row.
- PATCH /todos/{id} updates title / notes / status independently.
- DELETE /todos/{id} returns 204; second DELETE returns 404.
- Bulk DELETE with `?scope=conversation&conversation_id=N&status=done` deletes correct rows, returns count.
- Validation: POST without title → 422.
- Validation: PATCH with `status=garbage` → 422.

### Dashboard tests

`kc-dashboard/tests/components/TodoWidget.test.tsx` (~5 cases):
- Renders empty state when no items.
- Renders items grouped conversation vs agent.
- Pin icon shown for agent-scoped.
- Checkbox click fires PATCH with toggled status.
- WS `todo_event` adds/removes items live.

`kc-dashboard/tests/components/TodoItem.test.tsx` (~4 cases):
- Click title opens edit popover.
- Save in popover fires PATCH.
- Delete affordance fires DELETE.
- Long notes truncate.

`kc-dashboard/tests/components/ClarifyCard.test.tsx` (~5 cases):
- Renders question + choices.
- Click a choice fires `clarify_response` WS message.
- Skip button fires response with `choice: null, reason: "skipped"`.
- Countdown decrements once per second.
- After timeout, card shows "Timed out — Kona moved on" and buttons are disabled.

### No external network in tests

All tests use mock storage (or in-memory SQLite for `test_todos.py`) and a fake WS broker. No live supervisor process, no live Firecrawl, no Google.

## Manual SMOKE Gates (post-merge)

Run after merging Phase C to `main`:

1. **Todo round-trip:** Ask Kona "start a todo list for my NYC trip" with a few items. Sidebar shows them in real-time. Tick a checkbox in the sidebar; Kona's next reply sees the completed state.
2. **Hybrid scope:** Ask Kona "add a persistent reminder to renew my passport." Use `persist=true`. Switch to a *different* conversation — pin icon visible there too. Sidebar shows under "📌 Persistent" header.
3. **clear_done:** mark several items done, ask Kona "clear out the completed items." She calls `todo.clear_done`. Sidebar updates.
4. **Clarify happy path:** Ask Kona "schedule dinner with mom." She should call `clarify` with 3-4 day options. Card appears inline with countdown. Click a choice → her tool result has the answer; she continues.
5. **Clarify skip:** new clarify. Click Skip. Tool returns `{"choice": null, "reason": "skipped"}` and Kona handles it gracefully (asks in free text, or moves on).
6. **Clarify timeout:** new clarify with `timeout_seconds=10` (override default). Wait. After 10s the card transitions to "Timed out — Kona moved on." Her tool result is the timeout payload.
7. **WS reconnect mid-clarify:** start a clarify with `timeout_seconds=120`. Hard-reload the dashboard tab (Cmd+Shift+R). After reconnect, the same card reappears with the countdown continuing.
8. **Audit:** check the `audit` table after running gates 4-6. One row per clarify with question, chosen answer, decision reason.
9. **Dashboard manipulation:** in the sidebar, click a todo title → edit popover → change the title and save. Kona's next `todo.list` call sees the updated title.

Document pass/fail in `docs/superpowers/specs/2026-05-1X-todo-clarify-SMOKE.md` matching the Phase A / Phase B precedent.

## Rollout

1. Ship the supervisor side (todos subpackage, clarify subpackage, HTTP routes, WS frames, assembly wiring) in one PR.
2. Ship the dashboard side (TodoWidget, ClarifyCard, api/todos.ts, WS handlers) in a second PR.
3. After both merge to `main`, restart supervisor. Run all 9 SMOKE gates.
4. No system-prompt update needed for Kona — tool descriptions are clear; she'll discover them naturally on calendar-planning / scheduling questions.
5. Document the tools in `kc-supervisor/README.md` and `kc-dashboard/README.md`.

## Decisions Locked

| Decision | Choice |
|---|---|
| Scope | One combined spec (todo + clarify) |
| Todo state lifetime | Hybrid: conversation-scoped default, `persist=true` lifts to agent-scope |
| Clarify interaction | Blocking with default 300s timeout (clamped [10, 600]) + skip button |
| Todo storage | New `todos` table in supervisor's SQLite (`~/KonaClaw/data/konaclaw.db`) |
| Clarify storage | In-memory `ClarifyBroker` (mirrors `ApprovalBroker`); audit row on resolution |
| Package shape | Subpackages inside kc-supervisor (parallel to `scheduling/`) |
| Todo operations | 6: add, list, complete, update, delete, clear_done (no reopen for v1) |
| Clarify surface | Single-select, plain string choices, 2-8 items, no free-text |
| Todo UI | Right sidebar in Chat, stacked under NewsWidget (per-conversation view with pin markers for agent-scoped) |
| Clarify UI | Inline card in chat transcript (mirrors `ApprovalCard` pattern, amber border) |
| Tool tier | `Tier.SAFE` for all 7 tools — no approval prompts |
| Enable flag | None — always on for Kona; conversation-internal primitives have no security surface |
| Cross-agent access | Forbidden: each row carries `agent`; cross-agent ops return `wrong_agent` error |
| Cross-conversation access | Forbidden for conv-scoped todos (returns `wrong_conversation`); allowed for agent-scoped |

## Out of Scope (future work)

- `todo.reopen` — add when re-opening becomes a real workflow need.
- Drag-to-reorder in the sidebar — `created_at` ordering is sufficient for v1.
- Multi-select clarify — ask twice if you need it.
- Structured `{value, label}` clarify choices — revisit if a real use case emerges (e.g., calendar event picker).
- Cross-agent todo visibility — agent-scoped means "this agent only."
- "Snooze" / "due_date" on todos — reminders exist for that.
- Tab-level "Todos" view (cross-conversation) — sidebar suffices for v1.
- Notification when a clarify is pending and the dashboard is in another tab — browser notification API.
- "Bulk add" tool to drop in 5+ items at once — not used in observed workflows.

**Note on audit:** every tool call goes through `AuditingToolRegistry`, so todo and clarify calls both write standard audit rows automatically. The spec's "Audit" section for clarify describes the *user-visible* meaning of those rows (which question was asked, which answer was chosen) — it doesn't introduce a separate audit path. Todo audit rows are the same shape as any other tool call; the spec doesn't need to call them out specially.
