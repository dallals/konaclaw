# KonaClaw — Umbrella Design Spec

**Status:** Draft, pre-implementation. Awaiting hardware (MacBook Pro M5 Max, 128 GB RAM) before build begins.
**Date:** 2026-05-02
**Owner:** Sammy Dallal
**Scope:** This is the *umbrella* spec covering the whole system architecture and the sub-project decomposition. Each sub-project listed below will get its own focused design spec + implementation plan when its turn comes up — they are explicitly out of scope for this document.

---

## 1. What KonaClaw Is

KonaClaw is a local-first agent platform — a privately-hosted alternative to a Claude-Code-style agent runtime, designed to run entirely on the user's Mac using local LLMs served by Ollama. It exposes a localhost dashboard for chatting with a primary agent ("KonaClaw") and configuring/monitoring named subagents. It can be reached from outside the dashboard via iMessage and Telegram, and it can act on Gmail and Google Calendar through OAuth. It can dynamically connect to additional MCP servers and Zapier integrations on request, gated by a permission system.

The defining constraint: **all reasoning happens locally on the user's machine, and the system has no implicit access to the user's filesystem outside explicitly-allowlisted "shares."**

## 2. Goals & Non-Goals

**Goals**
- Run an agent runtime locally backed by Ollama-served models.
- Provide a dashboard (localhost web app) for chat, agent management, monitoring, and approvals.
- Support both persistent named subagents (chattable directly) and ephemeral task-delegation subagents.
- Reach KonaClaw from outside via iMessage and Telegram.
- Integrate with Gmail and Google Calendar via OAuth, with no Drive / file-pulling scopes.
- Allow agents to install new MCP servers and Zapier integrations on demand, gated by user approval.
- Strong sandbox: file/shell access scoped to user-allowlisted shares only; never any access to non-shared paths.
- Strong permission model: tier-based defaults with per-tool overrides; destructive actions (delete, send, OAuth grant, MCP install) require explicit approval.
- Undo: every reversible action (file ops + reversible external actions) can be undone via the dashboard.

**Non-Goals (v1)**
- Multi-user / multi-tenant. Single-user only.
- Cloud deployment. Local-only.
- Mobile native apps. Reach via Telegram/iMessage instead.
- Hosted-model providers (Anthropic/OpenAI). Hooks left for them via the OpenAI-compatible chat-completions interface, but not wired up.
- Google Drive, Google Contacts, Google Photos, or any local-file-pulling Google scope. Explicitly excluded.
- Full filesystem-snapshot rollback (e.g., APFS snapshots). Out of scope; share-level git journal is sufficient.

## 3. Decisions Locked In (from Brainstorming)

| Topic | Decision |
|---|---|
| Scope | Full plan, decomposed into 8 sub-projects |
| Tech stack | Python (supervisor / runtime / connectors) + React+TS (dashboard) |
| Subagent model | Both persistent specialists (chattable) and ephemeral task delegation; agents can dynamically install MCPs and Zapier integrations |
| Sandbox | Multiple named whitelisted "shares"; per-tool tier system with per-agent overrides |
| Undo | File ops + reversible external actions for v1; design hooks for full session rewind later |
| Models | OpenAI-compatible interface to Ollama; per-agent `model` config field with global default; default model in the Gemma 3 ~27–30B / Qwen 2.5–3 class |
| Process architecture | Two processes: supervisor (Python, FastAPI) + dashboard (React + tiny FastAPI). Subagents are async tasks within the supervisor. Designed so any subagent can later be promoted to its own subprocess without rewriting orchestration. |
| Connectors | iMessage via `chat.db` tail + AppleScript send; Telegram bot API; Gmail + Google Calendar via OAuth (minimal scopes). Each connector is a thin adapter — no filesystem access. |
| Memory | SQLite for conversations + per-agent `MEMORY.md` + global `user.md` |
| Network policy | Open-by-default, all outbound requests logged; allowlist + content-scanning are planned hooks for later |
| Storage root | `~/KonaClaw/` (visible). Easy to relocate if user later prefers `~/Library/Application Support/KonaClaw/` or `~/.konaclaw/`. |

