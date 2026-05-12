# KonaClaw Subagents — Design Spec

**Date:** 2026-05-11
**Status:** Draft — pending Sammy's review before plan generation
**Scope:** Add ephemeral subagent spawning to KonaClaw alongside the existing persistent-sibling pattern.

## 1. Goal

Let Kona-AI delegate work to short-lived ephemeral subagents whose tool, model, and permission surface is defined by user-authored templates. Persistent siblings (today's static YAML agents like `Research-Agent`) continue to exist unchanged; this design adds a parallel ephemeral track for one-shot missions.

## 2. Background

KonaClaw already supports a static notion of "subagent": YAML files in `agents_dir`, loaded by `AgentRegistry`, with their own model + tool surface, and reachable from Kona-AI via the existing `delegate_to_agent` tool (loop guard, `depth_limit=1`, fresh history per delegation). What's missing:

- **Dynamic spawning.** Kona cannot today instantiate a one-off worker. Every agent must be pre-declared as a YAML file in `agents_dir` and lives forever in the registry.
- **Parallel fan-out.** `delegate_to_agent` is synchronous; Kona must wait for one delegation to return before starting the next.
- **Per-instance lifecycle.** No timeout, no stop button, no per-instance metadata. A delegation either returns or blocks the parent.

This spec adds an ephemeral track that fills those gaps while reusing the assembly/audit/approval machinery that already works.

## 3. Decisions locked during brainstorming

1. **Scope:** ship both persistent siblings (already done) and ephemeral templates (new). The new work is ephemeral.
2. **Authoring:** YAML files on disk are the source of truth, with a dashboard UI authoring on top. Full template surface (model + prompt + tools + MCP + memory + shares + permission overrides) ships in Phase 1.
3. **Visibility:** subagent runs render as inline collapsible trace blocks in the parent's chat transcript. Approval prompts originate from the subagent and are attributed to it in the card.
4. **Concurrency:** parallel spawning allowed; nesting disallowed (subagents cannot themselves spawn). Caps: 4 in-flight instances per parent conversation, 16 across the whole supervisor.
5. **Timeout + stop:** every template carries a default timeout; spawn calls may tighten (not widen) it. Inline trace block exposes a Stop button that cancels the asyncio task.
6. **Context:** fresh history every spawn. Optional `context` payload accepted by the spawn call and serialized into the subagent's first system message. No state across spawns; no subagent writes to Kona's memory.
7. **API surface:** two distinct tools (`spawn_subagent`, `await_subagents`) on Kona only. Today's `delegate_to_agent` stays unchanged for persistent siblings.
8. **Architecture:** new `EphemeralInstance` wrapper around a freshly-`assemble_agent()`'d real `AssembledAgent` (hybrid approach 3). The wrapper owns timeout, stop handle, parent attribution, and trace WS routing; everything else reuses existing infra.
9. **Rollout:** `KC_SUBAGENTS_ENABLED` env flag, default `false`. Standard KonaClaw rollout pattern.
10. **Starter templates:** `web-researcher`, `coder`, `email-drafter`, `scheduler` ship as seed YAML files.

## 4. Architecture

### 4.1 New package: `kc-subagents`

Lives as a sibling of `kc-skills`, `kc-terminal`, `kc-web` at the monorepo root.

```
kc-subagents/
  pyproject.toml
  src/kc_subagents/
    __init__.py
    templates.py          # SubagentTemplate dataclass + YAML loader + SubagentIndex
    runner.py             # EphemeralInstance lifecycle + Runner registry
    trace.py              # WS frame shapes + per-conversation pub-sub buffer
    tools.py              # build_subagent_tools(): spawn_subagent + await_subagents
  tests/
    test_templates.py
    test_runner.py
    test_trace.py
    test_tools.py
```

Editable-installed into `kc-supervisor`'s venv, matching the convention used for `kc-skills`, `kc-terminal`, `kc-web`.

### 4.2 Supervisor wiring

- `kc_supervisor.main` builds a `SubagentIndex` (pointing at `~/KonaClaw/subagent-templates/`) and a `SubagentRunner` at startup when `KC_SUBAGENTS_ENABLED=true`.
- `AgentRegistry.__init__` accepts `subagent_index` and `subagent_runner` kwargs (nullable). Threaded into `assemble_agent` like `skill_index` and `web_config` already are.
- `assemble_agent` registers `spawn_subagent` and `await_subagents` only when `cfg.name in ("kona", "Kona-AI")` AND both deps are non-None. Same gate shape as todos/clarify/scheduling.

### 4.3 Storage layout

- Templates live at `~/KonaClaw/subagent-templates/<name>.yaml` — outside the repo, matching how `~/KonaClaw/skills/` and `~/KonaClaw/agents/` already live.
- No new tables for templates themselves; YAML files are the source of truth. The `SubagentIndex` keeps an in-memory cache with mtime invalidation, mirroring `SkillIndex`.
- Audit additions live in the existing `kc-sandbox` audit DB — see §10.

## 5. Template schema

Authoritative reference. Every field except `name`, `model`, and `system_prompt` is optional.

```yaml
# ── identity ────────────────────────────────────────────
name: web-researcher              # required; lowercase-kebab, ≤64 chars; must match filename stem
description: >                    # optional but recommended; ≤1024 chars
  Researches a question end-to-end and returns a structured summary
  with sources.
version: "1.0"                    # optional free-form

# ── model ───────────────────────────────────────────────
model: claude-opus-4-7            # required; any model name resolvable by the supervisor's runtime
model_options:                    # optional passthrough (temperature, etc.)
  temperature: 0.3

# ── prompting ───────────────────────────────────────────
system_prompt: |                  # required
  You are a focused research agent. Use web_search to find sources,
  web_fetch to read them, and return a structured Markdown summary
  with inline source URLs. Do not chitchat.

# ── tools ───────────────────────────────────────────────
tools:                            # optional; dict-of-dicts; if omitted → empty toolset (text-only)
  web_search:
    budget: 20                    # tighter than the global per-session cap
  web_fetch:
    max_response_bytes: 16384     # tighter than the global 32KB cap
  skill_view: {}                  # empty dict = whitelisted with global defaults

# ── MCP servers (optional) ──────────────────────────────
mcp_servers:                      # list of MCP server names from the supervisor's registry
  - zapier
mcp_action_filter:                # optional whitelist per server
  zapier:
    - gmail_find_email
    - gmail_create_draft

# ── memory (optional) ───────────────────────────────────
memory:
  mode: read-only                 # Phase 1: one of: none | read-only  (default: none)
                                  # The "read-write" mode is reserved for a later phase — see §15.
  scope: "research/"              # optional subpath under memory_root; default: full root

# ── sandbox shares (optional) ───────────────────────────
shares:                           # list of share names from kc-sandbox SharesRegistry
  - downloads-readable
  - scratch-readwrite

# ── permission overrides (optional) ─────────────────────
permission_overrides:             # per-tool tier override scoped to this template only
  terminal_run: MUTATING
  web_fetch: SAFE

# ── lifecycle ───────────────────────────────────────────
timeout_seconds: 300              # default 300; clamped [10, 1800]
max_tool_calls: 50                # safety cap; default 50; clamped [1, 500]
```

**Validation:** schema is enforced at load time. Unknown keys are rejected. Unknown tool names (not present in the global tool registry) are rejected. Unknown MCP server names are rejected. Memory `scope` paths are validated to stay under `memory_root`.

**Degradation:** a malformed template loads into the index with `status=DEGRADED` and `last_error=...`, mirroring how `agents.py` handles bad agent YAML today. Degraded templates surface in the dashboard tab with the parse error inline; `spawn_subagent` against them returns `"error: template <name> is degraded: <error>"`.

**Translation to AgentConfig:** a pure function `template_to_agent_config(template, *, instance_id, parent_agent)` produces the `AgentConfig` that `assemble_agent` already consumes. Synthetic name `"<parent_agent>/<instance_id>/<template_name>"` so audit rows are self-documenting.

## 6. Spawn API

Two tools, registered only on Kona-AI.

### 6.1 `spawn_subagent`

Tier: **SAFE**. The act of spawning is itself safe; the subagent's own tool calls go through their own tier checks.

```jsonc
// parameters
{
  "type": "object",
  "properties": {
    "template":         { "type": "string",  "description": "Template name from the subagent index." },
    "task":             { "type": "string",  "description": "The prompt the subagent receives as its user message." },
    "context":          { "type": "object",  "description": "Optional JSON payload; serialized into the subagent's first system message." },
    "label":            { "type": "string",  "description": "Optional human-readable tag for the inline trace block (≤64 chars)." },
    "timeout_seconds":  { "type": "integer", "description": "Override the template default; cannot exceed it. Clamped [10, template.timeout_seconds]." }
  },
  "required": ["template", "task"]
}

// returns
{
  "subagent_id": "ep_a7b3c9",
  "status":      "running",
  "template":    "web-researcher",
  "label":       "berlin-weather"
}
```

Errors returned as the tool-result string (parent's loop stays alive):

- `error: unknown template <name>`
- `error: template <name> is degraded: <reason>`
- `error: too many in-flight subagents on this conversation (4/4); await some before spawning more`
- `error: supervisor in-flight subagent cap reached (16/16); retry shortly`
- `error: timeout_seconds <n> exceeds template max (<n>)`
- `error: subagents not enabled` (when `KC_SUBAGENTS_ENABLED=false`; surfaced only if the tool is somehow callable — gating means it won't be registered in that case)

### 6.2 `await_subagents`

Tier: **SAFE**.

```jsonc
// parameters
{
  "type": "object",
  "properties": {
    "subagent_ids":    { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 8 },
    "timeout_seconds": { "type": "integer", "description": "Ceiling on the wait itself. Clamped [10, 1800]." }
  },
  "required": ["subagent_ids"]
}

// returns: array preserving input order
[
  { "subagent_id": "ep_a7b3c9", "status": "ok",      "reply": "...markdown...",       "duration_ms": 18430, "tool_calls_used": 7 },
  { "subagent_id": "ep_d2e8f1", "status": "error",   "error": "...",                  "duration_ms": 12100 },
  { "subagent_id": "ep_f4g5h6", "status": "timeout", "error": "timed out after 300s", "duration_ms": 300000 },
  { "subagent_id": "ep_x9y0z1", "status": "stopped", "error": "stopped by user",      "duration_ms":  7200 }
]
```

Unknown handles are reported as `{"subagent_id": "...", "status": "error", "error": "unknown subagent_id"}` in the response array rather than failing the whole call.

**Single-spawn ergonomics:** the common case of one spawn + one wait stays clean as two consecutive tool calls. No convenience wrapper.

## 7. Execution model

### 7.1 EphemeralInstance lifecycle

1. **Spawn.** `runner.spawn(template, task, context, parent_cid, parent_agent, label, timeout_override)`:
   - Generate `subagent_id` = `"ep_" + 6-char-base32(random)`. Collisions rejected via in-flight registry.
   - Resolve effective timeout: `min(timeout_override or template.timeout_seconds, template.timeout_seconds)`.
   - Build `AgentConfig` via `template_to_agent_config`.
   - Call `assemble_agent(...)` with the same kwargs `AgentRegistry` uses, plus the per-instance permission override wrapper around `PermissionEngine`.
   - Insert `subagent_runs` row with `status=running`.
   - Emit `subagent_started` WS frame to the parent conversation.
   - Schedule `asyncio.create_task(self._run())` and return the handle immediately.

2. **Run.** `_run()`:
   - Compose first message: `task` plus, if `context` provided, a structured block (`"## Context\n```json\n{context}\n```"`).
   - Run `asyncio.wait_for(self.assembled.core_agent.send(message), timeout=effective_timeout)`.
   - Each tool call inside the subagent triggers a `subagent_tool` WS frame (instrumented via the existing audit hook — added attribution fields rather than a new pipeline).
   - On `max_tool_calls` overrun, inject a synthetic tool-result `"error: max_tool_calls cap reached"` and let the model produce a final assistant turn.
   - Capture the assistant's final text content as `reply`.

3. **Terminal state.** Exactly one of:
   - `ok` — `core_agent.send` returned cleanly. `reply` = assistant text.
   - `error` — runner caught a non-cancellation exception. `error` = exception message.
   - `timeout` — `asyncio.wait_for` raised `TimeoutError`. `error` = `"timed out after Ns"`.
   - `stopped` — runner saw a stop signal and cancelled the task. `error` = `"stopped by user"`.

   In all four cases: update `subagent_runs` row with end timestamp + status + duration_ms + tool_calls_used; emit `subagent_finished` WS frame; resolve the result future for any `await_subagents` waiters.

4. **Dispose.** Drop the `AssembledAgent` reference, remove from the in-flight registry. Audit rows persist; in-memory state is gone.

### 7.2 Concurrency

- Per-conversation cap of **4** in-flight instances. 5th `spawn_subagent` returns the cap error.
- Per-supervisor cap of **16** in-flight instances across all conversations. Same cap-error shape.
- `await_subagents([h1, h2, h3])` is `asyncio.gather` over the handles' result futures, with `return_exceptions=True` so one failure doesn't poison the others.
- Subagents themselves do NOT get `spawn_subagent` or `delegate_to_agent` registered. Hardcoded in `assemble_agent`: ephemeral instances (detected via `cfg.name` starting with `<parent>/ep_`) skip the delegation/spawn tool registration.

### 7.3 Stop

- Dashboard sends `{"type": "subagent_stop", "subagent_id": "..."}` over the parent conversation's WS.
- `ws_routes` forwards to `runner.stop(subagent_id)`.
- `runner.stop` calls `task.cancel()` on the instance's asyncio task. Any in-flight tool call (e.g. `web_fetch` mid-request) gets cancelled via standard asyncio propagation.
- Instance enters terminal state `stopped`, emits `subagent_finished`, resolves the result future.

### 7.4 Restart resilience

- On supervisor restart, in-flight instances are gone (process state). Any `subagent_runs` rows still marked `running` at startup are reaped: status updated to `interrupted`, end timestamp set to "now", error set to `"supervisor restarted mid-run"`. Mirrors how the existing approval/clarify subsystems treat in-flight items across restart.

## 8. Inline trace (WS protocol)

Frames flow over the **parent's** WS channel (`/ws/chat/{conversation_id}`). The dashboard transcript renders them as a collapsible "▾ subagent: <template> (<label>)" block.

### 8.1 Frame types

```jsonc
{ "type": "subagent_started",
  "subagent_id": "ep_a7b3c9",
  "template": "web-researcher",
  "label": "berlin-weather",
  "task_preview": "research current weather…",
  "ts": "2026-05-11T18:23:04Z" }

{ "type": "subagent_tool",
  "subagent_id": "ep_a7b3c9",
  "tool": "web_search",
  "args_preview": "{\"query\":\"berlin weather today\"}",
  "result_preview": "5 results, top: weather.com/…",
  "tier": "SAFE",
  "ts": "..." }

{ "type": "subagent_approval",
  "subagent_id": "ep_a7b3c9",
  "approval_id": "appr_xyz",
  "tool": "terminal_run",
  "args_preview": "{\"argv\":[\"ls\",\"-la\"]}",
  "attributed_to": "web-researcher (ep_a7b3c9, child of Kona-AI)",
  "ts": "..." }

{ "type": "subagent_finished",
  "subagent_id": "ep_a7b3c9",
  "status": "ok",        // | "error" | "timeout" | "stopped" | "interrupted"
  "reply_preview": "first 400 chars of the assistant reply…",
  "duration_ms": 18430,
  "tool_calls_used": 7,
  "error_message": null, // populated on non-ok statuses
  "ts": "..." }
```

`subagent_approval` frames are emitted in **addition to** the existing `approval_request` frame the `ApprovalBroker` already sends. The new frame carries attribution metadata; the existing approval card UI gets a "via subagent" badge when its `request_id` matches a `subagent_approval` it has seen.

### 8.2 Reconnect replay

The runner keeps a per-conversation buffer of frames emitted by currently in-flight instances (`Dict[parent_cid, List[Frame]]`). On WS reconnect, the dashboard catches up by replaying buffered frames before live ones resume. Buffer entries are evicted when their instance reaches a terminal state.

This mirrors `pending_for_conversation` for clarify cards.

### 8.3 UI rendering

- **Card header:** `▾ subagent: web-researcher · berlin-weather · running (0:12)` with a **⏹ Stop** button visible while `running`.
- **Expanded body:** chronological list of tool-call rows + any approval cards inline. Final reply rendered as a child message bubble inside the card on `subagent_finished`.
- **Collapsed state after finish:** one-line summary `web-researcher · berlin-weather · ✓ ok · 7 tools · 18.4s`. Status icons: ✓ ok, ⚠ error, ⏱ timeout, ⏹ stopped, ⚡ interrupted.
- **Persistence:** card stays in the transcript after finish; doesn't vanish (matches clarify card behavior).

## 9. Approval attribution

Approval cards for subagent tool calls reuse the existing `ApprovalBroker` end-to-end. Two specific changes:

1. **Card metadata.** Two new fields added to the broker's `Request`/`Response` dataclasses: `parent_agent: Optional[str]`, `subagent_id: Optional[str]`. Populated only when the request originates from inside an `EphemeralInstance`. The runner threads them through via a contextvar (`_subagent_attribution`) that wraps `core_agent.send` for the instance, same way `_delegation_chain` works today.
2. **Card display.** The dashboard `ApprovalCard` component reads the two new fields. When present, the agent label becomes `"<template> (<subagent_id>, child of <parent_agent>)"` and a small "via subagent" badge appears. Cards still route to the parent conversation's WS channel — no new queue, no separate routing path.

**Per-template permission overrides** (the `permission_overrides:` block) are realized as a thin per-instance wrapper around the global `PermissionEngine`. The wrapper consults the template's override map first, falls back to the global engine if no override is specified. Wrapper is constructed at `EphemeralInstance` build time and lives only for that instance.

**Default tiers** for the seed templates (see §11) are chosen so the four ship cases require approvals on the right operations: `coder` keeps `terminal_run` at `MUTATING`, `email-drafter` keeps Zapier `gmail_create_draft` at `MUTATING`, etc.

## 10. Audit & lifecycle

### 10.1 Schema additions

The existing `tool_calls` table in the `kc-sandbox` audit DB gains three nullable columns:

```sql
ALTER TABLE tool_calls ADD COLUMN parent_agent       TEXT;
ALTER TABLE tool_calls ADD COLUMN subagent_id        TEXT;
ALTER TABLE tool_calls ADD COLUMN subagent_template  TEXT;
```

NULL for static agents and direct Kona calls. Populated only for tool calls made inside an `EphemeralInstance`.

A new `subagent_runs` table tracks instance-level lifecycle:

```sql
CREATE TABLE subagent_runs (
  id                       TEXT PRIMARY KEY,             -- "ep_a7b3c9"
  parent_conversation_id   TEXT NOT NULL,
  parent_agent             TEXT NOT NULL,
  template                 TEXT NOT NULL,
  label                    TEXT,
  task_preview             TEXT,                          -- first 200 chars
  context_keys             TEXT,                          -- JSON array of top-level keys if context supplied
  started_ts               TIMESTAMP NOT NULL,
  ended_ts                 TIMESTAMP,
  status                   TEXT NOT NULL,                 -- running|ok|error|timeout|stopped|interrupted
  duration_ms              INTEGER,
  tool_calls_used          INTEGER NOT NULL DEFAULT 0,
  reply_chars              INTEGER,                       -- length of final assistant reply
  error_message            TEXT
);

CREATE INDEX idx_subagent_runs_parent ON subagent_runs(parent_conversation_id, started_ts DESC);
CREATE INDEX idx_subagent_runs_template ON subagent_runs(template, started_ts DESC);
```

Lets you query "all `terminal_run` calls made by `coder` subagents this week" or "average duration of `web-researcher` spawns."

### 10.2 No persistent state across spawns

Each `EphemeralInstance` builds a fresh `AssembledAgent` with empty history. The `context` payload, if supplied, is serialized into the first system message — it is the only channel for cross-spawn information flow, and it is controlled by Kona's reasoning at spawn time, not by an implicit session.

Subagents do **not** write to KonaClaw's conversation history. Their transcript lives only in the inline trace block in the parent's chat + the `subagent_runs` row + the per-call audit rows. On instance disposal, all in-memory state is dropped.

### 10.3 Memory writes

Phase 1: subagents may **not** write to memory. The `memory.mode` enum is restricted to `none | read-only` in Phase 1; the `read-write` value is rejected at template load time with `"error: memory.read-write is not yet supported; see spec §15"`. Write access is deferred until a later phase that designs the audit + attribution story for memory mutations.

(Open follow-up: when write access lands, memory rows should carry the subagent attribution columns same as `tool_calls`.)

## 11. Seed templates

Four ship under `~/KonaClaw/subagent-templates/` on first run. Installed by the supervisor on startup if the directory is empty; not overwritten on subsequent runs (user-edited files win).

### 11.1 `web-researcher.yaml`

```yaml
name: web-researcher
description: Research a question end-to-end and return a structured summary with sources.
model: claude-opus-4-7
system_prompt: |
  You are a focused research agent. Use web_search to find sources,
  web_fetch to read promising results, and return a structured Markdown
  summary with inline source URLs. Do not chitchat; lead with findings.
tools:
  web_search:
    budget: 20
  web_fetch:
    max_response_bytes: 16384
  skill_view: {}
timeout_seconds: 300
max_tool_calls: 30
```

### 11.2 `coder.yaml`

```yaml
name: coder
description: Execute a focused coding task in a specified cwd; return a summary of what changed.
model: claude-opus-4-7
system_prompt: |
  You are a focused coding agent. The user provides a cwd and a task.
  Use terminal_run to execute commands. Make minimal, focused changes.
  Return a one-paragraph summary of what you did and any followups.
tools:
  terminal_run: {}
  skill_view: {}
permission_overrides:
  terminal_run: MUTATING   # explicit — keeps the global default; subagent approvals are attributed
timeout_seconds: 600
max_tool_calls: 100
```

### 11.3 `email-drafter.yaml`

```yaml
name: email-drafter
description: Draft a Gmail reply matching the user's tone. Returns draft text only — never sends.
model: claude-haiku-4-5-20251001
system_prompt: |
  You draft email replies. Find recent context with Zapier Gmail read actions,
  then produce a draft that matches the user's tone. Always return the draft
  text as your final reply. Do not send.
tools:
  skill_view: {}
mcp_servers:
  - zapier
mcp_action_filter:
  zapier:
    - gmail_find_email
    - gmail_get_thread
    - gmail_create_draft
timeout_seconds: 240
max_tool_calls: 20
```

### 11.4 `scheduler.yaml`

```yaml
name: scheduler
description: Schedule events on the user's Google Calendar. Asks for clarification when ambiguous.
model: claude-haiku-4-5-20251001
system_prompt: |
  You schedule events on the user's Google Calendar. Use clarify to ask
  about ambiguous times, durations, or attendees. Use gcal tools to find
  free slots and create the event.
tools:
  clarify: {}
  skill_view: {}
mcp_servers:
  - zapier
mcp_action_filter:
  zapier:
    - google_calendar_find_event
    - google_calendar_quick_add_event
    - google_calendar_create_detailed_event
timeout_seconds: 300
max_tool_calls: 30
```

## 12. Dashboard UI

New tab **09 "Subagents"** in the dashboard sidebar.

### 12.1 Templates view (default)

- Card grid of templates from `~/KonaClaw/subagent-templates/`.
- Per-card display: name, model badge, tool count, MCP server badges (if any), short description, edit / duplicate / delete actions.
- **+ New template** opens a modal with the full schema as a typed form:
  - Identity: name (validated), description, version.
  - Model: dropdown sourced from the supervisor's known-model list; model_options as a JSON field.
  - System prompt: large textarea.
  - Tools: multiselect from the global tool registry. Selected tools expose their per-tool config fields (e.g. `web_search.budget`, `web_fetch.max_response_bytes`) inline.
  - MCP servers: multiselect from the supervisor's MCP server registry. Selected servers expose an action-filter multiselect populated from `list_enabled_zapier_actions`-style introspection.
  - Memory: mode dropdown + optional scope field.
  - Shares: multiselect from `SharesRegistry`.
  - Permission overrides: per-tool tier dropdown for whitelisted tools.
  - Lifecycle: `timeout_seconds` and `max_tool_calls` numeric inputs with the clamps shown.
- **Save** writes YAML to `~/KonaClaw/subagent-templates/<name>.yaml`, fires `POST /subagent-templates` which calls `subagent_index.reload()`, then emits a `templates_changed` WS frame so all dashboard clients refresh.
- Degraded templates show as a card with an amber border and the parse error inline, mirroring degraded-agent surfacing.

### 12.2 Active runs panel (toggle)

- Live list of currently-running `EphemeralInstance`s across all parent conversations.
- Per-row: `subagent_id`, template, parent conversation title (clickable to jump to the chat), elapsed time, tool calls used so far, **Stop** button.
- Updates live via the same `subagent_started`/`subagent_finished` WS frames the chat transcript consumes.
- Useful for seeing what's happening when Kona has fanned out multiple spawns and a transcript is busy.

### 12.3 Chat transcript

Existing chat transcript picks up the new WS frame types and renders the inline trace block described in §8. No new tab needed for trace.

### 12.4 HTTP routes (dashboard-server)

```
GET    /subagent-templates              → list (name, description, model, tool_count, mcp_count, status, last_error)
GET    /subagent-templates/{name}       → full YAML body + parsed view
POST   /subagent-templates              → create (body: full YAML); returns the parsed shape
PATCH  /subagent-templates/{name}       → update (body: full YAML); returns the parsed shape
DELETE /subagent-templates/{name}       → remove the YAML file from disk

GET    /subagents/active                → list of in-flight instances across all parent conversations
POST   /subagents/{subagent_id}/stop    → request stop (acts as if user clicked the trace block's Stop)
```

All routes are read-allowed; write routes require the same auth as existing dashboard write routes.

## 13. Rollout

- `KC_SUBAGENTS_ENABLED` env flag, default `false`. Sammy flips after SMOKE.
- When disabled: `SubagentIndex` and `SubagentRunner` are NOT constructed in `main.py`; `spawn_subagent` and `await_subagents` are NOT registered on Kona; dashboard tab 09 is hidden.
- When enabled: full feature set is live. No partial states.
- Seed templates are installed on first startup with the flag enabled and an empty `~/KonaClaw/subagent-templates/` directory.

## 14. Testing strategy

### 14.1 `kc-subagents` unit tests

- **Templates:** valid YAML loads, schema rejection messages for every malformed field (bad name, unknown tool, unknown MCP server, bad memory scope, etc.), mtime invalidation triggers reload, degraded templates surface with `last_error`.
- **`template_to_agent_config`:** every field maps correctly, defaults applied, synthetic name format is `<parent>/<instance_id>/<template>`.
- **`EphemeralInstance`:** ok / error / timeout / stopped paths exercised; result future resolution; trace frame emission order; `max_tool_calls` cap injects the synthetic tool-result and finalizes.
- **`spawn_subagent` tool:** bad template name → error string; in-flight cap (per-conversation 4, per-supervisor 16) → error strings; timeout_override exceeding template max → error string; happy path returns the handle.
- **`await_subagents` tool:** input-order preservation; unknown handles reported as error rows; partial-failure (one ok, one error, one timeout) returns all three; ceiling timeout cuts the wait but doesn't kill the running instances.
- **Trace buffer:** per-conversation reconnect replay returns only in-flight frames; evicted on instance terminal state.

### 14.2 `kc-supervisor` integration tests

- Spawning a template that uses `terminal_run` produces an approval card with `parent_agent="Kona-AI"`, `subagent_id="ep_..."` populated.
- Per-template `permission_overrides` apply to that instance and no others (run two parallel spawns of templates with differing overrides on the same tool; confirm each sees its own override).
- Parallel spawn + join: 3 templates in flight, all complete, `await_subagents` aggregates in input order.
- Stop button cancels mid-tool-call and emits the right WS frame sequence (`subagent_finished` with `status=stopped`; the cancelled tool call's audit row reflects cancellation).
- Audit rows correctly tagged with `parent_agent`, `subagent_id`, `subagent_template`.
- `Kona-AI` gets `spawn_subagent` + `await_subagents`; `Research-Agent` does not; ephemeral instances themselves do not.
- Restart with an in-flight `subagent_runs` row reaps it to `status=interrupted`.

### 14.3 Dashboard tests

- Templates tab renders, creates, edits, deletes templates (round-trip to disk + reload).
- Per-tool config fields render correctly when their tool is selected.
- Active runs panel shows live instances and Stop button works end-to-end.
- Chat transcript renders the new WS frame types as inline trace blocks; expanded/collapsed states behave; status icons correct.
- Reconnect replay: kill WS mid-run, reconnect, trace block resumes from buffered frames.

### 14.4 SMOKE gates (manual, post-merge, with `KC_SUBAGENTS_ENABLED=true`)

1. Author a new template in the dashboard UI; YAML appears on disk; `subagent_index` reloads; new template is visible to Kona via tool schema.
2. Kona spawns the new template via natural-language ask; inline trace block renders with correct label.
3. Subagent makes a tool call needing approval; card shows attribution as `"<template> (ep_..., child of Kona-AI)"`.
4. Two parallel spawns + `await_subagents` returns both in input order; durations and tool counts populate in the `subagent_runs` table.
5. Stop button cancels a long-running spawn; in-flight tool call is cancelled; `subagent_runs` row reflects `status=stopped`.
6. Timeout fires when subagent exceeds template limit; `subagent_runs` row reflects `status=timeout`; reply absent.
7. `max_tool_calls` cap fires and finalizes cleanly with a non-empty reply that acknowledges the cap.
8. Each seed template runs end-to-end on a representative task:
   - `web-researcher`: "what's the weather in Berlin today?"
   - `coder`: "in `/tmp/foo`, create a small bash script that prints the date." (gated approval prompts attributed)
   - `email-drafter`: "draft a reply to my most recent email from mom." (Zapier Gmail enabled)
   - `scheduler`: "schedule a 30-minute walk with mom tomorrow afternoon." (clarify exercised)
9. Restart supervisor while a subagent is mid-run; on restart, the row is reaped to `interrupted`; no zombie state.

## 15. Open follow-ups (not blocking Phase 1)

- **Subagent memory writes.** Currently disallowed; `memory.read-write` mode is half-implemented. Future phase needs an audit/attribution story for memory mutations.
- **Cross-spawn sessions.** Brainstorming considered + rejected (option C in Q5). Reconsider if Kona accumulates real use cases for multi-turn ephemeral workers.
- **One level of nesting.** Currently subagents cannot spawn. Lift to depth 2 if Kona's actual usage shows fan-out patterns blocked by this.
- **Per-template budget sharing.** Today `web_search.budget` in a template is a tightened cap per session — but the session is the parent conversation, not the spawn instance. Consider per-instance budget scoping if templates start abusing the parent's budget pool.
- **Subagent observability.** A "subagents history" dashboard view on top of `subagent_runs` would be useful once enough runs accumulate. Out of scope for Phase 1; defer until volume justifies.
- **Template versioning + rollback.** Today edits clobber the file. If templates become load-bearing, a git-backed history view in the dashboard might be worth adding.

## 16. Non-goals

- Letting subagents spawn further subagents (nesting). Hard-coded off in Phase 1.
- Persisting subagent conversation history into KonaClaw's main conversation list.
- Cross-spawn state sharing via `session_id` or similar.
- Subagent-initiated writes to KonaClaw memory.
- A separate chat surface (sidebar conversation) per ephemeral instance. Trace stays inline in the parent transcript.

---

**Next step:** Sammy reviews this spec, then we hand off to `superpowers:writing-plans` to produce the implementation plan.
