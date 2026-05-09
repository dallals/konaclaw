# Dashboard tokens-per-second metric — design

**Date:** 2026-05-09
**Scope:** Surface model throughput in the KonaClaw dashboard so generation tok/s and time-to-first-byte (TTFB) can be compared across models — particularly when swapping in local models via Ollama.
**Affected subrepos:** `kc-core`, `kc-supervisor`, `kc-dashboard`.

## Goal

When the user sends a chat message, the dashboard shows:

1. A **live tok/s ticker** during streaming.
2. A **per-reply final number** that snaps to authoritative provider-reported tokens once streaming completes.
3. A **TTFB figure** (time-to-first-byte) as a secondary readout — labelled `ttfb` rather than `prompt_eval` because the OpenAI-compatible Ollama endpoint does not return `prompt_eval_duration`, so the value is wall-clocked client-of-the-LLM-side and includes network/queue latency.

These numbers are visible both in the chat header (always shows current/most-recent turn) and as a per-bubble badge on each assistant message (historical record across the session).

## Non-goals

- Cost estimation in dollars.
- Cross-conversation aggregation or session averages.
- Token-by-token timing histograms.
- Refactoring `kc-core`'s frame protocol beyond the additions described here.

## Architecture

```
┌─ kc-core ────────────────────────────────────────────────┐
│ OllamaClient.chat_stream                                 │
│   • sends stream_options.include_usage=true              │
│   • stamps t_first_byte on first TextDelta               │
│   • stamps t_done on terminal chunk                      │
│   • captures final {usage:{prompt_tokens,                │
│       completion_tokens, ...}}                           │
│   ▶ yields ChatUsage(input, output, ttfb_ms,             │
│       generation_ms, usage_reported)                     │
│ Agent.send_stream                                        │
│   • forwards each ChatUsage as TurnUsage(call_index,…)   │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─ kc-supervisor ──────────────────────────────────────────┐
│ Per-user-turn aggregator collects every TurnUsage,       │
│ sums tokens and durations, counts calls.                 │
│ At end-of-turn, immediately before assistant_complete,   │
│ emits ONE WS frame:                                      │
│   {type:"usage", input_tokens, output_tokens,            │
│    ttfb_ms, generation_ms, calls}                        │
│ Aggregate is also persisted on the AssistantMessage row  │
│ in SQLite so historical bubbles still show their badge.  │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─ kc-dashboard ───────────────────────────────────────────┐
│ Live ticker: char-based estimate during streaming        │
│   (output_chars / 4) / elapsed_seconds, recompute @ 4Hz  │
│ Authoritative: replace estimate with usage frame numbers │
│ Header strip: shows current / last turn (live)           │
│ Per-bubble badge: historical, from persisted DB blob     │
└──────────────────────────────────────────────────────────┘
```

**Invariants:**

- One user message → exactly one `usage` WS frame on a successful turn, or zero on an errored turn.
- The live ticker is always an estimate and is always overwritten by authoritative numbers when the `usage` frame arrives.
- The persisted SQLite row is written only on a clean `Complete`. Errored / partial turns leave no usage row.

## Wire protocol

### kc-core stream frames (`kc-core/src/kc_core/stream_frames.py`)

Two new dataclasses, added to the existing union types:

```python
@dataclass(frozen=True)
class ChatUsage:
    """Wire-level usage frame from a single OllamaClient.chat_stream call."""
    input_tokens: int          # 0 if usage_reported is False
    output_tokens: int         # 0 if usage_reported is False
    ttfb_ms: float             # always set; wall-clocked from request start to first text byte
    generation_ms: float       # always set; wall-clocked from first text byte to terminal chunk
    usage_reported: bool       # True iff provider returned a usable `usage` object

@dataclass(frozen=True)
class TurnUsage:
    """Agent-level usage frame, one per inner chat_stream call."""
    call_index: int            # 0, 1, 2... within a single send_stream invocation
    input_tokens: int
    output_tokens: int
    ttfb_ms: float
    generation_ms: float
    usage_reported: bool
```

`ChatStreamFrame` and `StreamFrame` unions extend to include them.

### kc-core OllamaClient changes (`kc-core/src/kc_core/ollama_client.py`)

- Request body adds `"stream_options": {"include_usage": True}`.
- The streaming loop captures `t_request_start` before the first chunk, `t_first_byte` on the first non-empty `TextDelta`, and `t_done` on the terminal chunk (`finish_reason` set or `[DONE]` sentinel). If no text is ever yielded (tool-only turn), `t_first_byte` falls back to `t_done` so `generation_ms == 0`.
- After the terminal chunk, emit one `ChatUsage`:
  - `input_tokens` / `output_tokens`: from upstream `usage` if present and both fields are non-negative integers, else `0` with `usage_reported=False`.
  - `ttfb_ms = (t_first_byte - t_request_start) * 1000`.
  - `generation_ms = (t_done - t_first_byte) * 1000`.