## 4. Architecture Overview

```
┌─────────────────────────────────────────┐         ┌────────────────────┐
│ konaclaw-supervisor (Python, FastAPI)   │◄───────►│ konaclaw-dashboard │
│  - Agent runtime (asyncio)              │  HTTP + │ (React + Vite,     │
│  - Subagents as in-process async tasks  │  WebSkt │  served by its own │
│  - Connector adapters                   │         │  small FastAPI)    │
│  - Sandbox + permission engine          │         └────────────────────┘
│  - Undo journal (git-backed shares)     │
│  - SQLite (conversations, audit, undo)  │         ┌────────────────────┐
│  - Files: ~/KonaClaw/{shares,memory,db} │◄───────►│ Ollama (existing)  │
└─────────────────────────────────────────┘  HTTP   │ /v1/chat/completions
                                                    └────────────────────┘
```

Two processes, independent restart. Supervisor owns all state. Dashboard is a stateless client. Ollama is reused as-is.

## 5. Sub-Project Decomposition

Each sub-project below gets its own design spec and implementation plan when its turn comes up. They are listed in dependency order — each builds on the previous.

### 5.1 kc-core — Agent Runtime
Python library implementing the ReAct-style agent loop, the Ollama adapter (OpenAI-compatible), the message/tool schema, and the in-memory tool registry. Drivable from a CLI for verification.
**Done means:** chat with one agent against your local Ollama, no tools yet.

### 5.2 kc-sandbox — Shares, Permissions, Undo
Shares engine (named allowlisted folders, path resolution, traversal protection), permission tier engine (Safe / Mutating / Destructive with per-tool/per-agent overrides), git-backed write journal per share, undo API.
**Done means:** the kc-core agent can read/write/delete inside a share; writes are journaled; you can undo a delete.

### 5.3 kc-supervisor — Service & Persistence
FastAPI service exposing HTTP + WebSocket. Subagent lifecycle (spawn/list/kill), audit log, SQLite persistence (conversations, messages, audit, undo_log, mcp_installs), encrypted secrets store, health/metrics endpoints, restart-on-crash via launchd.
**Done means:** start the supervisor, hit its API to spawn an agent and chat with it from `curl`.

### 5.4 kc-dashboard — React Web App
Six views — Chat, Agents, Shares, Permissions, Monitor, Audit (see §9). Real-time chat via WebSocket. Permission-approval UI. Agent / share / permission management forms. Audit-log viewer with Undo buttons.
**Done means:** browser usable end-to-end — chat with KonaClaw, create a subagent from a form, see audit log, click Undo on a file delete. **First "real" usable system.**

### 5.5 kc-mcp — MCP Integration & Dynamic Install
Built on the official Python `mcp` SDK (stdio + HTTP transports). Static MCPs from config + dynamic install meta-tool with the destructive-tier approval flow. New MCP tools default to destructive tier until user-downgraded.
**Done means:** an agent installs an MCP server on user approval, uses its tools, you can disable/uninstall it from the dashboard.

### 5.6 kc-connectors — iMessage, Telegram, Gmail, Calendar
Four adapters implementing the connector contract (§7). Each is a plug-in, independently disable-able. iMessage and Telegram have explicit dashboard-only pairing (no inbound pairing approvals — that pattern is exactly what a prompt-injection attack would attempt). Gmail and Calendar use minimal OAuth scopes; **no Drive scope ever**.
**Done means:** text KonaClaw from your phone (Telegram first, then iMessage), see the convo in the dashboard, route a chat to a specific subagent, connect Gmail + Calendar via OAuth.

### 5.7 kc-zapier — Zapier Integration
Zapier already exposes its catalog as an MCP server, so this is mostly: pre-configure that one MCP + reuse the dynamic-install flow from §5.5 for individual zaps. Same destructive-tier-by-default rule.
**Done means:** an agent connects a zap on user approval and uses it.

