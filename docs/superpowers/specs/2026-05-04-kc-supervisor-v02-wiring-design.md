# kc-supervisor v0.2 Wiring — Design

**Status:** Approved (brainstorming 2026-05-04)
**Sub-project:** Bridge between kc-supervisor v0.1 (merged 2026-05-04, commit `3e18727`) and kc-dashboard (sub-project 4 of 8). Touches kc-core (streaming), kc-supervisor (most), and kc-sandbox surface (consumed; no kc-sandbox changes). Repository: `/Users/sammydallal/Desktop/claudeCode/SammyClaw/`.
**Goal:** Make `/ws/chat` work against real Ollama with full audit + undo + token streaming + safe concurrency, plus subagent spawning. After this lands, the dashboard (next sub-project) ships against a fully wired backend.

---

## Scope

Six items, all in:

1. **AgentRuntime production wiring.** Each `*.yaml` becomes an `AssembledAgent` with sandboxed file tools, async permission check, per-agent OllamaClient (per-agent model from YAML, fallback to `KC_DEFAULT_MODEL`), per-share Journals, and shared Undoer.
2. **Audit logging.** Every tool call (SAFE/MUTATING/DESTRUCTIVE) writes one row to the `audit` table — `agent, tool, args_json, decision, result, undoable, ts`. Undoable calls also write a row to `audit_undo_link(audit_id, undo_op_id)`.
3. **POST /undo/{audit_id}.** Replaces the 501 stub. Looks up the linked op_id and calls `kc_sandbox.undo.Undoer.undo(op_id)`. Returns the reverse-action description on success.
4. **Token streaming.** New `Agent.send_stream(user_text)` async generator in kc-core. `/ws/chat` forwards token deltas, tool-call frames, and tool-result frames as they happen, then a final `assistant_complete` frame. `chat` keeps working for non-streaming callers (CLI, fakes).
5. **Per-conversation locking.** Each cid has its own `asyncio.Lock`. Each turn rebuilds kc-core `Agent.history` from `ConversationManager.list_messages(cid)` rather than carrying it on the runtime. Two clients on different cids run in parallel; same-cid serializes.
6. **POST /agents (subagent spawn).** Body `{name, system_prompt, model?}`. Writes `~/KonaClaw/agents/{name}.yaml`, calls `registry.load_all()`, returns the new agent's snapshot. Name must match `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`. Collision → 409.

**Plus YAML `permission_overrides:` parsing** — a kc-sandbox TODO carried into the supervisor's wiring. The supervisor's assembly reads `cfg.permission_overrides` (tier name strings keyed by tool name) and passes a `{cfg.name: {tool: Tier}}` dict to `PermissionEngine.agent_overrides`.

**Schema correction.** kc-supervisor v1 defined `audit_undo_link.undo_op_id` as `TEXT` (anticipating an opaque string id). After reading kc-sandbox's actual `UndoLog.record() -> int` return type, the column should be `INTEGER` referencing `UndoLog` row ids. v1 has no callers writing to this table, so v0.2 changes the column type and updates the storage tests to use integers (was `"op-abc-123"`, becomes `42`).

---

## Architecture

The three-layer stack stays the same: **kc-core** (agent loop) → **kc-sandbox** (sandboxed tools, permission engine, journals, undo log) → **kc-supervisor** (HTTP/WS service, audit, multi-agent registry, persistence).

What changes:

- kc-supervisor now **owns agent assembly** instead of delegating to `kc_sandbox.wiring.build_sandboxed_agent`. That helper stays as-is for the standalone `konaclaw` CLI use case; supervisor needs richer hooks (audit-wrapped tools, async permission callback with Decision capture, per-agent OllamaClient with custom model).
- kc-core gains a streaming surface (`Agent.send_stream`, `_ChatClient.chat_stream`, `OllamaClient.chat_stream`) without breaking `chat`/`send`.
- kc-supervisor's WS chat handler stops carrying history on the runtime — each turn rebuilds `Agent.history` from SQLite under a per-cid lock.
- Tool invocations get wrapped by an `AuditingToolRegistry` that writes audit rows and (for undoable tools) `audit_undo_link` rows.