- The frame is yielded after `Done(finish_reason=...)` so existing consumers that break on `Done` are not affected unless they opt in.

### kc-core Agent changes (`kc-core/src/kc_core/agent.py`)

- `Agent.send_stream` tracks an inner `call_index` counter, incremented per `chat_stream` invocation.
- When the inner stream yields `ChatUsage`, the agent yields a corresponding `TurnUsage` frame to the caller.
- `Complete` is yielded after the final `TurnUsage` (existing `Complete` semantics unchanged).

### Supervisor WS frame (`kc-supervisor/src/kc_supervisor/ws_routes.py`, `inbound.py`)

A new event type `{type: "usage"}` is emitted to the dashboard immediately before `{type: "assistant_complete"}`:

```json
{
  "type": "usage",
  "input_tokens": 1283,
  "output_tokens": 412,
  "ttfb_ms": 1042,
  "generation_ms": 3240,
  "calls": 2,
  "usage_reported": true
}
```

The aggregator initialises empty state at the start of a user turn and:

- Sums `input_tokens` and `output_tokens` across every `TurnUsage` (or yields `output_tokens: null`, `input_tokens: null` if any constituent had `usage_reported=False` — partial reporting is treated as no reporting).
- Sums `generation_ms` across every `TurnUsage`.
- Sets `ttfb_ms` to the **first** `TurnUsage`'s ttfb (the user-perceived time-to-first-byte; subsequent inner calls do not contribute).
- Sets `calls` to the count of `TurnUsage` frames seen.

If the stream raises before `Complete`, the aggregator state is dropped and no `usage` frame is sent.

### SQLite schema (additive migration)

The `messages` table gains an optional `usage_json TEXT` column, NULL for any pre-existing or non-AssistantMessage rows. The supervisor writes the aggregated payload (same shape as the WS frame, minus `type`) to this column when persisting the `AssistantMessage`. The `listMessages` HTTP route echoes the parsed JSON through to the dashboard as a `usage` field on the message object.

### Dashboard types (`kc-dashboard/src/ws/useChatSocket.ts`)

The event union extends to include:

```ts
| { type: "usage";
    input_tokens: number | null;
    output_tokens: number | null;
    ttfb_ms: number;
    generation_ms: number;
    calls: number;
    usage_reported: boolean }
```

`Chat.tsx` derives:

- `currentTurnUsage`: most recent `usage` event since the last `assistant_complete` (drives the header strip).
- `bubbleUsage[messageId]`: from the persisted `messages` query (drives the per-bubble badge).

## UI presentation

### Chat header strip (`kc-dashboard/src/views/Chat.tsx:357`)

The existing `<dl>` gains two rows below the current `Status` row:

```
Last reply  127 t/s · 412 tok          ← live during stream, snaps on usage
TTFB        1.04 s · 2 calls           ← muted color; calls suffix only when > 1
```

During streaming the `Last reply` value shows `~131 t/s · streaming` with a subtle pulsing dot until the `usage` frame lands. The `TTFB` row is hidden until the first `usage` frame for the active conversation arrives (any `usage` frame, including `usage_reported=false` ones — TTFB is always populated).

When the arrived `usage` frame has `usage_reported=false`, the `Last reply` row falls back to the muted live-estimate value frozen at end-of-stream and labels it `~131 t/s · estimate`. Both rows render together; only the `Last reply` row's authoritative-vs-estimate state differs.

### Per-bubble badge (`kc-dashboard/src/components/MessageBubble.tsx`)

Assistant rows gain a tiny mono-font footer:

```
…end of assistant text…
                                127 t/s · 412 tok · 2 calls · ttfb 1.04 s
```

Style: `text-muted2`, same uppercase tracking as other on-page metrics. The `2 calls` suffix is suppressed when `calls === 1`. The currently-streaming bubble shows the live estimate and updates at 4 Hz.

### Formatting rules

- Tokens/sec: integer (`127 t/s`) when ≥ 10, one decimal (`8.4 t/s`) when < 10.
- Token count: integer with no separator under 10 000 (`412 tok`); `k` suffix above with one decimal (`12.4k tok`).
- TTFB: seconds with two decimals under 10 s (`1.04 s`), one decimal otherwise (`12.3 s`).
- When `output_tokens === 0` (tool-only turn): show `— · ttfb 1.04 s · N calls` instead of `0 t/s`.
- When `usage_reported === false`: badge shows `— ttfb only` and the live estimate is rendered in muted color permanently (never gets overwritten).

### Live ticker math