### 5.8 kc-memory — Memory Layer
Global `~/KonaClaw/memory/user.md` + per-agent `~/KonaClaw/memory/<agent>/MEMORY.md`. Read at start of each agent turn. Writes go through kc-sandbox so they are journaled and undoable.
**Done means:** KonaClaw remembers you and your projects across conversations; per-agent memory works.

## 6. Agent Runtime (kc-core in depth)

**The loop** — every agent runs the same loop:

```
1. Load: system prompt + agent's MEMORY.md + global user.md + recent convo
2. Send context + available tools to Ollama (/v1/chat/completions)
3. Receive response — text or tool-call
4. If tool-call:
     a. Look up tool → check permission tier → maybe pause for user approval
     b. Execute (file op via sandbox, MCP call, web fetch, send msg, etc.)
     c. Append tool result to context
     d. Loop back to step 2
5. If text reply: stream to dashboard / connector, save turn to SQLite
```

**Agent definition** — YAML files under `~/KonaClaw/agents/`:

```yaml
name: EmailBot
model: qwen2.5:32b           # optional, falls back to global default
system_prompt: "You handle..."
shares: [Documents/Email]    # which shares this agent can touch
tools: [file.*, mcp.gmail.*, web.fetch]   # tool allowlist (glob patterns)
permission_overrides:
  mcp.gmail.send_to_self: auto-approve
spawn_policy: persistent     # or: ephemeral
```

The main agent ("KonaClaw") is just a built-in agent with broader tool access and a `spawn_subagent` tool.

**Subagent flavors** (same loop, different lifecycle):
- **Persistent** — long-lived, chattable from the dashboard, own conversation thread + memory.
- **Ephemeral** — main agent spawns one for a focused task, gets a structured summary back, the task agent is destroyed. Audit log preserves the work.

**Ollama adapter** — talks to Ollama's `/v1/chat/completions` (OpenAI-compatible), not the native API, so the runtime can later swap in another local runner (llama.cpp server, vLLM) or a hosted model without rewriting the loop. Streams tokens. Includes a fallback tool-call parser for models that emit JSON-in-text rather than native tool calls.

**Tool-call safety** — every tool call is logged before execution with: agent, tool, args, permission decision, result. This audit trail powers the dashboard's Monitor view *and* the undo system.

## 7. Sandbox, Permissions & Undo (kc-sandbox in depth)

### Shares
A "share" is a named, allowlisted folder. Config in `~/KonaClaw/config/shares.yaml`:

```yaml
shares:
  - name: research
    path: ~/Documents/Research
    mode: read-write
  - name: downloads
    path: ~/Downloads/KonaClaw
    mode: read-write
  - name: reference-docs
    path: ~/Documents/Manuals
    mode: read-only
```

Every file/shell tool takes `share` + a path *relative to that share*. The sandbox layer:
- Resolves `share + relpath` → absolute path. Rejects `../` traversal and any symlink that escapes the share root.
- Enforces `mode` — `read-only` shares reject writes/deletes regardless of permission tier.
- Enforces "no shell escape" — the shell tool is a constrained executor that runs commands with `cwd` pinned to a share, with a denylist for `sudo`, `rm -rf /`, and raw network binaries (`curl`, `wget`, `nc`, etc.). The denylist on raw network tools is not about blocking outbound network — outbound network is open by default (§3) — it's about forcing network use through the proper `web.fetch` / MCP / connector tools so it lands in the audit log instead of bypassing it via shell. **Hardening hook:** v2 wraps the shell tool in `bwrap` / `sandbox-exec` for OS-level isolation.

### Permission tiers
| Tier | Tools | Default behavior |
|------|-------|------------------|
| Safe | read file, list dir, grep, web fetch (GET) | Auto-approve, logged |
| Mutating | write/overwrite file, shell command, web POST | Auto-approve, logged, journaled for undo |
| Destructive | delete, rmdir, send message, create calendar event, install MCP, OAuth grant | **Pause for explicit approval each time** |

