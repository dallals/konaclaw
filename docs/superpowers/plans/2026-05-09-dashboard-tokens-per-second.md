# Dashboard tokens-per-second metric — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface live and per-reply tokens/sec + TTFB in the chat header and per-bubble in the dashboard, sourced from upstream provider `usage` objects via `kc-core`, aggregated per user-turn in `kc-supervisor`, persisted to SQLite, and rendered live in `kc-dashboard`.

**Architecture:** New `ChatUsage` (wire-level) and `TurnUsage` (agent-level) stream frames in `kc-core`. `OllamaClient` requests `stream_options.include_usage=true` and yields `ChatUsage` after `Done`. `Agent.send_stream` forwards each as `TurnUsage(call_index)`. The supervisor aggregates per turn, emits one new `{type:"usage"}` WS frame before `assistant_complete`, and persists the aggregate to a new `usage_json` column. The dashboard renders a live char-based estimate during streaming and snaps to authoritative numbers on the `usage` event.

**Tech Stack:** Python 3.12, `httpx`/`respx` for SSE testing, `pytest-asyncio`, FastAPI, SQLite (additive migration), React 18 + Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-09-dashboard-tokens-per-second-design.md`

---

## File Structure

**New files:**
- _(none — all changes extend existing files)_

**Modified files:**
- `kc-core/src/kc_core/stream_frames.py` — add `ChatUsage`, `TurnUsage` dataclasses; extend `ChatStreamFrame`, `StreamFrame` unions
- `kc-core/src/kc_core/ollama_client.py` — request `stream_options.include_usage=true`; capture `usage` chunk; wall-clock timing; emit `ChatUsage` after `Done`
- `kc-core/src/kc_core/agent.py` — track `call_index`; forward each `ChatUsage` as `TurnUsage`
- `kc-core/tests/test_stream_frames.py` — frame construction tests
- `kc-core/tests/test_ollama_client.py` — usage capture, no-usage fallback, garbage-value fallback
- `kc-core/tests/test_agent.py` — multi-call `TurnUsage` forwarding
- `kc-supervisor/src/kc_supervisor/storage.py` — additive `usage_json` column migration; `append_message` accepts optional `usage_json`
- `kc-supervisor/src/kc_supervisor/conversations.py` — `append()` accepts optional `usage` dict for `AssistantMessage`; `list_messages` returns rows including `usage`
- `kc-supervisor/src/kc_supervisor/http_routes.py` — `_message_to_dict` echoes `usage` through to JSON
- `kc-supervisor/src/kc_supervisor/ws_routes.py` — per-turn aggregator; emits `{type:"usage"}` WS frame before `assistant_complete`; persists aggregate
- `kc-supervisor/src/kc_supervisor/inbound.py` — same aggregator; persists aggregate (no WS emission — channel side has no WS)
- `kc-supervisor/tests/test_storage.py` — migration + persistence
- `kc-supervisor/tests/test_ws_chat.py` — WS frame emission, error path drops, partial reporting
- `kc-supervisor/tests/test_inbound.py` — persistence on inbound (Telegram) path
- `kc-supervisor/tests/test_conversations.py` — `append()` with usage; `list_messages` returns usage
- `kc-supervisor/tests/test_http.py` — `/conversations/{cid}/messages` echoes usage
- `kc-dashboard/src/ws/useChatSocket.ts` — extend `ChatEvent` union with `usage`
- `kc-dashboard/src/views/Chat.tsx` — track `currentTurnUsage`; pass `usage` to `MessageBubble`; render `Last reply` + `TTFB` rows; live-ticker hook
- `kc-dashboard/src/components/MessageBubble.tsx` — accept optional `usage` prop; render badge footer
- `kc-dashboard/tests/views/Chat.test.tsx` — header strip rendering across happy-path, no-usage, tool-only
- `kc-dashboard/tests/components/MessageBubble.test.tsx` — _new_ — badge formatting tests
- `kc-dashboard/SMOKE.md` — manual gates
- `kc-supervisor/SMOKE.md` — manual gates

---

## Task 1: Add `ChatUsage` and `TurnUsage` stream frames in kc-core

**Files:**
- Modify: `kc-core/src/kc_core/stream_frames.py`
- Test: `kc-core/tests/test_stream_frames.py`

- [ ] **Step 1: Write the failing tests**

Add to `kc-core/tests/test_stream_frames.py`:

```python
from kc_core.stream_frames import ChatUsage, TurnUsage


def test_chat_usage_frame_default_fields():
    u = ChatUsage(
        input_tokens=120,
        output_tokens=42,
        ttfb_ms=314.5,
        generation_ms=1280.0,
        usage_reported=True,
    )
    assert u.input_tokens == 120
    assert u.output_tokens == 42
    assert u.ttfb_ms == 314.5
    assert u.generation_ms == 1280.0
    assert u.usage_reported is True


def test_turn_usage_frame_carries_call_index():
    u = TurnUsage(
        call_index=1,
        input_tokens=300,
        output_tokens=12,
        ttfb_ms=80.0,
        generation_ms=110.0,
        usage_reported=False,
    )
    assert u.call_index == 1
    assert u.usage_reported is False


def test_chat_usage_is_chat_stream_frame():
    from kc_core.stream_frames import ChatStreamFrame
    # Type-system check: assignable to ChatStreamFrame union
    f: ChatStreamFrame = ChatUsage(0, 0, 0.0, 0.0, False)
    assert f is not None


def test_turn_usage_is_stream_frame():
    from kc_core.stream_frames import StreamFrame
    f: StreamFrame = TurnUsage(0, 0, 0, 0.0, 0.0, False)
    assert f is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-core && uv run pytest tests/test_stream_frames.py -v`
Expected: FAIL with `ImportError: cannot import name 'ChatUsage'` (and `TurnUsage`).

- [ ] **Step 3: Add the dataclasses**

Edit `kc-core/src/kc_core/stream_frames.py`. After the existing `Done` class, add:

```python
@dataclass(frozen=True)
class ChatUsage:
    """Per-chat_stream-call usage. Yielded after Done.

    `usage_reported=False` means the upstream provider did not include a usable
    `usage` object — caller should treat token counts as unknown but durations
    are still wall-clocked and valid.
    """
    input_tokens: int
    output_tokens: int
    ttfb_ms: float
    generation_ms: float
    usage_reported: bool
```

Update `ChatStreamFrame` union:

```python
ChatStreamFrame = Union[TextDelta, ToolCallsBlock, Done, ChatUsage]
```

After the existing `Complete` class, add:

```python
@dataclass(frozen=True)
class TurnUsage:
    """Agent-level usage frame, one per inner chat_stream call within a single send_stream.

    `call_index` starts at 0 for the first model call of the turn and increments
    for each subsequent call (multi-step tool-using turns).
    """
    call_index: int
    input_tokens: int
    output_tokens: int
    ttfb_ms: float
    generation_ms: float
    usage_reported: bool
