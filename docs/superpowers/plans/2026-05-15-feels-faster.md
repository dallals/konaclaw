# "Feels Faster" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce real and perceived wait time when chatting with Kona by (1) telling Ollama to keep the model resident via `keep_alive`, and (2) replacing the passive `ThinkingIndicator` with a stage-aware `ChatProgressIndicator` that surfaces live tool-call progress.

**Architecture:** Backend change is one new constant + two body-dict insertions in `kc-core/ollama_client.py`. Frontend change is a new React component that consumes the existing WS `tool_call`/`tool_result`/`assistant_complete` frame stream Chat.tsx already receives. No supervisor changes. No new packages.

**Tech Stack:** Python 3.11+ (kc-core), httpx (existing), React 18 + TypeScript + Vitest (kc-dashboard).

**Spec:** `docs/superpowers/specs/2026-05-15-feels-faster-design.md`

---

## File Map

### kc-core

| File | Action |
|---|---|
| `kc-core/src/kc_core/ollama_client.py` | Modify — add `_DEFAULT_KEEP_ALIVE` constant; insert `body["keep_alive"] = _DEFAULT_KEEP_ALIVE` in both `_chat_stream_openai` and `_chat_stream_native` body builders. |
| `kc-core/tests/test_ollama_client.py` | Modify — add 3 tests covering default value, env override to `-1`, env override to `0`. |

### kc-dashboard

| File | Action |
|---|---|
| `kc-dashboard/src/components/ChatProgressIndicator.tsx` | Create — new component replacing ThinkingIndicator. |
| `kc-dashboard/src/components/ChatProgressIndicator.test.tsx` | Create — 6 tests covering label states and chip rendering. |
| `kc-dashboard/src/views/Chat.tsx` | Modify — maintain `turnToolCalls` state per turn; derive `attachmentFilenames` map; swap `ThinkingIndicator` for `ChatProgressIndicator`. |
| `kc-dashboard/src/components/ThinkingIndicator.tsx` | Delete after Chat.tsx swap. |
| `kc-dashboard/src/components/ThinkingIndicator.test.tsx` | Delete if present. |

---

## Task 1: `keep_alive` constant + insertion in both paths

**Files:**
- Modify: `kc-core/src/kc_core/ollama_client.py`

- [ ] **Step 1: Add the import + constant**

In `kc-core/src/kc_core/ollama_client.py`, line 1-9 currently reads:

```python
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse
import httpx
from kc_core.stream_frames import TextDelta, ReasoningDelta, ToolCallsBlock, Done, ChatUsage
```

Add `import os` after `import json`:

```python
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse
import httpx
from kc_core.stream_frames import TextDelta, ReasoningDelta, ToolCallsBlock, Done, ChatUsage


def _default_keep_alive() -> str:
    """Returns the value passed to Ollama's `keep_alive` request field.

    Defaults to `"30m"` — Ollama keeps the model resident for 30 minutes after
    the last request. Override with `KC_OLLAMA_KEEP_ALIVE`:
      - `"-1"` → never unload (good for personal machines with spare RAM).
      - `"0"`  → unload immediately after the request (Ollama default).
      - `"<duration>"` → any Go duration string Ollama accepts ("10m", "1h", etc.).

    Read at call time (not import) so tests can monkeypatch the env var.
    """
    return os.environ.get("KC_OLLAMA_KEEP_ALIVE", "30m")
```

- [ ] **Step 2: Insert into `_chat_stream_openai` body**

In `kc-core/src/kc_core/ollama_client.py`, find the body construction in `_chat_stream_openai` (around line 116). Currently:

```python
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
```

Add the keep_alive line after the tools block:

```python
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
        body["keep_alive"] = _default_keep_alive()
```

- [ ] **Step 3: Insert into `_chat_stream_native` body**

In the same file, find the body construction in `_chat_stream_native` (around line 260). Currently:

```python
        body: dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "stream": True,
            "think": think,
        }
        if tools:
            body["tools"] = tools
```

Add the keep_alive line:

```python
        body: dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "stream": True,
            "think": think,
        }
        if tools:
            body["tools"] = tools
        body["keep_alive"] = _default_keep_alive()
```