### Frame protocol on the wire

`/ws/chat/{cid}` produces ordered frames during a streaming turn:

```
{type: "agent_status", status: "thinking"}
{type: "token", delta: "Sure, let me read "}
{type: "tool_call", call: {id, name, arguments}}
{type: "tool_result", call_id, content}
{type: "token", delta: "The file says..."}
{type: "assistant_complete", content: "Sure, let me read... The file says..."}
```

Clients accumulate `delta`s into a live buffer; on `assistant_complete` they reconcile (`assistant_complete.content` should equal the concatenated deltas — idempotent overwrite is safe).

---

## Components

### kc-core (1 new module + 1 method on Agent + 1 method on OllamaClient)

**`kc_core.stream_frames`** — discriminated unions:
- `ChatStreamFrame`: `TextDelta(content: str)`, `ToolCallsBlock(calls: list[dict])`, `Done(finish_reason: str)`. Wire-level shape from the Ollama client.
- `StreamFrame`: `TokenDelta(content: str)`, `ToolCallStart(call: dict)`, `ToolResult(call_id: str, content: str)`, `Complete(reply: AssistantMessage)`. Agent-level shape consumed by kc-supervisor.

**`kc_core.agent.Agent.send_stream(user_text: str) -> AsyncIterator[StreamFrame]`** — async generator running the same ReAct loop as `send`. Calls `client.chat_stream` instead of `client.chat`. During text generation phases it yields `TokenDelta`s. At tool-call boundaries it yields `ToolCallStart` per call, runs each tool (respecting `permission_check` and the deny-path semantics from v1), yields `ToolResult` per call, then loops. Final yield is `Complete(AssistantMessage(...))`. Honors `max_tool_iterations`; on exceeded, raises (caller catches).

**`kc_core.ollama_client._ChatClient.chat_stream(messages, tools) -> AsyncIterator[ChatStreamFrame]`** — protocol method. The existing `chat` becomes a thin wrapper that calls `chat_stream` and accumulates, eliminating duplication.

**`kc_core.ollama_client.OllamaClient.chat_stream`** — issues `/api/chat?stream=true`, parses the NDJSON response line-by-line. Each line is a JSON object with either `message.content` (text delta), `message.tool_calls` (tool call block), or `done: true` with finish_reason.

### kc-supervisor (3 new modules + 4 changes)

**`kc_supervisor.assembly`** — agent assembly:
- `AssembledAgent(name, system_prompt, ollama_client, registry, engine, permission_check, journals, undo_log)` dataclass.
- `assemble_agent(cfg: AgentConfig, *, shares: SharesRegistry, audit_storage: Storage, broker: ApprovalBroker, ollama_url: str, default_model: str) -> AssembledAgent`. Builds: per-agent `OllamaClient(model=cfg.model)`, per-share `Journal`s, shared `UndoLog`, kc-sandbox's `build_file_tools`, supervisor's `AuditingToolRegistry` wrapping each tool, `PermissionEngine` with `agent_overrides={cfg.name: cfg.permission_overrides}` and `approval_callback=broker`-wired adapter, and an audit-aware async `permission_check` that captures the Decision in a contextvar before returning `(allowed, reason)`.

**`kc_supervisor.audit_tools`** — audit hooks:
- `AuditingToolRegistry`: extends/wraps `kc_core.tools.ToolRegistry`. Internally, each `register(tool)` wraps the tool's `impl` with an audit-writing closure.
- The closure: read Decision from `_decision_contextvar` (set by the audit-aware permission_check earlier in the same async task), invoke the underlying impl, capture (result_or_exception), write `audit` row with `decision=Decision.source` (e.g., `"override+callback"`) and `result=str(result)` (or stringified exception), if a new undo entry was recorded by this tool call, also write `audit_undo_link(audit_id, eid)`. Returns the original return value (or re-raises).
- **op_id capture mechanism:** kc-sandbox's file tools call `undo_log.record(UndoEntry(...)) -> int` internally. The supervisor wraps `UndoLog` with a `RecordingUndoLog` subclass whose `record()` overrides write the returned `eid` into a contextvar before returning. The audit-tool wrapper reads the contextvar (and clears it) after the tool runs. Tools that don't journal (file.read, file.list) leave it empty → no link row written. Per-cid serialization (the WS lock) guarantees no cross-tool contextvar bleed within a single agent turn.