```

Update `StreamFrame` union:

```python
StreamFrame = Union[TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-core && uv run pytest tests/test_stream_frames.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd kc-core
git add src/kc_core/stream_frames.py tests/test_stream_frames.py
git commit -m "feat(kc-core): add ChatUsage and TurnUsage stream frames"
```

---

## Task 2: `OllamaClient.chat_stream` requests `include_usage` and emits `ChatUsage` (happy path)

**Files:**
- Modify: `kc-core/src/kc_core/ollama_client.py`
- Test: `kc-core/tests/test_ollama_client.py`

- [ ] **Step 1: Write the failing test for usage capture**

Add to `kc-core/tests/test_ollama_client.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_when_provider_reports():
    from kc_core.stream_frames import TextDelta, Done, ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":17,"completion_tokens":4,"total_tokens":21}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)
    # Expect: TextDelta, TextDelta, Done, ChatUsage
    types = [type(f).__name__ for f in frames]
    assert types == ["TextDelta", "TextDelta", "Done", "ChatUsage"]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.input_tokens == 17
    assert usage.output_tokens == 4
    assert usage.usage_reported is True
    assert usage.ttfb_ms >= 0.0
    assert usage.generation_ms >= 0.0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_request_body_includes_stream_options():
    sse = (
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    captured = {}
    def _capture(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return Response(200, content=sse, headers={"content-type": "text/event-stream"})
    respx.post("http://localhost:11434/v1/chat/completions").mock(side_effect=_capture)
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    async for _ in client.chat_stream(messages=[], tools=[]):
        pass
    assert captured["body"].get("stream_options") == {"include_usage": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-core && uv run pytest tests/test_ollama_client.py::test_chat_stream_emits_chat_usage_when_provider_reports tests/test_ollama_client.py::test_chat_stream_request_body_includes_stream_options -v`
Expected: FAIL — `stream_options` not in body; `ChatUsage` not yielded.

- [ ] **Step 3: Update `OllamaClient.chat_stream`**

Edit `kc-core/src/kc_core/ollama_client.py`. In the `chat_stream` method:

Replace the existing body construction:

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

Add `import time` at top of file (next to `import json`).

Inside `chat_stream`, before the `async with httpx.AsyncClient(...)` block, add timing/state vars:

```python
        import time
        t_request_start = time.monotonic()
        t_first_byte: float | None = None
        usage_obj: dict[str, Any] | None = None
        done_emitted = False
```

Update the SSE loop. Around the existing text-yield block (where `text = delta.get("content")` and `if text:`), update to capture `t_first_byte`:

```python
                    text = delta.get("content")
                    if text:
                        if t_first_byte is None:
                            t_first_byte = time.monotonic()
                        yield TextDelta(content=text)
```

Inside the SSE loop, just after parsing `chunk = json.loads(payload)`, add a top-level `usage` capture (alongside the `choices`/`delta` extraction):

```python
                    if chunk.get("usage"):
                        usage_obj = chunk["usage"]
```

Replace the existing `Done` emission block at the end of the per-chunk loop. Currently it does `yield Done(...); return`. Change to:

```python
                    if finish_reason and not done_emitted:
                        if tool_call_frags:
                            calls = []
                            for idx in sorted(tool_call_frags.keys()):
                                slot = tool_call_frags[idx]
                                args_str = slot["arguments_str"] or "{}"
                                try:
                                    args = json.loads(args_str)
                                except json.JSONDecodeError:
                                    args = {}
                                calls.append({
                                    "id": slot["id"] or f"call_{idx}",
                                    "name": slot["name"],
                                    "arguments": args,
                                })
                            yield ToolCallsBlock(calls=calls)
                            tool_call_frags.clear()
                        yield Done(finish_reason=finish_reason)
                        done_emitted = True
                        # do NOT return — usage chunk may follow
```

After the `async for line in r.aiter_lines():` loop exits naturally (i.e., after `[DONE]` is seen), emit `ChatUsage`. Add this immediately after the `aiter_lines` loop, still inside the `r.aread()` `async with`:

```python
                # End of stream — emit ChatUsage with whatever timing we have.
                t_done = time.monotonic()
                if t_first_byte is None:
                    # No text was produced (tool-only turn or hard error after open)
                    t_first_byte = t_done
                ttfb_ms = (t_first_byte - t_request_start) * 1000.0
                gen_ms = (t_done - t_first_byte) * 1000.0
                usage_reported = False
                input_tokens = 0
                output_tokens = 0
                if usage_obj:
                    pt = usage_obj.get("prompt_tokens")
                    ct = usage_obj.get("completion_tokens")
                    if isinstance(pt, int) and pt >= 0 and isinstance(ct, int) and ct >= 0:
                        input_tokens = pt
                        output_tokens = ct
                        usage_reported = True
                from kc_core.stream_frames import ChatUsage
                yield ChatUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    ttfb_ms=ttfb_ms,
                    generation_ms=gen_ms,
                    usage_reported=usage_reported,
                )
```

Note: the existing `[DONE]` `break` exits the inner `aiter_lines` loop but stays inside the `async with` blocks, which is what we want.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-core && uv run pytest tests/test_ollama_client.py -v`
Expected: all existing tests still pass; the two new tests pass.

- [ ] **Step 5: Commit**

```bash
cd kc-core
git add src/kc_core/ollama_client.py tests/test_ollama_client.py
git commit -m "feat(kc-core): emit ChatUsage from OllamaClient with include_usage"
```

---

## Task 3: `OllamaClient` fallback when no usage / garbage usage

**Files:**
- Test: `kc-core/tests/test_ollama_client.py`

The validation logic from Task 2 should already cover this; this task adds explicit tests for both fallback paths.

- [ ] **Step 1: Write the failing tests**

Add to `kc-core/tests/test_ollama_client.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_when_provider_silent():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.usage_reported is False
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.ttfb_ms >= 0.0
    assert usage.generation_ms >= 0.0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_treats_garbage_usage_as_unreported():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":-3,"completion_tokens":4}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = frames[-1]
    assert isinstance(usage, ChatUsage)
    assert usage.usage_reported is False
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_chat_usage_for_tool_only_turn():
    from kc_core.stream_frames import ChatUsage
    sse = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"echo","arguments":"{}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":8}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    client = OllamaClient(base_url="http://localhost:11434", model="gemma3:4b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    usage = [f for f in frames if isinstance(f, ChatUsage)][0]
    assert usage.usage_reported is True
    assert usage.input_tokens == 50
    assert usage.output_tokens == 8
    # tool-only turn: no text was emitted, so generation_ms should be 0.0
    assert usage.generation_ms == 0.0
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd kc-core && uv run pytest tests/test_ollama_client.py -v`
Expected: all three new tests pass (the validation logic in Task 2 should already handle these).

- [ ] **Step 3: Commit**

```bash
cd kc-core
git add tests/test_ollama_client.py
git commit -m "test(kc-core): cover usage fallback and tool-only ChatUsage paths"
```

---

## Task 4: `Agent.send_stream` forwards `ChatUsage` as `TurnUsage` with call_index

**Files:**
- Modify: `kc-core/src/kc_core/agent.py`
- Test: `kc-core/tests/test_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `kc-core/tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_send_stream_forwards_turn_usage_per_call(monkeypatch):
    from kc_core.agent import Agent
    from kc_core.tools import ToolRegistry
    from kc_core.stream_frames import (
        TextDelta, ToolCallsBlock, Done, ChatUsage,
        TurnUsage, Complete,
    )

    class FakeClient:
        model = "fake"
        def __init__(self):
            self._call = 0
        async def chat(self, messages, tools):
            raise NotImplementedError
        async def chat_stream(self, messages, tools):
            self._call += 1
            if self._call == 1:
                # First call: tool call + usage
                yield ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"x": 1}}])
                yield Done(finish_reason="tool_calls")
                yield ChatUsage(input_tokens=120, output_tokens=8, ttfb_ms=50.0, generation_ms=10.0, usage_reported=True)
            else:
                # Second call: text + usage
                yield TextDelta(content="ok")
                yield Done(finish_reason="stop")
                yield ChatUsage(input_tokens=140, output_tokens=2, ttfb_ms=60.0, generation_ms=8.0, usage_reported=True)

    tools = ToolRegistry()
    @tools.tool
    def echo(x: int) -> int:
        """echoes x"""
        return x

    agent = Agent(name="t", client=FakeClient(), system_prompt="", tools=tools)
    frames = [f async for f in agent.send_stream("hi")]
    turn_usages = [f for f in frames if isinstance(f, TurnUsage)]
    assert [u.call_index for u in turn_usages] == [0, 1]
    assert turn_usages[0].input_tokens == 120
    assert turn_usages[1].output_tokens == 2
    # Complete still terminal
    assert isinstance(frames[-1], Complete)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kc-core && uv run pytest tests/test_agent.py::test_send_stream_forwards_turn_usage_per_call -v`
Expected: FAIL — `TurnUsage` not yielded.

- [ ] **Step 3: Update `Agent.send_stream`**

Edit `kc-core/src/kc_core/agent.py`:

Add to the import list at the top:

```python
from kc_core.stream_frames import (
    ChatStreamFrame, TextDelta, ToolCallsBlock, Done, ChatUsage,
    StreamFrame, TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage,
)
```

Inside `send_stream`, replace the existing `for _ in range(self.max_tool_iterations + 1):` loop body's stream-draining block to track call_index and forward `ChatUsage`:

```python
        call_index = 0
        for _ in range(self.max_tool_iterations + 1):
            wire = self._build_wire_messages()
            text_parts: list[str] = []
            tool_calls_block: list[dict[str, Any]] | None = None

            # Drain one model turn from chat_stream
            async for cs_frame in self.client.chat_stream(messages=wire, tools=self.tools.to_openai_schema()):
                if isinstance(cs_frame, TextDelta):
                    text_parts.append(cs_frame.content)
                    yield TokenDelta(content=cs_frame.content)
                elif isinstance(cs_frame, ToolCallsBlock):
                    tool_calls_block = cs_frame.calls
                elif isinstance(cs_frame, Done):
                    pass
                elif isinstance(cs_frame, ChatUsage):
                    yield TurnUsage(
                        call_index=call_index,
                        input_tokens=cs_frame.input_tokens,
                        output_tokens=cs_frame.output_tokens,
                        ttfb_ms=cs_frame.ttfb_ms,
                        generation_ms=cs_frame.generation_ms,
                        usage_reported=cs_frame.usage_reported,
                    )
            call_index += 1