- [ ] **Step 4: Commit (tests come next task)**

```bash
git add kc-core/src/kc_core/ollama_client.py
git commit -m "feat(kc-core): add keep_alive to chat requests (default 30m; KC_OLLAMA_KEEP_ALIVE env override)"
```

---

## Task 2: Tests for `keep_alive`

**Files:**
- Modify: `kc-core/tests/test_ollama_client.py`

- [ ] **Step 1: Inspect existing test patterns**

Open `kc-core/tests/test_ollama_client.py` and find an existing test that already builds an `OllamaClient` and captures a request body via `httpx.MockTransport`. The new tests follow the same pattern. If the file already has helpers like `_make_client(handler)` or similar, reuse them; otherwise write a self-contained fixture per the snippet below.

- [ ] **Step 2: Write the failing tests**

Append at the end of `kc-core/tests/test_ollama_client.py`:

```python
import json as _json_kalive
import os as _os_kalive

import httpx as _httpx_kalive
import pytest

from kc_core.ollama_client import OllamaClient


def _capture_handler(captured: dict):
    def handler(request: _httpx_kalive.Request) -> _httpx_kalive.Response:
        captured["body"] = _json_kalive.loads(request.content.decode())
        # Minimal SSE response so the streaming loop terminates cleanly.
        sse = (
            b"data: {\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":null}]}\n\n"
            b"data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )
        return _httpx_kalive.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    return handler


@pytest.mark.asyncio
async def test_keep_alive_default_is_30m(monkeypatch):
    monkeypatch.delenv("KC_OLLAMA_KEEP_ALIVE", raising=False)
    captured: dict = {}
    transport = _httpx_kalive.MockTransport(_capture_handler(captured))
    # Inject the mock transport by patching httpx.AsyncClient to return our transport.
    # The client constructs its own AsyncClient per call, so we patch at the module level.
    import kc_core.ollama_client as _ocm
    orig_async_client = _ocm.httpx.AsyncClient
    def _async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)
    monkeypatch.setattr(_ocm.httpx, "AsyncClient", _async_client_factory)

    client = OllamaClient(base_url="http://x.example", model="m", api_key=None)
    async for _f in client.chat_stream(messages=[{"role": "user", "content": "hi"}], tools=[]):
        pass

    assert captured["body"]["keep_alive"] == "30m"


@pytest.mark.asyncio
async def test_keep_alive_env_override_minus_one(monkeypatch):
    monkeypatch.setenv("KC_OLLAMA_KEEP_ALIVE", "-1")
    captured: dict = {}
    transport = _httpx_kalive.MockTransport(_capture_handler(captured))
    import kc_core.ollama_client as _ocm
    orig_async_client = _ocm.httpx.AsyncClient
    def _async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)
    monkeypatch.setattr(_ocm.httpx, "AsyncClient", _async_client_factory)

    client = OllamaClient(base_url="http://x.example", model="m", api_key=None)
    async for _f in client.chat_stream(messages=[{"role": "user", "content": "hi"}], tools=[]):
        pass

    assert captured["body"]["keep_alive"] == "-1"


@pytest.mark.asyncio
async def test_keep_alive_env_override_zero(monkeypatch):
    monkeypatch.setenv("KC_OLLAMA_KEEP_ALIVE", "0")
    captured: dict = {}
    transport = _httpx_kalive.MockTransport(_capture_handler(captured))
    import kc_core.ollama_client as _ocm
    orig_async_client = _ocm.httpx.AsyncClient
    def _async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_async_client(*args, **kwargs)
    monkeypatch.setattr(_ocm.httpx, "AsyncClient", _async_client_factory)

    client = OllamaClient(base_url="http://x.example", model="m", api_key=None)
    async for _f in client.chat_stream(messages=[{"role": "user", "content": "hi"}], tools=[]):
        pass

    assert captured["body"]["keep_alive"] == "0"
```

- [ ] **Step 3: Run, verify PASS**

```bash
cd kc-core && pytest tests/test_ollama_client.py -v 2>&1 | tail -15
```

Expected: 3 new tests PASS. All previously existing tests in the file still PASS.