**`kc_supervisor.locks`** — `ConversationLocks`:
- `get(cid: int) -> asyncio.Lock`. Lazy-created, never evicted (cheap; single-user local app, finite cids).

**Changes:**
- `kc_supervisor.agents`: `AgentRuntime` gains `assembled: Optional[AssembledAgent]`. `AgentRegistry.__init__` takes `audit_storage`, `broker`, `shares`, `ollama_url` so it can call `assemble_agent` on each YAML. Assembly failures set `last_error` + `status=DEGRADED` without stopping boot.
- `kc_supervisor.ws_routes.ws_chat`: rewritten to use per-cid lock, rehydrate history from SQLite, run `assembled.send_stream` (where `send_stream` is on the underlying kc-core Agent created from the AssembledAgent fields), forward each frame, persist `UserMessage` + `AssistantMessage` (and any ToolCall/ToolResult intermediate messages) to ConversationManager.
- `kc_supervisor.http_routes`:
  - New `POST /agents` (Pydantic `CreateAgentRequest{name, system_prompt, model: Optional[str]}`); name regex validated; collision → 409; bad pattern → 422 (file not written); writes via tempfile + atomic rename; returns the new agent's snapshot or 500 with cleanup on assembly failure.
  - `POST /undo/{audit_id}` becomes real: `storage.get_undo_op_for_audit` → 404/422 → `Undoer.undo(op_id)` → 200 with the reverse-action description, or 500 with the audit_id preserved on Undoer raise.
- `kc_supervisor.service.Deps`: gains `conv_locks: ConversationLocks` and `shares: SharesRegistry`.
- `kc_supervisor.main`: builds `Storage`, `ApprovalBroker`, `ConversationLocks`, `SharesRegistry.from_yaml`, `AgentRegistry(audit_storage=..., broker=..., shares=..., ollama_url=..., default_model=...)`, then `registry.load_all()`.

### kc-sandbox (consumed; no changes)

We use:
- `kc_sandbox.shares.SharesRegistry.from_yaml`
- `kc_sandbox.journal.Journal`
- `kc_sandbox.undo.UndoLog`, `kc_sandbox.undo.Undoer`
- `kc_sandbox.tools.build_file_tools`, `DEFAULT_FILE_TOOL_TIERS`
- `kc_sandbox.permissions.PermissionEngine`, `to_async_agent_callback`

`build_sandboxed_agent` is **not** used by the supervisor (it stays for the standalone `konaclaw` CLI).

---

## Data Flow

### Boot

`main()` reads env (`KC_HOME`, `KC_OLLAMA_URL` (now consumed!), `KC_DEFAULT_MODEL=qwen2.5:7b`, `KC_PORT`) → constructs `Storage` (init), `ApprovalBroker`, `ConversationLocks`, `SharesRegistry.from_yaml(KC_HOME/config/shares.yaml)` → constructs `AgentRegistry(audit_storage=storage, broker=broker, shares=shares, ollama_url=ollama_url, default_model=default_model)` → `registry.load_all()` iterates `KC_HOME/agents/*.yaml`, calls `assemble_agent(cfg, ...)` per file. Init failures (bad YAML, share path missing, Ollama unreachable at construction time — though OllamaClient construction itself is just a config object, so this is mostly bad YAML) set `last_error` + `status=DEGRADED`; supervisor continues.

### Chat turn (`/ws/chat/{cid}` receives `user_message`)