```

(Keep the rest of the method body unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-core && uv run pytest tests/test_agent.py -v`
Expected: all existing tests still pass; the new test passes.

- [ ] **Step 5: Commit**

```bash
cd kc-core
git add src/kc_core/agent.py tests/test_agent.py
git commit -m "feat(kc-core): Agent.send_stream forwards ChatUsage as TurnUsage with call_index"
```

---

## Task 5: SQLite migration — add `usage_json` column to `messages`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Test: `kc-supervisor/tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `kc-supervisor/tests/test_storage.py`:

```python
def test_messages_table_has_usage_json_column(tmp_path):
    from kc_supervisor.storage import Storage
    s = Storage(tmp_path / "kc.db")
    s.init()
    with s.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    assert "usage_json" in cols


def test_append_message_accepts_usage_json(tmp_path):
    from kc_supervisor.storage import Storage
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.append_message(cid, "assistant", "hello", None, usage_json='{"output_tokens":4}')
    rows = s.list_messages(cid)
    assert rows[0]["usage_json"] == '{"output_tokens":4}'


def test_legacy_message_returns_none_usage_json(tmp_path):
    from kc_supervisor.storage import Storage
    s = Storage(tmp_path / "kc.db")
    s.init()
    cid = s.create_conversation(agent="kona", channel="dashboard")
    s.append_message(cid, "user", "hi", None)  # no usage_json kw
    rows = s.list_messages(cid)
    assert rows[0]["usage_json"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_storage.py -v -k usage_json`
Expected: FAIL — column missing; `append_message` rejects unknown kwarg.

- [ ] **Step 3: Update schema and `append_message`**

Edit `kc-supervisor/src/kc_supervisor/storage.py`:

Update the `messages` `CREATE TABLE` block in `SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_json TEXT,
    usage_json TEXT,
    ts REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
```

In `Storage.init()`, after the existing `if "title" not in cols` migration block for conversations, add a migration block for messages:

```python
            msg_cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
            if "usage_json" not in msg_cols:
                c.execute("ALTER TABLE messages ADD COLUMN usage_json TEXT")
```

Update `Storage.append_message`:

```python
    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: Optional[str],
        tool_call_json: Optional[str],
        usage_json: Optional[str] = None,
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_call_json, usage_json, ts) "
                "VALUES (?,?,?,?,?,?)",
                (conversation_id, role, content, tool_call_json, usage_json, time.time()),
            )
            return int(cur.lastrowid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_storage.py -v`
Expected: all existing tests still pass; the three new tests pass.

- [ ] **Step 5: Commit**

```bash
cd kc-supervisor
git add src/kc_supervisor/storage.py tests/test_storage.py
git commit -m "feat(kc-supervisor): add usage_json column to messages with additive migration"
```

---

## Task 6: `ConversationManager.append` accepts optional `usage` for AssistantMessage; `list_messages` returns usage on rows

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/conversations.py`
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Test: `kc-supervisor/tests/test_conversations.py`
- Test: `kc-supervisor/tests/test_http.py`

`ConversationManager.list_messages` currently returns a `list[Message]` (not dicts). The dashboard reads via the HTTP route `/conversations/{cid}/messages`, which serializes via `_message_to_dict`. To carry `usage` through, we add a sibling method `list_messages_with_meta` returning `list[tuple[Message, dict|None]]`, and update the HTTP route to use it.

- [ ] **Step 1: Write the failing tests**

Add to `kc-supervisor/tests/test_conversations.py`:

```python
def test_append_assistant_persists_usage(tmp_path):
    import json
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    from kc_core.messages import AssistantMessage
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(s)
    cid = cm.start("kona", "dashboard")
    cm.append(cid, AssistantMessage(content="hi"), usage={"output_tokens": 4, "ttfb_ms": 80.0})
    rows = s.list_messages(cid)
    assert rows[0]["role"] == "assistant"
    assert json.loads(rows[0]["usage_json"]) == {"output_tokens": 4, "ttfb_ms": 80.0}