If the new tests fail because `OllamaClient.chat_stream` uses a different mocking technique in this file (e.g., the existing tests use `httpx.Client.stream` directly rather than constructing a fresh `AsyncClient`), look at how the closest existing test mocks the transport and copy its pattern verbatim — the underlying assertion (`captured["body"]["keep_alive"] == ...`) stays the same.

- [ ] **Step 4: Run the full kc-core suite**

```bash
cd kc-core && pytest -v 2>&1 | tail -10
```

Expected: all PASS (previously 87 + 3 new = 90).

- [ ] **Step 5: Commit**

```bash
git add kc-core/tests/test_ollama_client.py
git commit -m "test(kc-core): keep_alive default + env override (-1, 0)"
```

---

## Task 3: `ChatProgressIndicator` skeleton + label logic

**Files:**
- Create: `kc-dashboard/src/components/ChatProgressIndicator.tsx`
- Create: `kc-dashboard/src/components/ChatProgressIndicator.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/src/components/ChatProgressIndicator.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { ChatProgressIndicator, type ToolCallState } from "./ChatProgressIndicator";


function call(overrides: Partial<ToolCallState> = {}): ToolCallState {
  return {
    id: overrides.id ?? "call_1",
    name: overrides.name ?? "read_attachment",
    args: overrides.args ?? {},
    status: overrides.status ?? "running",
  };
}


describe("ChatProgressIndicator", () => {
  it("renders Thinking when no tool calls", () => {
    render(<ChatProgressIndicator toolCalls={[]} attachmentFilenames={{}} />);
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });

  it("renders read_attachment label with resolved filename", () => {
    const calls = [call({ args: { attachment_id: "att_abc" } })];
    render(
      <ChatProgressIndicator
        toolCalls={calls}
        attachmentFilenames={{ att_abc: "will.pdf" }}
      />,
    );
    expect(screen.getByText(/reading will\.pdf/i)).toBeInTheDocument();
  });

  it("renders read_attachment label without filename (unresolved)", () => {
    const calls = [call({ args: { attachment_id: "att_xyz" } })];
    render(
      <ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />,
    );
    expect(screen.getByText(/reading attachment/i)).toBeInTheDocument();
  });

  it("renders web_search label", () => {
    const calls = [call({ name: "web_search", args: { query: "weather" } })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/searching the web/i)).toBeInTheDocument();
  });

  it("renders web_fetch label with truncated host", () => {
    const calls = [call({ name: "web_fetch", args: { url: "https://en.wikipedia.org/wiki/Claude_Shannon" } })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/fetching en\.wikipedia\.org/i)).toBeInTheDocument();
  });

  it("falls back to Running {name} for unknown tools", () => {
    const calls = [call({ name: "weird_tool" })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/running weird_tool/i)).toBeInTheDocument();
  });

  it("uses most-recent running call for top label when multiple in flight", () => {
    const calls = [
      call({ id: "call_1", name: "web_search", args: { query: "x" }, status: "done" }),
      call({ id: "call_2", name: "read_attachment", args: { attachment_id: "att_abc" } }),
    ];
    render(
      <ChatProgressIndicator
        toolCalls={calls}
        attachmentFilenames={{ att_abc: "will.pdf" }}
      />,
    );
    // The newest running call's label wins.
    expect(screen.getByText(/reading will\.pdf/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, verify FAIL**

```bash
cd kc-dashboard && npm test -- ChatProgressIndicator --run 2>&1 | tail -10
```

Expected: FAIL — `ChatProgressIndicator` not importable.

- [ ] **Step 3: Implement the component**

Create `kc-dashboard/src/components/ChatProgressIndicator.tsx`:

```typescript
import React, { useMemo } from "react";


export interface ToolCallState {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
}


export interface ChatProgressIndicatorProps {
  toolCalls: ToolCallState[];
  attachmentFilenames: Record<string, string>;
}


function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}


function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return truncate(url, 40);
  }
}