Per-tool overrides on the agent config can promote or demote a tool for that agent. New tools (e.g., from a freshly installed MCP) **default to destructive tier** until the user downgrades them.

### Approval UX
When a destructive action pauses, a notification fires in three channels at once: dashboard banner, optional Telegram message, optional iMessage. You approve from any of them. The agent loop is parked (not killed) waiting for the decision. Default timeout 24 h, then auto-deny.

### Undo (git-backed shares)
Every share is a hidden git repo (`.kc-journal/` inside the share root). The journal is invisible bookkeeping for the supervisor; the user does not interact with it directly.
- Every mutating file op = one commit by `konaclaw <agent-name>`.
- Every destructive op = one commit, plus a row in `undo_log` recording the reverse procedure.
- For external actions that *can* be reversed (calendar event, draft, MCP install), `undo_log` stores the API call needed to reverse it.
- For external actions that *cannot* be reversed (sent email, certain Zapier triggers), no undo row is created — those go through the destructive-tier gate up front and never silently happen.

**Undo API** — dashboard timeline of agent actions; each row has an Undo button when reversal is possible. Hitting undo:
1. File op → `git revert <commit>` inside the journal repo.
2. External op → executes the stored reverse procedure.
3. Chain undo (most recent N actions in reverse) — v1 supports single-action undo; chain undo is the v2 hook for full session rewind.

## 8. Connectors (kc-connectors, kc-mcp, kc-zapier in depth)

### Connector contract

```python
class Connector:
    name: str
    capabilities: set[str]                 # e.g., {"send", "react", "edit"}
    async def start(self, supervisor): ...
    async def stop(self): ...
    async def on_inbound(self, msg) -> None  # turn external input into agent input
    async def send(self, dest, content): ...
```

Connectors **never** see the filesystem, sandbox, or other agents' state. They only see message envelopes (sender id, content). Attachments arrive as bytes/URLs that the runtime writes into a designated inbox share before exposing them to any agent. Connectors load as plug-ins from `~/KonaClaw/connectors/*.py`.

### Routing
A small router in the supervisor:
1. Look up the chat/sender in the routing table (configured in dashboard).
2. If routed → deliver to that agent's inbox.
3. Otherwise → main agent (KonaClaw), which can reply directly or delegate.

Routing is per-chat, not per-connector — e.g., "this Telegram chat goes to ResearchBot, that iMessage thread goes to KonaClaw."

### iMessage adapter (macOS-only)
- **Inbound** — `chat.db` tailer polling `~/Library/Messages/chat.db`. Requires Full Disk Access granted in System Settings. Tailer reads new rows since last seen `ROWID`, scoped to allowlisted chats only — messages from other chats land in the DB but never reach the runtime.
- **Outbound** — AppleScript bridge via `osascript` driving the Messages app. Supports text, attachments (file paths), basic group threads.
- **Allowlist** — `~/KonaClaw/config/imessage.yaml` lists allowed handles + group chat IDs. Pairing happens **only** via the dashboard, never via inbound message — "approve this pairing" arriving inbound is exactly what a prompt-injection attack would say. Explicitly rejected by design.
- **Limitations** — no read receipts; group reactions best-effort; attachments arrive as paths the runtime copies into an `imessage-inbox` share before any agent sees them.

### Telegram adapter
- Telegram Bot API via long-poll. User saves bot token via dashboard (encrypted into `secrets.enc`).
- Same allowlist + dashboard-only-pairing pattern as iMessage.
- Supports text, attachments (downloaded into a `telegram-inbox` share), reactions, edit-in-place for "thinking…" status updates.