def test_list_messages_with_meta_returns_usage(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    from kc_core.messages import UserMessage, AssistantMessage
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(s)
    cid = cm.start("kona", "dashboard")
    cm.append(cid, UserMessage(content="hi"))
    cm.append(cid, AssistantMessage(content="hello"), usage={"output_tokens": 1})
    pairs = cm.list_messages_with_meta(cid)
    assert len(pairs) == 2
    msg0, meta0 = pairs[0]
    assert isinstance(msg0, UserMessage)
    assert meta0 is None
    msg1, meta1 = pairs[1]
    assert isinstance(msg1, AssistantMessage)
    assert meta1 == {"output_tokens": 1}
```

Add to `kc-supervisor/tests/test_http.py` (mirror `test_list_messages_for_conversation` and `test_audit_endpoint` which both use the `app` and `deps` fixtures):

```python
def test_list_messages_route_echoes_usage(app, deps):
    from kc_core.messages import AssistantMessage
    cid = deps.conversations.start("alice", "dashboard")
    deps.conversations.append(
        cid, AssistantMessage(content="hi"),
        usage={"output_tokens": 4, "ttfb_ms": 50.0, "generation_ms": 100.0,
               "input_tokens": 10, "calls": 1, "usage_reported": True},
    )
    with TestClient(app) as client:
        r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assistant = [m for m in msgs if m["type"] == "AssistantMessage"][0]
    assert assistant["usage"] == {
        "output_tokens": 4, "ttfb_ms": 50.0, "generation_ms": 100.0,
        "input_tokens": 10, "calls": 1, "usage_reported": True,
    }


def test_list_messages_route_omits_usage_for_legacy_rows(app, deps):
    cid = deps.conversations.start("alice", "dashboard")
    # Append directly via storage with no usage_json — simulates pre-migration data.
    deps.storage.append_message(cid, "assistant", "legacy", None)
    with TestClient(app) as client:
        r = client.get(f"/conversations/{cid}/messages")
    msgs = r.json()["messages"]
    assistant = [m for m in msgs if m["type"] == "AssistantMessage"][0]
    assert "usage" not in assistant
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
cd kc-supervisor && uv run pytest tests/test_conversations.py tests/test_http.py -v -k "usage or with_meta"
```
Expected: FAIL — `append` doesn't accept `usage`; `list_messages_with_meta` doesn't exist; HTTP route doesn't return `usage`.

- [ ] **Step 3: Update `ConversationManager`**

Edit `kc-supervisor/src/kc_supervisor/conversations.py`:

Update `append` to accept optional `usage`:

```python
    def append(
        self,
        conversation_id: int,
        msg: Message,
        usage: Optional[dict] = None,
    ) -> int:
        if isinstance(msg, UserMessage):
            return self.s.append_message(conversation_id, "user", msg.content, None)
        if isinstance(msg, AssistantMessage):
            usage_json = json.dumps(usage) if usage is not None else None
            return self.s.append_message(
                conversation_id, "assistant", msg.content, None, usage_json=usage_json,
            )
        if isinstance(msg, ToolCallMessage):
            payload = json.dumps({
                "tool_call_id": msg.tool_call_id,
                "tool_name": msg.tool_name,
                "arguments": msg.arguments,
            })
            return self.s.append_message(conversation_id, "tool_call", None, payload)
        if isinstance(msg, ToolResultMessage):
            payload = json.dumps({
                "tool_call_id": msg.tool_call_id,
                "content": msg.content,
            })
            return self.s.append_message(conversation_id, "tool_result", None, payload)
        raise TypeError(f"unknown message type: {type(msg)}")
```

Add `from typing import Optional` to the imports if not present.

Add a new method `list_messages_with_meta`:

```python
    def list_messages_with_meta(self, conversation_id: int) -> list[tuple[Message, Optional[dict]]]:
        """Like list_messages, but also returns the parsed usage dict for AssistantMessage rows."""
        out: list[tuple[Message, Optional[dict]]] = []
        for row in self.s.list_messages(conversation_id):
            role = row["role"]
            usage = None
            if role == "user":
                msg: Message = UserMessage(content=row["content"] or "")
            elif role == "assistant":
                msg = AssistantMessage(content=row["content"] or "")
                if row.get("usage_json"):
                    try:
                        usage = json.loads(row["usage_json"])
                    except json.JSONDecodeError:
                        usage = None
            elif role == "tool_call":
                d = json.loads(row["tool_call_json"])
                msg = ToolCallMessage(
                    tool_call_id=d["tool_call_id"],
                    tool_name=d["tool_name"],
                    arguments=d["arguments"],
                )
            elif role == "tool_result":
                d = json.loads(row["tool_call_json"])
                msg = ToolResultMessage(
                    tool_call_id=d["tool_call_id"],
                    content=d["content"],
                )
            else:
                raise ValueError(f"unknown role in storage: {role!r}")
            out.append((msg, usage))
        return out
```

Note: `sqlite3.Row` supports `.get()` only via membership; use `row["usage_json"] if "usage_json" in row.keys() else None` if `.get` fails. Verify by running tests.

If `Row.get` doesn't work, replace `row.get("usage_json")` with:

```python
                uj = row["usage_json"] if "usage_json" in row.keys() else None
                if uj:
                    ...
```

- [ ] **Step 4: Update HTTP route to echo `usage`**

Edit `kc-supervisor/src/kc_supervisor/http_routes.py`:

Replace the `_message_to_dict` helper and the `list_messages` route:

```python
def _message_to_dict(m, usage: Optional[dict] = None) -> dict:
    """Serialize a kc_core.messages dataclass for JSON. Optionally includes usage."""
    d = {"type": m.__class__.__name__, **asdict(m)}
    if usage is not None:
        d["usage"] = usage
    return d
```

(Add `from typing import Optional` import if not already present.)

Update the route:

```python
    @app.get("/conversations/{cid}/messages")
    def list_messages(cid: int):
        if app.state.deps.conversations.s.get_conversation(cid) is None:
            raise HTTPException(404, "conversation not found")
        pairs = app.state.deps.conversations.list_messages_with_meta(cid)
        return {"messages": [_message_to_dict(m, usage=u) for (m, u) in pairs]}
```

(Match the existing 404 handling shape; if the existing route uses a different idiom, follow that idiom.)

- [ ] **Step 5: Run tests to verify they pass**

Run:
```
cd kc-supervisor && uv run pytest tests/test_conversations.py tests/test_http.py -v
```
Expected: all existing tests still pass; the new tests pass.

- [ ] **Step 6: Commit**

```bash
cd kc-supervisor
git add src/kc_supervisor/conversations.py src/kc_supervisor/http_routes.py tests/test_conversations.py tests/test_http.py
git commit -m "feat(kc-supervisor): persist and expose per-message usage on AssistantMessage rows"
```

---

## Task 7: Per-turn usage aggregator + `{type:"usage"}` WS frame in `ws_routes.py`

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py`
- Test: `kc-supervisor/tests/test_ws_chat.py`

- [ ] **Step 1: Write the failing tests**

Add to `kc-supervisor/tests/test_ws_chat.py`. The file already has a `fake_client_factory` fixture and an `_inject_fake` helper (defined at the top). The tests below build on those — but `TurnUsage` is an *agent-level* frame (yielded by `Agent.send_stream`), not a *wire-level* frame consumed by `chat_stream`. To inject `TurnUsage` we replace the agent's `send_stream` directly rather than swapping the chat client. Add a small helper at the top of the new test block:

```python
def _inject_send_stream(deps, agent_name: str, frames):
    """Replace core_agent.send_stream so it yields the given frames once."""
    async def _gen(_content):
        for f in frames:
            yield f
    rt = deps.registry.get(agent_name)
    assert rt.assembled is not None
    rt.assembled.core_agent.send_stream = _gen
```

Then the three tests:

```python
def test_ws_chat_emits_usage_frame_aggregated_across_calls(app, deps):
    import json
    from kc_core.messages import AssistantMessage
    from kc_core.stream_frames import (
        TokenDelta, ToolCallStart, ToolResult, TurnUsage, Complete,
    )

    frames = [
        TokenDelta(content="h"),
        TokenDelta(content="i"),
        TurnUsage(call_index=0, input_tokens=100, output_tokens=5,
                  ttfb_ms=50.0, generation_ms=100.0, usage_reported=True),
        ToolCallStart(call={"id": "c1", "name": "file.list", "arguments": {}}),
        ToolResult(call_id="c1", content="[]"),
        TokenDelta(content=" done"),
        TurnUsage(call_index=1, input_tokens=120, output_tokens=3,
                  ttfb_ms=60.0, generation_ms=80.0, usage_reported=True),
        Complete(reply=AssistantMessage(content="hi done")),
    ]
    _inject_send_stream(deps, "alice", frames)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        seen = []
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            while True:
                m = ws.receive_json()
                seen.append(m)
                if m["type"] == "assistant_complete":
                    break

    types = [m["type"] for m in seen]
    assert "usage" in types
    assert types.index("usage") < types.index("assistant_complete")
    usage = next(m for m in seen if m["type"] == "usage")
    assert usage["input_tokens"] == 220
    assert usage["output_tokens"] == 8
    assert usage["ttfb_ms"] == 50.0      # first TurnUsage's ttfb_ms
    assert usage["generation_ms"] == 180.0
    assert usage["calls"] == 2
    assert usage["usage_reported"] is True

    rows = deps.storage.list_messages(cid)
    asst = [r for r in rows if r["role"] == "assistant"][-1]
    parsed = json.loads(asst["usage_json"])
    assert parsed == {
        "input_tokens": 220,
        "output_tokens": 8,
        "ttfb_ms": 50.0,
        "generation_ms": 180.0,
        "calls": 2,
        "usage_reported": True,
    }


def test_ws_chat_no_usage_frame_when_stream_errors_mid_turn(app, deps):
    from kc_core.stream_frames import TokenDelta, TurnUsage

    async def _gen(_content):
        yield TokenDelta(content="partial")
        yield TurnUsage(call_index=0, input_tokens=10, output_tokens=2,
                        ttfb_ms=20.0, generation_ms=30.0, usage_reported=True)
        raise RuntimeError("upstream went away")

    rt = deps.registry.get("alice")
    rt.assembled.core_agent.send_stream = _gen

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        seen = []
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            try:
                while True:
                    m = ws.receive_json()
                    seen.append(m)
                    if m["type"] in ("assistant_complete", "error"):
                        break
            except Exception:
                pass

    types = [m["type"] for m in seen]
    assert "usage" not in types
    rows = deps.storage.list_messages(cid)
    assert all(r["role"] != "assistant" for r in rows)


def test_ws_chat_partial_reporting_yields_null_token_counts(app, deps):
    from kc_core.messages import AssistantMessage
    from kc_core.stream_frames import TokenDelta, TurnUsage, Complete

    frames = [
        TokenDelta(content="h"),
        TurnUsage(call_index=0, input_tokens=100, output_tokens=5,
                  ttfb_ms=40.0, generation_ms=80.0, usage_reported=True),
        TokenDelta(content="i"),
        TurnUsage(call_index=1, input_tokens=0, output_tokens=0,
                  ttfb_ms=20.0, generation_ms=10.0, usage_reported=False),
        Complete(reply=AssistantMessage(content="hi")),
    ]
    _inject_send_stream(deps, "alice", frames)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            usage = None
            while True:
                m = ws.receive_json()
                if m["type"] == "usage":
                    usage = m
                if m["type"] == "assistant_complete":
                    break

    assert usage is not None
    assert usage["input_tokens"] is None
    assert usage["output_tokens"] is None
    assert usage["generation_ms"] == 90.0
    assert usage["ttfb_ms"] == 40.0
    assert usage["calls"] == 2
    assert usage["usage_reported"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && uv run pytest tests/test_ws_chat.py -v -k usage`
Expected: FAIL — `usage` frame not emitted.

- [ ] **Step 3: Add aggregator + WS frame emission in `ws_routes.py`**

Edit `kc-supervisor/src/kc_supervisor/ws_routes.py`:

Update the imports near line 9:

```python
from kc_core.stream_frames import (
    TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage,
)
```

Inside the per-message handling block (around line 137 where `async for frame in rt.assembled.core_agent.send_stream(content):`), introduce per-turn aggregator state. Replace the entire `async for frame in ...` loop with:

```python
                    # Per-turn aggregator state (one user message → at most one usage frame)
                    agg = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "generation_ms": 0.0,
                        "ttfb_ms": None,        # first TurnUsage's ttfb_ms
                        "calls": 0,
                        "usage_reported": True,  # AND across all TurnUsage frames
                    }
                    try:
                        async for frame in rt.assembled.core_agent.send_stream(content):
                            if isinstance(frame, TokenDelta):
                                await _safe_send({"type": "token", "delta": frame.content})
                            elif isinstance(frame, ToolCallStart):
                                deps.conversations.append(conversation_id, ToolCallMessage(
                                    tool_call_id=frame.call["id"],
                                    tool_name=frame.call["name"],
                                    arguments=frame.call["arguments"],
                                ))
                                await _safe_send({"type": "tool_call", "call": frame.call})
                            elif isinstance(frame, ToolResult):
                                deps.conversations.append(conversation_id, ToolResultMessage(
                                    tool_call_id=frame.call_id,
                                    content=frame.content,
                                ))
                                await _safe_send({
                                    "type": "tool_result",
                                    "call_id": frame.call_id,
                                    "content": frame.content,
                                })
                            elif isinstance(frame, TurnUsage):
                                if not frame.usage_reported:
                                    agg["usage_reported"] = False
                                if frame.usage_reported:
                                    agg["input_tokens"] += frame.input_tokens
                                    agg["output_tokens"] += frame.output_tokens
                                agg["generation_ms"] += frame.generation_ms
                                if agg["ttfb_ms"] is None:
                                    agg["ttfb_ms"] = frame.ttfb_ms
                                agg["calls"] += 1
                            elif isinstance(frame, Complete):
                                # Build the public payload (None for token counts when partial reporting)
                                usage_payload = {
                                    "input_tokens": agg["input_tokens"] if agg["usage_reported"] else None,
                                    "output_tokens": agg["output_tokens"] if agg["usage_reported"] else None,
                                    "ttfb_ms": agg["ttfb_ms"] if agg["ttfb_ms"] is not None else 0.0,
                                    "generation_ms": agg["generation_ms"],
                                    "calls": agg["calls"],
                                    "usage_reported": agg["usage_reported"],
                                }
                                if agg["calls"] > 0:
                                    await _safe_send({"type": "usage", **usage_payload})
                                deps.conversations.append(
                                    conversation_id, frame.reply,
                                    usage=(usage_payload if agg["calls"] > 0 else None),
                                )
                                await _safe_send({
                                    "type": "assistant_complete",
                                    "content": frame.reply.content,
                                })
                        rt.last_error = None
                        if not ws_alive:
                            raise WebSocketDisconnect()
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        # Existing error handling is unchanged below; aggregator state is
                        # implicitly dropped since we never reached Complete.
                        ...
```

The engineer should preserve the existing `except` block (the `"error"` send + `rt.last_error` assignment + `set_status(DEGRADED)`) — that block already lives just below the `async for` loop. Keep it as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_ws_chat.py -v`
Expected: all existing tests still pass; the new tests pass.

- [ ] **Step 5: Commit**

```bash
cd kc-supervisor
git add src/kc_supervisor/ws_routes.py tests/test_ws_chat.py
git commit -m "feat(kc-supervisor): aggregate TurnUsage and emit {type:'usage'} WS frame per turn"
```

---

## Task 8: Inbound (Telegram/iMessage) path persists usage

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/inbound.py`
- Test: `kc-supervisor/tests/test_inbound.py`

The inbound path has no WS, but it persists the `AssistantMessage`. It should attach `usage` to that persistence so historical Telegram replies also get badges in the dashboard's sidebar view.

- [ ] **Step 1: Write the failing test**

Add to `kc-supervisor/tests/test_inbound.py`:

```python
@pytest.mark.asyncio
async def test_inbound_persists_usage_on_assistant_message(deps):
    import json as _j
    from kc_core.stream_frames import TurnUsage

    reply = AssistantMessage(content="hi back")
    frames = [
        TurnUsage(call_index=0, input_tokens=100, output_tokens=4,
                  ttfb_ms=40.0, generation_ms=80.0, usage_reported=True),
        Complete(reply=reply),
    ]
    rt = _build_runtime("alice", frames)
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env(channel="telegram", chat_id="C1", sender_id="u1", content="hi")
    await router.handle_inbound(env)

    cid = deps.conversations.s.get_conv_for_chat("telegram", "C1", "alice")
    assert cid is not None
    rows = deps.storage.list_messages(cid)
    asst = [r for r in rows if r["role"] == "assistant"][-1]
    assert asst["usage_json"] is not None
    parsed = _j.loads(asst["usage_json"])
    assert parsed == {
        "input_tokens": 100,
        "output_tokens": 4,
        "ttfb_ms": 40.0,
        "generation_ms": 80.0,
        "calls": 1,
        "usage_reported": True,
    }


@pytest.mark.asyncio
async def test_inbound_no_usage_persisted_when_stream_errors(deps):
    from kc_core.stream_frames import TurnUsage

    async def _gen(_content):
        yield TurnUsage(call_index=0, input_tokens=10, output_tokens=2,
                        ttfb_ms=20.0, generation_ms=30.0, usage_reported=True)
        raise RuntimeError("boom")

    rt = _build_runtime("alice", [])
    rt.assembled.core_agent.send_stream = _gen
    registry = _make_registry({"alice": rt})
    connector = _FakeConnector("telegram")
    conn_registry = _FakeConnectorRegistry({"telegram": connector})
    routing = _FakeRoutingTable(default_agent="alice")
    router = _make_router(deps, registry, conn_registry, routing)

    env = _Env(channel="telegram", chat_id="C2", sender_id="u1", content="hi")
    await router.handle_inbound(env)  # must not raise

    cid = deps.conversations.s.get_conv_for_chat("telegram", "C2", "alice")
    if cid is not None:
        rows = deps.storage.list_messages(cid)
        assert all(r["role"] != "assistant" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kc-supervisor && uv run pytest tests/test_inbound.py -v -k usage`
Expected: FAIL — usage_json not persisted on inbound path.

- [ ] **Step 3: Mirror the aggregator in `inbound.py`**

Edit `kc-supervisor/src/kc_supervisor/inbound.py`. Add the same `TurnUsage` import and the same per-turn aggregator pattern as Task 7, but **without the WS-send call**: only the `deps.conversations.append(..., usage=usage_payload)` survives. The existing `Complete` branch (around line 105) is the place to attach `usage`.

The engineer should diff against Task 7's `ws_routes.py` change — the structure is identical except for the absence of `_safe_send`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && uv run pytest tests/test_inbound.py -v`
Expected: all existing tests still pass; new test passes.

- [ ] **Step 5: Commit**

```bash
cd kc-supervisor
git add src/kc_supervisor/inbound.py tests/test_inbound.py
git commit -m "feat(kc-supervisor): persist usage on inbound path AssistantMessage"
```

---

## Task 9: Dashboard `ChatEvent` type extension

**Files:**
- Modify: `kc-dashboard/src/ws/useChatSocket.ts`

- [ ] **Step 1: Update the type union**

Edit `kc-dashboard/src/ws/useChatSocket.ts`:

Replace the existing `ChatEvent` definition with:

```ts
export type ChatUsageEvent = {
  type: "usage";
  input_tokens: number | null;
  output_tokens: number | null;
  ttfb_ms: number;
  generation_ms: number;
  calls: number;
  usage_reported: boolean;
};

export type ChatEvent =
  | { type: "agent_status"; status: string }
  | { type: "token"; delta: string }
  | { type: "tool_call"; call: { id: string; name: string; arguments?: unknown } }
  | { type: "tool_result"; tool_call_id: string; content?: string }
  | ChatUsageEvent
  | { type: "assistant_complete"; content: string }
  | { type: "error"; message: string };
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd kc-dashboard && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
cd kc-dashboard
git add src/ws/useChatSocket.ts
git commit -m "feat(kc-dashboard): add usage event type to ChatEvent union"
```

---

## Task 10: Live tok/s ticker hook

**Files:**
- Create: `kc-dashboard/src/ws/useLiveTokensPerSecond.ts`
- Test: `kc-dashboard/tests/components/useLiveTokensPerSecond.test.tsx`

A small dedicated hook to keep `Chat.tsx` focused. The hook recomputes a live tok/s estimate at 4 Hz from the streaming text buffer and the timestamp of the first `token` event seen in the current turn.

- [ ] **Step 1: Write the failing test**

Create `kc-dashboard/tests/components/useLiveTokensPerSecond.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLiveTokensPerSecond } from "../../src/ws/useLiveTokensPerSecond";

describe("useLiveTokensPerSecond", () => {
  it("returns null when streaming buffer is empty", () => {
    const { result } = renderHook(() => useLiveTokensPerSecond("", null));
    expect(result.current).toBeNull();
  });

  it("computes chars/4 / elapsed seconds", () => {
    vi.useFakeTimers();
    const t0 = Date.now();
    const { result, rerender } = renderHook(
      ({ buf, start }: { buf: string; start: number | null }) =>
        useLiveTokensPerSecond(buf, start),
      { initialProps: { buf: "", start: null } },
    );
    rerender({ buf: "0123456789".repeat(10), start: t0 }); // 100 chars
    act(() => { vi.setSystemTime(t0 + 1000); vi.advanceTimersByTime(250); });
    // 100/4 = 25 estimated tokens, 1s elapsed → 25 t/s
    expect(result.current).toBeCloseTo(25, 0);
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kc-dashboard && npx vitest run tests/components/useLiveTokensPerSecond.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the hook**

Create `kc-dashboard/src/ws/useLiveTokensPerSecond.ts`:

```ts
import { useEffect, useState } from "react";

const TICK_MS = 250;
const CHARS_PER_TOKEN = 4;

export function useLiveTokensPerSecond(
  streamingBuf: string,
  startedAtMs: number | null,
): number | null {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!streamingBuf || startedAtMs == null) return;
    const id = setInterval(() => setTick((t) => t + 1), TICK_MS);
    return () => clearInterval(id);
  }, [streamingBuf, startedAtMs]);

  if (!streamingBuf || startedAtMs == null) return null;
  const elapsedSeconds = Math.max((Date.now() - startedAtMs) / 1000, 0.001);
  const estimatedTokens = streamingBuf.length / CHARS_PER_TOKEN;
  // tick is read so React knows we depend on it for re-renders
  void tick;
  return estimatedTokens / elapsedSeconds;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kc-dashboard && npx vitest run tests/components/useLiveTokensPerSecond.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd kc-dashboard