function labelFor(call: ToolCallState, filenames: Record<string, string>): string {
  const name = call.name;
  const args = call.args || {};

  if (name === "read_attachment") {
    const attId = String(args.attachment_id ?? "");
    const filename = filenames[attId];
    return filename ? `Reading ${filename}…` : "Reading attachment…";
  }
  if (name === "list_attachments") {
    return "Listing attachments…";
  }
  if (name === "web_search") {
    return "Searching the web…";
  }
  if (name === "web_fetch") {
    const url = String(args.url ?? "");
    return url ? `Fetching ${truncate(hostOf(url), 40)}…` : "Fetching…";
  }
  if (name.startsWith("mcp.perplexity")) {
    return "Asking Perplexity…";
  }
  return `Running ${name}…`;
}


function iconFor(name: string): string {
  if (name === "read_attachment" || name === "list_attachments") return "📎";
  if (name === "web_search" || name === "web_fetch") return "🌐";
  if (name.startsWith("mcp.perplexity")) return "🤖";
  return "⚙️";
}


function chipLabel(call: ToolCallState, filenames: Record<string, string>): string {
  const args = call.args || {};
  if (call.name === "read_attachment") {
    const attId = String(args.attachment_id ?? "");
    const filename = filenames[attId] ?? "attachment";
    return `read_attachment(${filename})`;
  }
  if (call.name === "list_attachments") {
    return "list_attachments";
  }
  if (call.name === "web_search") {
    const q = String(args.query ?? "");
    return q ? `web_search("${truncate(q, 30)}")` : "web_search";
  }
  if (call.name === "web_fetch") {
    const url = String(args.url ?? "");
    return url ? `web_fetch(${truncate(hostOf(url), 30)})` : "web_fetch";
  }
  return call.name;
}


function statusIcon(status: ToolCallState["status"]): string {
  if (status === "running") return "⟳";
  if (status === "done") return "✓";
  return "⚠";
}