- `elapsed = now() − t_first_token_ws_event`.
- `estimated_output_tokens = output_chars / 4` (OpenAI's rule-of-thumb; ±20%, but converges fast).
- Recompute every 250 ms via a `setInterval`, only while the `streaming` text buffer is non-empty.
- Stop the interval immediately when `assistant_complete` arrives or the conversation switches.

## Error handling

| Scenario | Behaviour |
| --- | --- |
| Provider returns no `usage` field. | `ChatUsage` still emitted with `usage_reported=False`. WS `usage` frame has `input_tokens: null`, `output_tokens: null`. Dashboard renders `— ttfb only`. Live ticker remains in muted color (no snap). One supervisor warning logged per `(model, provider)` pair per process. |
| `output_tokens === 0` (tool-only turn). | Badge: `— · ttfb 1.04 s · N calls`. No tok/s computation. |
| `generation_ms < 50 ms`. | Badge: `instant · 412 tok`. Avoids meaningless `9999 t/s` blowups on cached responses. |
| Stream errored mid-flight. | Existing `{type: "error"}` path fires. No `usage` frame. Live ticker freezes on its last estimate; the bubble's badge then renders `— interrupted`. Aggregator state for that turn is dropped — no SQLite row written. |
| Multi-call turn where one inner call errors. | Same as previous row: aggregated `usage` is only emitted on a clean `Complete`. |
| User switches conversations mid-stream. | `useChatSocket(activeConv)` remounts (existing behaviour). Orphaned tokens discarded. Live ticker resets. No SQLite write — `usage` only persists with a completed `AssistantMessage`. |
| Legacy / pre-migration AssistantMessage rows. | `usage_json` is NULL. Dashboard treats missing usage as "render no badge" rather than zero values. |
| Provider returns `usage` with garbage values (negative, missing fields). | `OllamaClient` validates: any non-positive integer or missing key → `usage_reported=False`. Warning logged. |
| Partial reporting across multi-call turn (some calls report, others don't). | Treated as no reporting: aggregated WS frame uses `null` for token counts but still includes the wall-clocked durations and call count. |

## Testing

### kc-core (`kc-core/tests/`)

- `test_ollama_client.py`: Replay an SSE fixture that includes a final `usage` chunk; assert `chat_stream` emits a `ChatUsage` with the expected counts, `usage_reported=True`, and monotonic-time-derived `ttfb_ms` / `generation_ms`.
- `test_ollama_client.py`: Replay an SSE fixture without `usage`; assert `ChatUsage` with `usage_reported=False` and zero tokens but populated durations.
- `test_ollama_client.py`: Replay an SSE fixture where `usage` arrives mid-stream; assert it is still captured.
- `test_ollama_client.py`: Replay an SSE fixture with garbage usage (negative `prompt_tokens`); assert `usage_reported=False`.
- `test_agent.py`: Multi-step agent run (text → tool call → text); assert two `TurnUsage` frames with `call_index` 0 and 1 followed by exactly one `Complete`.
- All time-based fields are tested with a monkeypatched `time.monotonic`.

### Supervisor (`kc-supervisor/tests/`)

- `test_ws_routes.py`: Mock `core_agent.send_stream` to yield two `TurnUsage` frames then `Complete`. Assert exactly one `{type: "usage", calls: 2, ...}` WS frame is emitted with summed `input_tokens`/`output_tokens` and summed `generation_ms`, and that it lands immediately before `assistant_complete`. Assert `ttfb_ms` equals the first `TurnUsage`'s value.
- `test_ws_routes.py`: Error path — yield one `TurnUsage`, then raise; assert no `usage` frame is sent and no SQLite row is written.
- `test_ws_routes.py`: Partial reporting — yield one `TurnUsage` with `usage_reported=True`, one with `usage_reported=False`; assert WS frame has `input_tokens: null`, `output_tokens: null`, but populated durations.
- `test_inbound.py` / `test_storage.py`: After a clean turn, `listMessages` returns the `AssistantMessage` with the `usage` JSON blob; legacy rows return no `usage` field.

### Dashboard (`kc-dashboard/tests/`)

- Component test for the header strip — feed it a sequence of mocked WS events (`token`, `token`, `usage`, `assistant_complete`); assert the live ticker's value pre-`usage`, the snap-to-authoritative post-`usage`, and the rendered formatting.
- Component test for `MessageBubble` badge — feed `usage` blob, assert formatted output for: normal turn, tool-only turn (`output_tokens=0`), interrupted turn (no usage), legacy row (no usage field at all).
- Snapshot tests for the `< 10 t/s` decimal formatting and the `k` suffix threshold.

### Manual smoke (`kc-dashboard/SMOKE.md`, `kc-supervisor/SMOKE.md`)

Add gates:

- Send a single-turn reply → see live ticker, then authoritative snap.
- Send a tool-using reply → see `2 calls` suffix, no `0 t/s` blowup.
- Point at a provider that does not support `include_usage` → see `— ttfb only` rendering and a single supervisor log line.
- Reload the page → see historical badges restored from SQLite.