git add src/ws/useLiveTokensPerSecond.ts tests/components/useLiveTokensPerSecond.test.tsx
git commit -m "feat(kc-dashboard): add useLiveTokensPerSecond hook"
```

---

## Task 11: Format helpers for tok/s, tok count, ms

**Files:**
- Create: `kc-dashboard/src/lib/formatUsage.ts`
- Test: `kc-dashboard/tests/components/formatUsage.test.ts`

Pure functions, easy to test in isolation, reused by both the header strip and the bubble badge.

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/components/formatUsage.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  formatTokensPerSecond,
  formatTokenCount,
  formatTtfb,
} from "../../src/lib/formatUsage";

describe("formatTokensPerSecond", () => {
  it("integer when >= 10", () => {
    expect(formatTokensPerSecond(127)).toBe("127 t/s");
    expect(formatTokensPerSecond(10)).toBe("10 t/s");
  });
  it("one decimal when < 10", () => {
    expect(formatTokensPerSecond(8.43)).toBe("8.4 t/s");
    expect(formatTokensPerSecond(0.5)).toBe("0.5 t/s");
  });
  it("returns 'instant' when generation_ms < 50", () => {
    // exposed via a separate API: formatTokensPerSecond accepts only a number,
    // 'instant' is a header-/bubble-rendering concern handled in the components.
  });
});

describe("formatTokenCount", () => {
  it("integer under 10000", () => {
    expect(formatTokenCount(412)).toBe("412 tok");
    expect(formatTokenCount(9999)).toBe("9999 tok");
  });
  it("k suffix at and above 10000", () => {
    expect(formatTokenCount(10000)).toBe("10.0k tok");
    expect(formatTokenCount(12400)).toBe("12.4k tok");
  });
});

describe("formatTtfb", () => {
  it("two decimals under 10s", () => {
    expect(formatTtfb(1042)).toBe("1.04 s");
    expect(formatTtfb(50)).toBe("0.05 s");
  });
  it("one decimal at and above 10s", () => {
    expect(formatTtfb(10000)).toBe("10.0 s");
    expect(formatTtfb(12345)).toBe("12.3 s");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/components/formatUsage.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the formatters**

Create `kc-dashboard/src/lib/formatUsage.ts`:

```ts
export function formatTokensPerSecond(tps: number): string {
  if (tps >= 10) return `${Math.round(tps)} t/s`;
  return `${tps.toFixed(1)} t/s`;
}