### Gmail + Google Calendar adapters
- OAuth2 flow handled in dashboard ("Connect Google account" → browser → consent → token stored encrypted).
- **Scopes are minimal and explicit:** read + modify mail labels and threads, read + write calendar events. **No Drive scope. No Contacts. No Photos. No file-pulling APIs.**
- Tools exposed: `gmail.search`, `gmail.read_thread`, `gmail.draft`, `gmail.send` (destructive), `gcal.list_events`, `gcal.create_event` (destructive), `gcal.update_event`, `gcal.delete_event` (destructive).

### MCP integration (kc-mcp)
- Built on the official Python `mcp` SDK (stdio + HTTP transports).
- **Static MCPs** in `~/KonaClaw/config/mcp.yaml`, started with the supervisor.
- **Dynamic install** — agent calls `install_mcp_server` meta-tool with `{name, command, args, env, why}`. The supervisor:
  1. Pauses, fires destructive-tier approval prompt with the *why* shown prominently.
  2. On approve → spawns the MCP subprocess in a child process group, sandboxed env, registers tools.
  3. New tools default to destructive tier until user downgrades.
  4. Records install in `mcp_installs` SQLite table; dashboard exposes a "Manage MCPs" page (disable, uninstall, change tier defaults).

### Zapier (kc-zapier)
Zapier already exposes its catalog as an MCP server. This sub-project = pre-configure that one MCP + reuse the dynamic-install pattern from kc-mcp for individual zaps. Same destructive-tier-by-default rule.

## 9. Dashboard, Storage & Observability (kc-dashboard + kc-supervisor in depth)

### Dashboard views
Six pages, all served from the dashboard process:

1. **Chat** — left rail = active conversations grouped by agent. Main pane = streaming chat with markdown, tool-call cards, inline approval prompts. Switch which agent a thread routes to mid-conversation.
2. **Agents** — list with status (idle / thinking / paused-for-approval), CPU/memory, last activity, model. New / Edit / Disable / Kill. New-agent form is a YAML editor with field hints.
3. **Shares** — list with size, last modified, agent activity heatmap. Add Share (folder picker → mode → save). Per-share file browser + "Recent agent changes" tab mapping to journal commits.
4. **Permissions** — pending approval queue (top), recent decisions (middle), per-agent override rules (bottom). Each pending item shows the *why*, full args, Approve / Deny / Approve-and-remember.
5. **Monitor** — Ollama up/down + loaded models, supervisor metrics, per-agent metrics, connector status, MCP status, recent errors.
6. **Audit** — searchable, filterable log of every tool call. Per-row Undo button when applicable. Filters: agent, tool, time range, share, success/failure.

### Storage layout — `~/KonaClaw/`
```
~/KonaClaw/
├── config/
│   ├── konaclaw.yaml          # global settings, default model, etc.
│   ├── shares.yaml
│   ├── imessage.yaml          # allowlist
│   ├── telegram.yaml          # bot token (in secrets.enc), allowlist
│   ├── google.yaml            # OAuth tokens (in secrets.enc), scopes
│   └── mcp.yaml               # static MCP configs
├── agents/
│   ├── EmailBot.yaml
│   └── ResearchBot.yaml
├── memory/
│   ├── user.md                # global user profile
│   ├── EmailBot/MEMORY.md
│   └── ResearchBot/MEMORY.md
├── shares/                    # actual paths or symlinks to allowlisted folders
├── data/
│   ├── konaclaw.db            # SQLite: conversations, audit, undo_log, mcp_installs
│   └── secrets.enc            # encrypted token store
└── logs/
    └── supervisor.log
```

### SQLite schema (sketch)
- `conversations(id, agent, channel, started_at)`
- `messages(id, conversation_id, role, content, tool_call_json, ts)`
- `audit(id, ts, agent, tool, args_json, decision, result, undoable)`
- `undo_log(id, audit_id, reverse_kind, reverse_payload_json, applied_at)`
- `mcp_installs(id, name, command, why, installed_by_agent, ts, status)`

### Secrets
Telegram token, Google OAuth tokens, Zapier API key, etc. live in `secrets.enc`, encrypted with a key derived from a passphrase set on first run. Decrypted in-memory only. Never written to logs.