1. Acquire `conv_locks.get(cid)`.
2. Look up cid via `storage.get_conversation` → 404-frame on miss.
3. Look up agent via `registry.get(conv["agent"])` → 404-frame on miss.
4. Check `assembled is not None` and `status != DISABLED` → error frame + close on miss.
5. Persist `UserMessage(content)` to ConversationManager.
6. Reuse the per-runtime kc-core `Agent` (held on the AssembledAgent). Reset `agent.history = conversations.list_messages(cid)` before the call. The per-cid lock guarantees no cross-conversation history bleed. (Earlier we considered building a fresh Agent per turn; reuse + reset is cheaper and equivalent under the lock.)
7. Emit `agent_status: thinking`.
8. `async for frame in agent.send_stream(content):` — forward each frame (translate `StreamFrame` → wire JSON shape) to the WS. On `Complete(reply)`, persist `AssistantMessage(reply.content)`. On any intermediate `ToolCallStart`/`ToolResult` frames, optionally persist them too (so the dashboard can replay tool history; v0.2 yes).
9. Release lock.

If `send_stream` raises mid-turn: emit `{"type":"error","stage":"model_call","message":...}`, persist no AssistantMessage, set agent `last_error` and `status=DEGRADED`, release lock.

### Undo (`POST /undo/{audit_id}`)

1. `storage.get_undo_op_for_audit(audit_id)` → if None: 422 "this audit row has no journal op (only mutating/destructive file ops journal)".
2. Look up the audit row to find `agent` → `registry.get(agent).assembled.undoer.undo(op_id)` (we'll likely keep an `Undoer` instance per AssembledAgent for the agent's journals + the shared UndoLog).
3. On success: 200 with `{"reversed": {"kind": ..., "details": ...}}`.
4. On Undoer raise: 500 with `{"detail": "undo failed: <msg>", "audit_id": ...}`.

### Subagent spawn (`POST /agents`)

1. Validate body shape via Pydantic.
2. Validate name regex.
3. Check filesystem for `KC_HOME/agents/{name}.yaml` collision → 409.
4. Write to `KC_HOME/agents/{name}.yaml.tmp` with the YAML serialization of the body, fsync, rename to final path.
5. `registry.load_all()`.
6. Return the new agent's snapshot. If assembly failed (status=DEGRADED), return 200 with the snapshot anyway (the agent exists, just degraded — caller sees `last_error`).

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `OllamaClient.chat_stream` raises (Ollama down, bad URL, etc.) | WS handler emits `error` frame, no AssistantMessage persisted, agent `status=DEGRADED`, lock released. |
| Streaming NDJSON malformed | `chat_stream` raises `ValueError`. Same as above. |
| `max_tool_iterations` exceeded | `Agent.send_stream` raises `RuntimeError`. Same as above. |
| Tool execution raises | kc-core's existing try/except wraps as `Error: <type>: <msg>` string in `ToolResult`. Audit row written with `result="Error: ..."`, `undoable=False`. Loop continues. |
| Assembly failure at boot | Caught by `AgentRegistry.load_all`. Runtime ends up `assembled=None, last_error=str(exc), status=DEGRADED`. Supervisor still boots. `/agents` lists it; `/ws/chat` returns "agent X is degraded: <last_error>". |
| Approval broker has no connected client | `await fut` blocks indefinitely. v0.2 keeps this; v0.3 may add a configurable timeout. |
| `POST /undo/{audit_id}` unknown id | 404. |
| `POST /undo/{audit_id}` no link in `audit_undo_link` | 422 "this audit row has no journal op". |
| `Undoer.undo` raises | 500 with the detail and the audit_id; row stays linked (idempotent). |
| `POST /agents` name collision | 409, no overwrite. |
| `POST /agents` bad name pattern | 422, no file written. |
| `POST /agents` write succeeds but assembly fails | 200 with degraded snapshot (file stays on disk; user can edit + restart). |

**Per-cid lock leak:** never evicts. Locks are tiny; conversations are user-finite. v1 multi-user → switch to `WeakValueDictionary`.

---

## Testing

### Unit tests (~25 new)

**kc-core (~5):**
- `OllamaClient.chat_stream`: parse NDJSON happy path; malformed JSON line → raise; truncated stream → raise.
- `Agent.send_stream`: scripted FakeClient yielding text deltas → matching `TokenDelta`s; tool-call boundary → `ToolCallStart` + `ToolResult` in order; `Complete` carries the same `AssistantMessage` shape `send` would; `chat`/`send` regression (still work after the refactor).