export function formatTokenCount(n: number): string {
  if (n < 10000) return `${n} tok`;
  return `${(n / 1000).toFixed(1)}k tok`;
}

export function formatTtfb(ms: number): string {
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(2)} s`;
  return `${s.toFixed(1)} s`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-dashboard && npx vitest run tests/components/formatUsage.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd kc-dashboard
git add src/lib/formatUsage.ts tests/components/formatUsage.test.ts
git commit -m "feat(kc-dashboard): add usage formatting helpers"
```

---

## Task 12: Per-bubble usage badge in `MessageBubble`

**Files:**
- Modify: `kc-dashboard/src/components/MessageBubble.tsx`
- Create: `kc-dashboard/tests/components/MessageBubble.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `kc-dashboard/tests/components/MessageBubble.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "../../src/components/MessageBubble";

describe("MessageBubble usage badge", () => {
  it("renders badge with tok/s, count, calls suffix, ttfb", () => {
    render(
      <MessageBubble
        role="assistant"
        content="hi"
        usage={{
          input_tokens: 100,
          output_tokens: 412,
          ttfb_ms: 1042,
          generation_ms: 3240,
          calls: 2,
          usage_reported: true,
        }}
      />,
    );
    // 412 tokens / 3.24 s ≈ 127 t/s
    expect(screen.getByText(/127 t\/s/)).toBeInTheDocument();
    expect(screen.getByText(/412 tok/)).toBeInTheDocument();
    expect(screen.getByText(/2 calls/)).toBeInTheDocument();
    expect(screen.getByText(/ttfb 1\.04 s/)).toBeInTheDocument();
  });

  it("omits 'calls' suffix when calls === 1", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: 100, output_tokens: 50, ttfb_ms: 100, generation_ms: 1000,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.queryByText(/1 calls/)).not.toBeInTheDocument();
  });

  it("renders '— · ttfb …' for tool-only turn (output_tokens=0)", () => {
    render(
      <MessageBubble role="assistant" content="" usage={{
        input_tokens: 100, output_tokens: 0, ttfb_ms: 200, generation_ms: 0,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.getByText(/—/)).toBeInTheDocument();
    expect(screen.getByText(/ttfb 0\.20 s/)).toBeInTheDocument();
  });

  it("renders '— ttfb only' when usage_reported is false", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: null, output_tokens: null, ttfb_ms: 1000, generation_ms: 500,
        calls: 1, usage_reported: false,
      }} />,
    );
    expect(screen.getByText(/ttfb only/)).toBeInTheDocument();
  });

  it("renders 'instant' when generation_ms < 50", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: 100, output_tokens: 4, ttfb_ms: 50, generation_ms: 10,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.getByText(/instant/)).toBeInTheDocument();
  });

  it("renders no badge when usage prop is omitted", () => {
    render(<MessageBubble role="assistant" content="hi" />);
    expect(screen.queryByText(/t\/s/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ttfb/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-dashboard && npx vitest run tests/components/MessageBubble.test.tsx`
Expected: FAIL — `usage` prop not accepted; badge not rendered.

- [ ] **Step 3: Update `MessageBubble`**

Edit `kc-dashboard/src/components/MessageBubble.tsx`:

Add an exported usage type and accept it as a prop:

```tsx
import { formatTokensPerSecond, formatTokenCount, formatTtfb } from "../lib/formatUsage";

export type BubbleUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  ttfb_ms: number;
  generation_ms: number;
  calls: number;
  usage_reported: boolean;
};

function renderBadge(usage: BubbleUsage): React.ReactNode {
  const callsSuffix = usage.calls > 1 ? ` · ${usage.calls} calls` : "";
  const ttfb = `ttfb ${formatTtfb(usage.ttfb_ms)}`;

  // No-usage path: durations only.
  if (!usage.usage_reported) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        — ttfb only · {ttfb}{callsSuffix}
      </div>
    );
  }

  const out = usage.output_tokens ?? 0;
  if (out === 0) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        — · {ttfb}{callsSuffix}
      </div>
    );
  }

  if (usage.generation_ms < 50) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        instant · {formatTokenCount(out)}{callsSuffix} · {ttfb}
      </div>
    );
  }

  const tps = (out * 1000) / usage.generation_ms;
  return (
    <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
      {formatTokensPerSecond(tps)} · {formatTokenCount(out)}{callsSuffix} · {ttfb}
    </div>
  );
}

