# "Feels Faster" Design — `keep_alive` + Progress Indicator

**Goal:** Reduce real and perceived wait time when chatting with Kona. Two independent pieces, one phase.

**Status:** Brainstorm complete 2026-05-15. Awaiting plan.

**Scope:** Backend `keep_alive` config in `kc-core/ollama_client.py`. Frontend `ChatProgressIndicator` in `kc-dashboard` replacing the current passive `ThinkingIndicator`. No supervisor changes. No new packages.

---

## Why

Current observed TTFB on cold turns: 30–60 seconds (e.g., gemma4:31b's 19 GB cold-load when Ollama unloaded it after 5 min idle). During the wait, the dashboard shows a passive "● ● ● THINKING" dot — Sammy has no signal about what Kona is actually doing.

Two perceived-speed wins:
1. **Cut real latency** for warm chats by telling Ollama to keep the model resident.
2. **Surface progress** during the unavoidable cold-load and tool-call waits so the wait feels purposeful.

---

## Piece 1 — `keep_alive` on chat requests

**File:** `kc-core/src/kc_core/ollama_client.py`

Two request paths exist:
- `_chat_stream_openai` — `POST /v1/chat/completions` (OpenAI-compatible SSE; used when `think` is None or when the base_url is a remote provider).
- `_chat_stream_native` — `POST /api/chat` (Ollama native JSON-lines; used when `think` is True/False).

Both build a body dict before sending. Insert one new key in each:

```python
_DEFAULT_KEEP_ALIVE = os.environ.get("KC_OLLAMA_KEEP_ALIVE", "30m")

# in both _chat_stream_openai and _chat_stream_native, after building body:
body["keep_alive"] = _DEFAULT_KEEP_ALIVE
```

### Behavior

- Default `"30m"` — Ollama keeps the model resident for 30 minutes after the last request. Subsequent chats within that window have single-digit-second TTFB instead of 30–60s.
- `KC_OLLAMA_KEEP_ALIVE="-1"` — never unload. Good for a personal machine that doesn't need to free RAM.
- `KC_OLLAMA_KEEP_ALIVE="0"` — unload immediately after the request (Ollama default). For test environments that want the old behavior.

### Backend compatibility

- Ollama native `/api/chat` accepts `keep_alive` directly. ✓
- Ollama-served `/v1/chat/completions` is a thin shim over the same backend — it accepts `keep_alive` as an unknown field and applies it. ✓
- Remote OpenAI-compat endpoints (NVIDIA NIM, OpenRouter) ignore unknown body fields. Adding `keep_alive` to their request is a silent no-op. ✓

So the single insertion works across every backend the kc-core client currently talks to.

### Tests

`kc-core/tests/test_ollama_client.py` (extend; pattern from existing tests):

1. Build a `_FakeChatClient` that captures the request body for both endpoints.
2. Assert `body["keep_alive"] == "30m"` in default mode.
3. Override env to `"-1"`, instantiate, assert `body["keep_alive"] == "-1"`.
4. Override to `"0"`, assert `body["keep_alive"] == "0"`.

The existing `chat_stream` tests already exercise both paths — extending them is mechanical.

---

## Piece 2 — `ChatProgressIndicator`

**Files:**
- `kc-dashboard/src/components/ChatProgressIndicator.tsx` (new)
- `kc-dashboard/src/components/ChatProgressIndicator.test.tsx` (new)
- `kc-dashboard/src/views/Chat.tsx` (swap in the new component)
- `kc-dashboard/src/components/ThinkingIndicator.tsx` (deleted)
- `kc-dashboard/src/components/ThinkingIndicator.test.tsx` (deleted, if it exists)

### Inputs

Two props from `Chat.tsx`:

```ts
interface ChatProgressIndicatorProps {
  // In-flight + recently-completed tool calls for the current turn.
  // Cleared when the turn produces an assistant_complete frame.
  toolCalls: ToolCallState[];
  // Filename lookup for attachment chips referenced in tool args.
  attachmentFilenames: Record<string, string>; // att_id -> filename
}

interface ToolCallState {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
}
```

`Chat.tsx` already receives the underlying `tool_call`, `tool_result`, and `assistant_complete` WS frames — see the existing handler around line 309. We extend the local state it builds to maintain the `ToolCallState[]` array per turn, then pass it down.

The `attachmentFilenames` map comes from the same chip-row state Task 22's `useAttachmentUpload` hook already exposes (`readyAttachmentIds` plus their chips, which carry the filename). We pre-derive a lookup map and pass it in.

### Visual structure

```
● ● ● Reading will.pdf…
  📎 read_attachment(will.pdf) ⟳
  🌐 web_search("brooklyn weather") ✓
```

**Top line — stage label, derived from the most recent in-flight tool call:**

| Tool name | Label |
|---|---|
| `read_attachment` | `Reading {filename}…` where filename is resolved from `attachmentFilenames[args.attachment_id]`, or `Reading attachment…` if unresolved |
| `list_attachments` | `Listing attachments…` |
| `web_search` | `Searching the web…` |
| `web_fetch` | `Fetching {url}…` (host portion of the URL, truncated to 40 chars) |
| `mcp.perplexity.search` (any `mcp.perplexity.*`) | `Asking Perplexity…` |
| any other tool name | `Running {tool_name}…` |
| no tool call yet OR all done but no `assistant_complete` | `Thinking…` |

The leading `● ● ●` is the existing pulsing dot animation, kept as-is.

**Chip row — one chip per tool call in this turn:**

Each chip shows a small icon + short label + status icon:
- Icon by tool family (📎 for attachments, 🌐 for web, 🤖 for MCP perplexity, ⚙️ for other).
- Label: tool name + key arg in parens (filename for read_attachment, truncated query for web_search, host for web_fetch, empty for list_attachments).
- Status: ⟳ (animated) for running, ✓ for done, ⚠ for error.
- Order: chronological, oldest first. New ones append.

Chips persist for the duration of the turn — even after a tool finishes, its ✓ chip stays visible so Sammy can see what Kona did. Cleared on `assistant_complete`.

### Tests

`ChatProgressIndicator.test.tsx`:

1. `renders default Thinking label when no tool calls` — pass `toolCalls=[]`, assert "Thinking…".
2. `renders read_attachment label with filename` — pass one running `read_attachment` call with a resolved filename in the map; assert "Reading will.pdf…".
3. `renders read_attachment label without filename` — same call, no entry in map; assert "Reading attachment…".
4. `renders web_search label` — passes a `web_search` call; assert "Searching the web…".
5. `renders chip row with multiple calls` — passes two calls (one running, one done); assert both chip labels are in the document and the icons reflect status.
6. `unknown tool falls back to Running {name}…` — passes a tool named "weird_tool"; assert "Running weird_tool…".

Patterns to follow: existing `AttachmentChip.test.tsx` (Vitest + React Testing Library).

### Chat.tsx integration

Around the existing send_stream loop in `Chat.tsx`:

1. Add local state: `const [turnToolCalls, setTurnToolCalls] = useState<ToolCallState[]>([])`.
2. In the WS `tool_call` handler: append a new entry with `status: "running"`.
3. In the WS `tool_result` handler: mark the matching id `done` (or `error` if the result content starts with an error JSON shape).
4. In the WS `assistant_complete` handler: clear `setTurnToolCalls([])`.
5. Build the `attachmentFilenames` map from `chips` (from `useAttachmentUpload`) plus parsed chip lines from past user messages in the conversation (filenames from the `[attached: ...]` prefix lines).
6. Replace `<ThinkingIndicator />` with `<ChatProgressIndicator toolCalls={turnToolCalls} attachmentFilenames={filenameMap} />`.

The existing pulsing-dot animation lives in the old `ThinkingIndicator`. Move the CSS keyframes into the new component verbatim so the dot animation stays identical.

### Out of scope (deferred)

- **Live tokens/sec ticker** during streaming — the post-reply usage line already shows this. Adding a live one is a separate phase if Sammy wants it.
- **Reasoning channel surface** — Ollama emits reasoning deltas via `ReasoningTokenDelta`. Surfacing them as a collapsible "Kona is thinking out loud" panel is its own phase.
- **Cross-turn progress history** — chips clear at `assistant_complete`. Persistent per-turn tool-call rendering in past messages is a separate concern (the audit tab already shows full history).

---

## Risks & open questions

- **Filename resolution for `read_attachment` after a fresh page load:** the chip line on a past user message contains `[attached: filename, ..., id=att_xxx]`. We need the indicator's `attachmentFilenames` map to include those — not just chips currently in the upload hook. The integration step pre-derives the map from both sources.
- **`web_fetch` URL truncation:** showing the full URL clutters the indicator. Truncating to host-only (or first 40 chars) keeps the label readable; the chip row can show a slightly longer view.
- **Multiple concurrent tool calls:** the model can request several tool calls in one block. The indicator handles this fine (each gets a chip; the top label tracks the most-recently-started running call).