**kc-supervisor assembly (~4):**
- Happy path with tmp_path-backed share builds `AssembledAgent` whose registry has the four file tools and whose `permission_check` returns a coroutine.
- Bad share path → typed exception → registry runtime is `status=DEGRADED, last_error=...`.
- Per-agent `OllamaClient` model = `cfg.model` (constructor recorded).
- `permission_overrides` YAML → `engine.agent_overrides[name]` populated.

**kc-supervisor audit_tools (~5):**
- Wrapped tool succeeds, no journal op → one audit row.
- Wrapped tool succeeds, journal op → one audit row + one `audit_undo_link` row.
- Wrapped tool raises → audit row with `result="Error: ..."`, `undoable=False`.
- Decision contextvar propagation: parallel tool calls don't cross-pollinate.
- Decision source recorded verbatim (`"callback"`, `"override+callback"`, `"tier"`).

**kc-supervisor locks (~2):**
- `get(cid)` returns same lock for same cid; different cids → different locks.
- Concurrent acquires on the same cid serialize.

**kc-supervisor http (~5):**
- `POST /agents` writes YAML, registry.snapshot includes new agent, second POST same name → 409.
- `POST /agents` invalid name pattern → 422, no file written.
- `POST /undo/{audit_id}` 404 unknown id; 422 no link; 200 happy path; 500 when Undoer raises.

### Integration tests (~4 in test_ws_chat.py expanded)

- Streaming round-trip with token deltas + a tool call: client receives the full frame sequence in order; SQLite has the 4-message stream persisted.
- Two parallel WS connections to *different* cids of the same agent run truly in parallel (slow FakeClient + `asyncio.wait_for` confirms overlap).
- Two WS connections to the *same* cid serialize (second turn's frames strictly after first's).
- History rehydration: open conversation → exchange a turn → close WS → reopen WS → send another `user_message` → FakeClient.calls[-1] shows prior turn in `messages`.

### Smoke (manual, requires real Ollama, in SMOKE.md)

Replaces the v1 "expected: agent not initialized" steps. New steps:
- Send a `user_message`; receive multiple `token` frames + `assistant_complete`. Reply matches model's actual output for the prompt.
- After a chat with a file tool call, `/audit` shows the entry with the right tool/decision.
- File tool that wrote/deleted: `POST /undo/{audit_id}` reverses it; rerun on the same audit_id raises a kc-sandbox `JournalError` ("nothing to revert") that becomes 500 in v0.2 (idempotent-applied tracking is a v0.3 follow-up).
- `POST /agents` adds a new YAML; restart-or-not, the registry shows it.

**Test count target:** 132 (current) + ~25 = ~157.

---

## Out of Scope (deferred to v0.3)

- Encrypted secrets store (`~/KonaClaw/data/secrets.enc`) — connector-related, not needed for dashboard.
- launchd plist / auto-restart — operations, not needed for dashboard.
- Prometheus `/metrics` endpoint — operations, not needed for dashboard.
- Approval timeout knob — v0.2 keeps indefinite block.
- Shared httpx connection pool across per-agent OllamaClients — premature optimization for single-user local app.
- Multi-user lock eviction (`WeakValueDictionary`) — v1 is single-user.
- Streaming-while-tool-running fine-grained (e.g., showing partial tool output as it streams) — current design streams between turns, which is enough for v0.2 UX.

---

## Cross-references

- v1 supervisor merge: commit `3e18727` (2026-05-04). All v1 endpoints stay backward-compatible after v0.2.
- kc-sandbox merge: commit `efd927a` (2026-05-04). v0.2 consumes its primitives; no kc-sandbox changes.
- kc-core merge: commit `f416816` (2026-05-03). v0.2 adds the streaming surface to kc-core.
- Dashboard plan: `docs/superpowers/plans/2026-05-02-kc-dashboard.md`. Audit pass needed before dispatch (per recipe lessons).
- `audit_undo_link` schema (chosen 2026-05-04 as option (b) of three): `audit_id INTEGER PRIMARY KEY, undo_op_id TEXT NOT NULL`. Enforces 1:1 (one tool call = one journal op). `INSERT OR IGNORE` semantics (first link wins).