export function MessageBubble({
  role,
  content,
  usage,
}: {
  role: Role;
  content: string;
  usage?: BubbleUsage;
}) {
  // ... existing body ...
  // At the end of the assistant content block, after closing the markdown </div>,
  // insert: {role === "assistant" && usage && renderBadge(usage)}
}
```

The implementing engineer should place `{role === "assistant" && usage && renderBadge(usage)}` immediately after the closing `</div>` of the `font-body` content div — i.e., as a sibling so it appears below the message text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-dashboard && npx vitest run tests/components/MessageBubble.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd kc-dashboard
git add src/components/MessageBubble.tsx tests/components/MessageBubble.test.tsx
git commit -m "feat(kc-dashboard): per-bubble usage badge on assistant messages"
```

---

## Task 13: Wire usage into `Chat.tsx` — header strip, live ticker, per-bubble pass-through

**Files:**
- Modify: `kc-dashboard/src/views/Chat.tsx`
- Modify: `kc-dashboard/src/api/conversations.ts` _(if needed to surface `usage` on listMessages response)_
- Test: `kc-dashboard/tests/views/Chat.test.tsx`

- [ ] **Step 1: Verify `listMessages` API client surfaces `usage`**

Read `kc-dashboard/src/api/conversations.ts`. The response type for `listMessages` should be extended so `messages[i].usage` is typed. If it currently strips unknown fields, change it to pass through. If it uses `unknown[]` or similar, no change needed.

If a typed schema exists, edit it:

```ts
export type StoredMessage = {
  type: "UserMessage" | "AssistantMessage" | "ToolCallMessage" | "ToolResultMessage";
  content?: string;
  // ... existing fields ...
  usage?: {
    input_tokens: number | null;
    output_tokens: number | null;
    ttfb_ms: number;
    generation_ms: number;
    calls: number;
    usage_reported: boolean;
  };
};
```

- [ ] **Step 2: Write the failing test for `Chat.tsx`**

Add a new `it(...)` block inside `describe("Chat view", ...)` in `kc-dashboard/tests/views/Chat.test.tsx`. This mirrors the existing "submitting input sends user_message and renders assistant reply" test:

```tsx
  it("renders Last reply / TTFB header rows after a usage event", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());

    const input = screen.getByPlaceholderText(/reply/i);
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.submit(input.closest("form")!);

    // Persisted messages payload — the assistant reply WITH usage attached.
    messagesPayload = [
      { type: "UserMessage", content: "hi" },
      {
        type: "AssistantMessage",
        content: "Hello back!",
        // usage echoed by the HTTP route (Task 6)
        usage: {
          input_tokens: 100,
          output_tokens: 412,
          ttfb_ms: 1042,
          generation_ms: 3240,
          calls: 2,
          usage_reported: true,
        },
      },
    ];

    // Stream events ordered as on the wire.
    lastFakeWS!.push({ type: "token", delta: "Hello " });
    lastFakeWS!.push({ type: "token", delta: "back!" });
    lastFakeWS!.push({
      type: "usage",
      input_tokens: 100,
      output_tokens: 412,
      ttfb_ms: 1042,
      generation_ms: 3240,
      calls: 2,
      usage_reported: true,
    });
    lastFakeWS!.push({ type: "assistant_complete", content: "Hello back!" });

    // Header strip: Last reply with authoritative tok/s.
    // 412 / 3.24 ≈ 127 t/s; output count 412 tok.
    expect(await screen.findByText(/127 t\/s/)).toBeInTheDocument();
    expect(screen.getByText(/412 tok/)).toBeInTheDocument();
    // TTFB row.
    expect(screen.getByText(/1\.04 s/)).toBeInTheDocument();
    expect(screen.getByText(/2 calls/)).toBeInTheDocument();
    // Per-bubble badge appears too (with ttfb prefix).
    expect(screen.getByText(/ttfb 1\.04 s/)).toBeInTheDocument();
  });
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd kc-dashboard && npx vitest run tests/views/Chat.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Update `Chat.tsx` — derive `currentTurnUsage`, render header rows, pass usage to bubbles**

Edit `kc-dashboard/src/views/Chat.tsx`:

Add imports:

```tsx
import { useLiveTokensPerSecond } from "../ws/useLiveTokensPerSecond";
import { formatTokensPerSecond, formatTokenCount, formatTtfb } from "../lib/formatUsage";
import type { BubbleUsage } from "../components/MessageBubble";
```

After the existing `streaming` `useMemo` (around line 139), add new derivations:

```tsx
  // Timestamp of the first `token` event in the current (uncompleted) turn.
  const streamingStartMs = useMemo(() => {
    let start = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "assistant_complete") { start = i + 1; break; }
    }
    for (let i = start; i < events.length; i++) {
      if (events[i].type === "token") {
        // We don't have per-event timestamps, so approximate using effect timing
        // by storing the time the FIRST token was observed in a ref.
        return null;
      }
    }
    return null;
  }, [events]);

  // Track first-token wallclock time across event additions.
  const firstTokenAtRef = useRef<number | null>(null);
  useEffect(() => {
    const last = events[events.length - 1];
    if (last?.type === "token" && firstTokenAtRef.current == null) {
      firstTokenAtRef.current = Date.now();
    }
    if (last?.type === "assistant_complete") {
      firstTokenAtRef.current = null;
    }
  }, [events]);

  const liveTps = useLiveTokensPerSecond(streaming, firstTokenAtRef.current);

  // Most recent {type:"usage"} event since the last assistant_complete.
  const currentTurnUsage = useMemo(() => {
    let resetAt = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "assistant_complete") { resetAt = i + 1; break; }
    }
    for (let i = events.length - 1; i >= resetAt; i--) {
      const e = events[i];
      if (e.type === "usage") return e;
    }
    return null;
  }, [events]);

  // Per-bubble usage map (from persisted messages query).
  const bubbleUsageByIdx = useMemo(() => {
    const map = new Map<number, BubbleUsage>();
    const msgs = msgsQ.data?.messages ?? [];
    let assistantIdx = 0;
    for (const m of msgs) {
      if (m.type === "AssistantMessage") {
        if (m.usage) map.set(assistantIdx, m.usage as BubbleUsage);
        assistantIdx++;
      }
    }
    return map;
  }, [msgsQ.data]);