### Observability
1. **Structured logs** (`supervisor.log`) — JSON lines, agent/tool/conversation tagged.
2. **Metrics endpoint** — `/metrics` Prometheus-style; dashboard's Monitor view scrapes directly.
3. **Audit DB** — append-only; never auto-purged; manual purge from dashboard.

### Health
Supervisor exposes `/health` returning per-subsystem status (Ollama reachable, each connector up, each MCP up, sandbox journal write-able). Dashboard polls every 5 s.

## 10. Error Handling

| Failure | Response |
|---|---|
| **Tool error** (file not found, API 404) | Returned to agent as tool result. Agent decides next step. |
| **Agent crash** (exception in loop) | Caught in loop wrapper; agent marked `degraded`; traceback to audit + dashboard. Other agents unaffected. Restart from dashboard. Conversation preserved. |
| **Connector failure** | Connector marked unhealthy in Monitor; retried with exponential backoff. Dashboard still works. |
| **Supervisor crash** | launchd-style restart via launcher script. State durable in SQLite + journal git so resume is clean. In-flight tool calls that didn't commit are surfaced as "interrupted" in audit. |

No silent failures — everything routes through the audit log.

## 11. Testing Strategy

Three layers per sub-project:

1. **Unit tests** — pytest (Python), Vitest (React).
2. **Integration tests** — `kc-testkit` fixture spinning up the supervisor with a fake Ollama (deterministic canned responses), in-memory SQLite, temp `~/KonaClaw/`. Drives real agent flows end-to-end.
3. **Manual smoke checklist** — every sub-project ships with `SMOKE.md` of "things you should be able to do once this is built." Used as the verification gate at the end of each plan.

Real Ollama is non-deterministic and slow → not used in CI. A separate `tests/live/` suite hits real Ollama, runnable on demand on the new machine.

## 12. Build Phasing

| # | Sub-project | Done means |
|---|---|---|
| 1 | kc-core | CLI chats with one agent against your Ollama, no tools |
| 2 | kc-sandbox | That agent can read/write/delete in a share; writes journaled; undo works |
| 3 | kc-supervisor | Service runs; spawn an agent + chat from `curl` |
| 4 | kc-dashboard | Browser chat with KonaClaw; create subagent; see audit; click Undo. **First real usable system.** |
| 5 | kc-mcp | Agent installs an MCP on approval; uses its tools; manage from dashboard |
| 6 | kc-connectors | Text KonaClaw from phone (TG, then iMsg); route per-chat to subagents; connect Gmail + Calendar via OAuth |
| 7 | kc-zapier | Agent connects a zap on approval; uses it |
| 8 | kc-memory | KonaClaw remembers you across conversations; per-agent memory works |

## 13. Open Questions / Deferred Decisions

- **Shell tool OS-level sandbox** — v1 ships with constrained executor + denylist; v2 wraps in `bwrap` / `sandbox-exec`. Decision deferred until we hit a concrete escape we care about.
- **Network policy hardening** — v1 is open-by-default + logged; allowlist UI and outbound content scanning are explicit later add-ons.
- **Chain undo / full session rewind** — single-action undo in v1; chain undo deferred to v2.
- **Storage root location** — defaulting to `~/KonaClaw/`. Easy to relocate to `~/Library/Application Support/KonaClaw/` (Mac convention) or `~/.konaclaw/` (hidden) if user later prefers.
- **Hosted-model fallback** — adapter is OpenAI-compatible so Anthropic / OpenAI / Groq could be wired in later, but explicitly out of scope for v1.

## 14. Next Step

Each sub-project gets its own design spec + implementation plan via a fresh `/superpowers:brainstorming` invocation when its turn comes up. The first one to brainstorm in detail will be **kc-core**, since everything else depends on it. Sub-project specs will live next to this one as `docs/superpowers/specs/YYYY-MM-DD-kc-<name>-design.md`.