export function ChatProgressIndicator({
  toolCalls,
  attachmentFilenames,
}: ChatProgressIndicatorProps) {
  const topLabel = useMemo(() => {
    // Most-recent running call wins. If none are running, fall back to "Thinking…".
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      if (toolCalls[i].status === "running") {
        return labelFor(toolCalls[i], attachmentFilenames);
      }
    }
    return "Thinking…";
  }, [toolCalls, attachmentFilenames]);

  return (
    <div className="grid grid-cols-[90px_1fr] gap-7 py-[22px] items-start relative">
      <span className="absolute top-[22px] left-[90px] -translate-x-1/2 w-2 h-px bg-accent" />

      <div className="text-right pr-3.5 border-r border-line pt-1">
        <span className="inline-block font-mono text-[9px] font-bold uppercase tracking-[0.16em] px-1.5 py-[2px] leading-[1.4] mb-1.5 text-accent border border-accent">
          K
        </span>
        <div className="font-display font-semibold text-[13px] text-text leading-tight [letter-spacing:-0.01em]">
          kona
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:300ms]" />
          </div>
          <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
            {topLabel}
          </span>
        </div>

        {toolCalls.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {toolCalls.map((c) => (
              <span
                key={c.id}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-line bg-accent/5 font-mono text-[10px] text-text"
                data-status={c.status}
                data-testid="progress-chip"
              >
                <span>{iconFor(c.name)}</span>
                <span>{chipLabel(c, attachmentFilenames)}</span>
                <span aria-label={c.status}>{statusIcon(c.status)}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run, verify PASS**

```bash
cd kc-dashboard && npm test -- ChatProgressIndicator --run 2>&1 | tail -15
```

Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-dashboard/src/components/ChatProgressIndicator.tsx \
        kc-dashboard/src/components/ChatProgressIndicator.test.tsx
git commit -m "feat(kc-dashboard): ChatProgressIndicator with stage labels + tool chips"
```

---

## Task 4: Wire `ChatProgressIndicator` into `Chat.tsx`

**Files:**
- Modify: `kc-dashboard/src/views/Chat.tsx`

- [ ] **Step 1: Read current Chat.tsx WS handler**

Read `/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-dashboard/src/views/Chat.tsx`, focusing on the area around the `send_stream` consumption (the supervisor sends `tool_call`, `tool_result`, `assistant_complete` events on the WS). Note where `<ThinkingIndicator />` is rendered today. Note the local state hooks defined at the top of the `Chat()` body.

- [ ] **Step 2: Add `turnToolCalls` state + filename map derivation**

Near the existing `useState` block, add:

```typescript
import { ChatProgressIndicator, type ToolCallState } from "../components/ChatProgressIndicator";
// (Place alongside the existing component imports at the top of the file.)

// Inside Chat() body, alongside the existing useState hooks:
const [turnToolCalls, setTurnToolCalls] = useState<ToolCallState[]>([]);
```

Derive `attachmentFilenames` — a lookup from `att_id` → filename — from two sources:

1. The currently-uploading chips (from `useAttachmentUpload` hook — already in scope as `chips`).
2. Past user messages in the conversation that already have `[attached: filename, ..., id=att_xxx]` chip lines.

Add a `useMemo` that builds the map:

```typescript
const attachmentFilenames = useMemo(() => {
  const out: Record<string, string> = {};
  // From in-flight upload chips (the hook).
  for (const c of chips) {
    if (c.attachmentId && c.filename) out[c.attachmentId] = c.filename;
  }
  // From past user messages — parse the [attached: ...] lines in their content.
  const msgs = msgsQ.data ?? [];
  for (const m of msgs) {
    if (m.role !== "user" || typeof m.content !== "string") continue;
    for (const line of m.content.split("\n")) {
      const match = /^\[attached:\s*([^,\]]+)(?:,[^\]]*)?,\s*id=(att_[a-f0-9]+)\]\s*$/.exec(line);
      if (match) {
        const [, filename, attId] = match;
        out[attId] = filename.trim();
      }
    }
  }
  return out;
}, [chips, msgsQ.data]);
```

(Adapt `msgsQ.data` to whatever the actual messages-query variable is in your local Chat.tsx — match the existing name; don't introduce a new one.)

- [ ] **Step 3: Update the WS frame handlers**

Find the existing `async for frame in rt.assembled.core_agent.send_stream(...)` analog — in Chat.tsx this is the WebSocket event handler that branches on `frame.type` or `event.type`. Locate the branches for `tool_call`, `tool_result`, and `assistant_complete`.

Wire the new state into each:

```typescript
// On a "tool_call" event:
setTurnToolCalls((prev) => [
  ...prev,
  { id: event.call.id, name: event.call.name, args: event.call.arguments ?? {}, status: "running" },
]);

// On a "tool_result" event:
setTurnToolCalls((prev) => prev.map((c) =>
  c.id === event.call_id
    ? { ...c, status: looksLikeError(event.content) ? "error" : "done" }
    : c,
));

// On an "assistant_complete" event:
setTurnToolCalls([]);
```

Where `looksLikeError` is a small inline helper:

```typescript
function looksLikeError(content: string): boolean {
  if (typeof content !== "string") return false;
  try {
    const parsed = JSON.parse(content);
    return typeof parsed === "object" && parsed !== null && "error" in parsed;
  } catch {
    return false;
  }
}
```

Add `looksLikeError` near the top of the Chat.tsx module (above the component) or inline once.

If the user sends a new message before `assistant_complete` (e.g., interrupts and re-sends), also clear `turnToolCalls` at the start of the send-message handler. Add `setTurnToolCalls([])` at the same spot you call `clearChips()`.

- [ ] **Step 4: Swap the indicator**

Find the line that renders `<ThinkingIndicator />` (or `<ThinkingIndicator label="..." />`). Replace with:

```tsx
<ChatProgressIndicator
  toolCalls={turnToolCalls}
  attachmentFilenames={attachmentFilenames}
/>
```

Remove the now-orphan `import { ThinkingIndicator } from "../components/ThinkingIndicator";` line.

- [ ] **Step 5: Run dashboard tests**

```bash
cd kc-dashboard && npm test --run 2>&1 | tail -15
```

Expected: all tests PASS. Pre-existing Chat.test.tsx flakiness (the 3 known failures from prior phases) is acceptable; new failures introduced by this task are not.

- [ ] **Step 6: TypeScript check**

```bash
cd kc-dashboard && npx tsc --noEmit -p tsconfig.json 2>&1 | tail -10
```

Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add kc-dashboard/src/views/Chat.tsx
git commit -m "feat(kc-dashboard): wire ChatProgressIndicator into Chat (tool-call state + filename map)"
```

---

## Task 5: Delete `ThinkingIndicator`

**Files:**
- Delete: `kc-dashboard/src/components/ThinkingIndicator.tsx`
- Delete: `kc-dashboard/src/components/ThinkingIndicator.test.tsx` (if it exists)

- [ ] **Step 1: Confirm nothing else imports ThinkingIndicator**

```bash
cd kc-dashboard && grep -r "ThinkingIndicator" src --include="*.ts" --include="*.tsx" 2>&1 | head -5
```

Expected: no matches (Task 4 removed the only import).

If anything still references it, fix that consumer before deleting (it likely also wants the new indicator, but match the local need).

- [ ] **Step 2: Delete the files**

```bash
rm kc-dashboard/src/components/ThinkingIndicator.tsx
# Only if the test file exists:
[ -f kc-dashboard/src/components/ThinkingIndicator.test.tsx ] && rm kc-dashboard/src/components/ThinkingIndicator.test.tsx
```

- [ ] **Step 3: Run dashboard tests + tsc**

```bash
cd kc-dashboard && npm test --run 2>&1 | tail -10
cd kc-dashboard && npx tsc --noEmit -p tsconfig.json 2>&1 | tail -5
```

Expected: all PASS, no tsc errors.

- [ ] **Step 4: Commit**

```bash
git add -u kc-dashboard/src/components/
git commit -m "chore(kc-dashboard): remove ThinkingIndicator (replaced by ChatProgressIndicator)"
```

---

## Task 6: Final sweep + manual verification

- [ ] **Step 1: Full kc-core suite**

```bash
cd kc-core && pytest -v 2>&1 | tail -10
```

Expected: 90 PASS.

- [ ] **Step 2: Full kc-dashboard suite**

```bash
cd kc-dashboard && npm test --run 2>&1 | tail -10
```

Expected: ~140 PASS (previous ~133 + 7 new). The 3 pre-existing Chat.test.tsx flakes are tolerated.

- [ ] **Step 3: Manual smoke (instructions for Sammy)**

These manual gates don't need a separate SMOKE doc — the value here is small enough that visual confirmation in one chat is sufficient.

After restarting KonaClawDashboard:

1. **Cold load:** open a fresh Kona chat. First message TTFB should still be ~30-60s (model cold-load).
2. **Warm load:** send a second message within a minute. TTFB should drop to single digits.
3. **Progress label — `read_attachment`:** drag a `.txt` into the chat, ask "what's in this file?". Watch the indicator: should show `Reading <filename>…` while Kona reads the attachment, then a `📎 read_attachment(<filename>) ✓` chip.
4. **Progress label — `web_search`:** ask "search the web for today's news". Indicator should show `Searching the web…` plus a `🌐 web_search("…") ✓` chip.
5. **Multiple chips persist:** if Kona makes two tool calls in one turn, both chips appear and survive until the assistant reply is complete.

If any gate fails: check the dashboard browser DevTools console for React warnings, and the supervisor stderr for unexpected frames. The most likely failure mode is a frame-shape mismatch between Chat.tsx and the WS event payload — re-inspect the event types and adjust the destructuring.

- [ ] **Step 4: Optional commit if any tidy-up files remain**

```bash
git status
# If anything's staged, commit. Otherwise skip.
```

---

## Notes for the executor

- **TDD discipline:** Task 2 and Task 3 follow the standard RED → GREEN cycle. Run the failing-test step and observe the error message before writing the implementation — that's the only proof the test actually exercises the change.
- **Don't introduce a SMOKE doc.** This phase is small enough that the inline manual checks in Task 6 are sufficient. A formal SMOKE doc is overkill.
- **`keep_alive` is a one-line change duplicated across two paths.** Don't refactor `_chat_stream_openai` and `_chat_stream_native` to share a body builder — the file already isolates them by endpoint shape, and a one-line dup is cheaper than the abstraction.
- **`ChatProgressIndicator` reuses the existing pulsing-dot CSS.** Don't redesign the dot animation; the spec explicitly preserves it.
- **No supervisor changes.** If during execution you find yourself opening a file in `kc-supervisor/`, stop — the spec is wrong or you've wandered off scope.