```

Adjust the rendered messages loop. The existing `rendered` array is built in a `useMemo`; extend it to track an `assistantIdx` so each assistant entry gets the matching usage:

```tsx
  const rendered = useMemo(() => {
    const out: { role: "user" | "assistant"; content: string; usage?: BubbleUsage }[] = [];
    let assistantIdx = 0;
    for (const m of msgsQ.data?.messages ?? []) {
      if (m.type === "UserMessage") out.push({ role: "user", content: m.content ?? "" });
      else if (m.type === "AssistantMessage") {
        const content = m.content ?? "";
        if (!content.trim()) { assistantIdx++; continue; }
        out.push({
          role: "assistant",
          content,
          usage: bubbleUsageByIdx.get(assistantIdx),
        });
        assistantIdx++;
      }
    }
    return out;
  }, [msgsQ.data, bubbleUsageByIdx]);
```

Update the rendered-bubble map:

```tsx
{rendered.map((m, i) => <MessageBubble key={i} role={m.role} content={m.content} usage={m.usage} />)}
```

In the header `<dl>` (around line 357), append two new rows after the existing `Status` row:

```tsx
                <dt className="text-muted2 font-normal">Last reply</dt>
                <dd className="text-text font-medium">
                  {(() => {
                    if (currentTurnUsage && currentTurnUsage.usage_reported && currentTurnUsage.output_tokens != null && currentTurnUsage.generation_ms >= 50) {
                      const tps = (currentTurnUsage.output_tokens * 1000) / currentTurnUsage.generation_ms;
                      return `${formatTokensPerSecond(tps)} · ${formatTokenCount(currentTurnUsage.output_tokens)}`;
                    }
                    if (currentTurnUsage && !currentTurnUsage.usage_reported && liveTps != null) {
                      return `~${formatTokensPerSecond(liveTps)} · estimate`;
                    }
                    if (streaming && liveTps != null) {
                      return `~${formatTokensPerSecond(liveTps)} · streaming`;
                    }
                    return "—";
                  })()}
                </dd>
                {currentTurnUsage && (
                  <>
                    <dt className="text-muted2 font-normal">TTFB</dt>
                    <dd className="text-muted font-medium">
                      {formatTtfb(currentTurnUsage.ttfb_ms)}
                      {currentTurnUsage.calls > 1 && ` · ${currentTurnUsage.calls} calls`}
                    </dd>
                  </>
                )}
```

(Imports: ensure `useRef`, `useEffect` are imported from React.)

- [ ] **Step 5: Run all dashboard tests**

Run: `cd kc-dashboard && npm test`
Expected: all tests pass, including the new Chat header-strip test.

- [ ] **Step 6: Commit**

```bash
cd kc-dashboard
git add src/views/Chat.tsx src/api/conversations.ts tests/views/Chat.test.tsx
git commit -m "feat(kc-dashboard): render Last reply / TTFB header rows and per-bubble usage"
```

---

## Task 14: SMOKE.md updates and cross-repo verification

**Files:**
- Modify: `kc-dashboard/SMOKE.md`
- Modify: `kc-supervisor/SMOKE.md`

- [ ] **Step 1: Update `kc-dashboard/SMOKE.md`**

Append a new section:

```markdown
## tokens-per-second metric (added 2026-05-09)

- [ ] Send a single-message reply to any agent. The chat header `Last reply` row should briefly show `~NN t/s · streaming` and then snap to a stable `NN t/s · NNN tok` value. The `TTFB` row appears with `N.NN s`.
- [ ] The completed assistant bubble has a faint mono footer reading `NN t/s · NNN tok · ttfb N.NN s`.
- [ ] Send a message that triggers a tool call (e.g. ask Kona to use a calendar/Gmail tool). The header's `TTFB` row shows `N.NN s · 2 calls` and the bubble badge has `· 2 calls`.
- [ ] Reload the dashboard. The historical assistant bubbles still show their tok/s badges (read from SQLite).
- [ ] Switch the supervisor to point at a provider that does NOT support `stream_options.include_usage` (an old proxy or stub). The header row reads `~NN t/s · estimate`; the bubble shows `— ttfb only · ttfb N.NN s`. The supervisor logs one warning per `(model, provider)` pair.
```

- [ ] **Step 2: Update `kc-supervisor/SMOKE.md`**

Append:

```markdown
## tokens-per-second metric (added 2026-05-09)

- [ ] After a successful chat turn, the SQLite messages table for the AssistantMessage row has a non-NULL `usage_json` column whose JSON parses to `{input_tokens, output_tokens, ttfb_ms, generation_ms, calls, usage_reported}`.
- [ ] After a turn that errored mid-stream (e.g. kill the model server during reply), no AssistantMessage row is written and no `{type:"usage"}` WS frame is sent.
- [ ] An inbound (Telegram) reply also persists `usage_json` on its AssistantMessage row.
```

- [ ] **Step 3: Run the full suites one more time across all subrepos**

```bash
cd kc-core && uv run pytest -v
cd ../kc-supervisor && uv run pytest -v
cd ../kc-dashboard && npm test
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
cd <repo-root>
git add kc-dashboard/SMOKE.md kc-supervisor/SMOKE.md
git commit -m "docs: add SMOKE gates for tokens-per-second metric"
```

---

## Self-review notes

**Spec coverage check:**

- ✅ ChatUsage / TurnUsage frames — Tasks 1, 4
- ✅ `stream_options.include_usage=true` request — Task 2
- ✅ usage capture, no-usage fallback, garbage-value fallback — Tasks 2, 3
- ✅ Tool-only turn (`output_tokens=0` / `generation_ms=0`) — Task 3 (kc-core), Task 12 (badge), Task 13 (header)
- ✅ Multi-call aggregation in supervisor — Task 7
- ✅ Partial reporting → null token counts — Task 7
- ✅ Error path drops aggregator state, no SQLite write — Task 7
- ✅ SQLite `usage_json` migration — Task 5
- ✅ ConversationManager.append accepts usage — Task 6
- ✅ HTTP route echoes usage — Task 6
- ✅ Inbound (Telegram) path persists usage — Task 8
- ✅ Dashboard ChatEvent type — Task 9
- ✅ Live ticker hook — Task 10
- ✅ Format helpers (`< 10 t/s` decimal, `k` suffix, ttfb seconds) — Task 11
- ✅ Per-bubble badge rendering, all five edge cases — Task 12
- ✅ Header strip rows + currentTurnUsage derivation + bubble pass-through — Task 13
- ✅ SMOKE gates — Task 14

**Type consistency:**

- `ChatUsage(input_tokens, output_tokens, ttfb_ms, generation_ms, usage_reported)` — used identically in Tasks 1, 2, 3, 4.
- `TurnUsage(call_index, ...)` — same shape, used in Tasks 1, 4, 7, 8.
- `BubbleUsage` (TypeScript) matches the WS `ChatUsageEvent` shape (`input_tokens` and `output_tokens` are `number | null`) — used in Tasks 9, 12, 13.
- WS payload key names match between Tasks 7, 9, 12, 13 (`input_tokens`, `output_tokens`, `ttfb_ms`, `generation_ms`, `calls`, `usage_reported`).

**No placeholders:** all code blocks contain literal code; the few `...` placeholders in Tasks 7 and 8 explicitly delegate to existing test patterns the engineer should mirror, with concrete fixture-shape descriptions.
