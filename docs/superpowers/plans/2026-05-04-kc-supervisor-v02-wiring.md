# kc-supervisor v0.2 Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire kc-supervisor v0.1 (merged at `3e18727`) into a fully working backend: real Ollama chat with token streaming, audit logging hooked into tool calls, working `POST /undo/{audit_id}`, per-conversation locking, and `POST /agents` for subagent spawning. After this lands, the dashboard ships against a complete backend.

**Architecture:** kc-core gets a streaming surface (`Agent.send_stream`, `OllamaClient.chat_stream`) without breaking `chat`/`send`. kc-supervisor takes ownership of agent assembly (no longer delegating to `kc_sandbox.wiring.build_sandboxed_agent`) so it can wrap tools with audit hooks, capture Decisions via contextvar, and inject a `RecordingUndoLog` that records `record() -> int` eids for the `audit_undo_link` table. Per-conversation locking reuses one kc-core Agent per agent and resets `agent.history` from SQLite before each turn.

**Tech Stack:** Python 3.14 (system has 3.14.4), `asyncio`, `contextvars`, FastAPI 0.110+, uvicorn, httpx (already in tree). Tests use pytest + pytest-asyncio.

**Repo bootstrap:** Single monorepo at `/Users/sammydallal/Desktop/claudeCode/SammyClaw/`. Branches like `feat/kc-supervisor-v02-wiring` cut from `main`. Sub-packages `kc-core/`, `kc-sandbox/`, `kc-supervisor/` are siblings. Run tests via `cd <pkg> && source .venv/bin/activate && pytest tests/` (or use `kc-supervisor/.venv` for cross-package work — note that `kc-core` tests need `respx` which v1 didn't pull in; install once with `pip install respx` in the supervisor venv).

**Spec reference:** `docs/superpowers/specs/2026-05-04-kc-supervisor-v02-wiring-design.md`.

---

## File Structure

### kc-core (new + modified)
- `kc-core/src/kc_core/stream_frames.py` (NEW) — `ChatStreamFrame` (TextDelta, ToolCallsBlock, Done) and `StreamFrame` (TokenDelta, ToolCallStart, ToolResult, Complete) discriminated unions.
- `kc-core/src/kc_core/ollama_client.py` (MODIFY) — add `chat_stream`; rewrite `chat` as a thin accumulator over `chat_stream`.
- `kc-core/src/kc_core/agent.py` (MODIFY) — add `_ChatClient.chat_stream` Protocol method; add `Agent.send_stream`.
- `kc-core/tests/conftest.py` (MODIFY) — extend `FakeOllamaClient` to support `chat_stream`.
- `kc-core/tests/test_ollama_client.py` (MODIFY) — add NDJSON streaming tests.
- `kc-core/tests/test_agent.py` (MODIFY) — add `send_stream` tests.

### kc-supervisor (new + modified)
- `kc-supervisor/src/kc_supervisor/storage.py` (MODIFY) — change `audit_undo_link.undo_op_id` from `TEXT` to `INTEGER`; update method signatures (`undo_op_id: int`).
- `kc-supervisor/src/kc_supervisor/locks.py` (NEW) — `ConversationLocks`.
- `kc-supervisor/src/kc_supervisor/audit_tools.py` (NEW) — `_decision_contextvar`, `_eid_contextvar`, `RecordingUndoLog`, `AuditingToolRegistry`, `make_audit_aware_callback`.
- `kc-supervisor/src/kc_supervisor/assembly.py` (NEW) — `AssembledAgent` dataclass, `assemble_agent` factory.
- `kc-supervisor/src/kc_supervisor/agents.py` (MODIFY) — `AgentRegistry` accepts assembly deps; `load_all` constructs `AssembledAgent` per YAML; failures land on `last_error` + `status=DEGRADED`.
- `kc-supervisor/src/kc_supervisor/service.py` (MODIFY) — `Deps` gains `conv_locks: ConversationLocks` and `shares: SharesRegistry`.
- `kc-supervisor/src/kc_supervisor/main.py` (MODIFY) — wires new pieces; consumes `KC_OLLAMA_URL`.
- `kc-supervisor/src/kc_supervisor/ws_routes.py` (MODIFY) — `ws_chat` rewritten for streaming + per-cid lock + history rehydration.
- `kc-supervisor/src/kc_supervisor/http_routes.py` (MODIFY) — real `POST /undo/{audit_id}` + new `POST /agents`.
- `kc-supervisor/tests/test_storage.py` (MODIFY) — eid type swap (ints).
- `kc-supervisor/tests/test_locks.py` (NEW).
- `kc-supervisor/tests/test_audit_tools.py` (NEW).
- `kc-supervisor/tests/test_assembly.py` (NEW).
- `kc-supervisor/tests/test_agents.py` (MODIFY) — assembly degraded handling.
- `kc-supervisor/tests/test_ws_chat.py` (MODIFY) — streaming + lock + rehydration.
- `kc-supervisor/tests/test_http.py` (MODIFY) — real undo + spawn.
- `kc-supervisor/SMOKE.md` (MODIFY) — replace v1 "agent not initialized" steps with real-Ollama steps.
- `kc-supervisor/README.md` (MODIFY) — endpoint table updates, `KC_OLLAMA_URL` now consumed.

### kc-sandbox
- No changes. Consumed via `SharesRegistry`, `Journal`, `UndoLog`, `Undoer`, `build_file_tools`, `DEFAULT_FILE_TOOL_TIERS`, `PermissionEngine`.

---

## Task 1: kc-core stream frame types

**Files:**
- Create: `kc-core/src/kc_core/stream_frames.py`
- Test: `kc-core/tests/test_stream_frames.py` (NEW, optional but cheap)

**Why:** Two discriminated unions define the shapes that flow through the streaming pipeline. `ChatStreamFrame` is the wire-level shape from the Ollama client. `StreamFrame` is the higher-level shape `Agent.send_stream` yields to the supervisor. Keeping them in their own module avoids polluting `agent.py`/`ollama_client.py` with type machinery and gives kc-supervisor a clean import target.

- [ ] **Step 1: Write the failing test**

Create `kc-core/tests/test_stream_frames.py`:

```python
from kc_core.messages import AssistantMessage
from kc_core.stream_frames import (
    TextDelta, ToolCallsBlock, Done,
    TokenDelta, ToolCallStart, ToolResult, Complete,
)


def test_text_delta_holds_content():
    f = TextDelta(content="hello")
    assert f.content == "hello"


def test_tool_calls_block_holds_calls():
    calls = [{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]
    f = ToolCallsBlock(calls=calls)
    assert f.calls == calls


def test_done_finish_reason():
    f = Done(finish_reason="stop")
    assert f.finish_reason == "stop"


def test_token_delta_holds_content():
    f = TokenDelta(content="hi")
    assert f.content == "hi"


def test_tool_call_start_holds_call():
    call = {"id": "c1", "name": "echo", "arguments": {"text": "hi"}}
    f = ToolCallStart(call=call)
    assert f.call == call


def test_tool_result_holds_call_id_and_content():
    f = ToolResult(call_id="c1", content="hi")
    assert f.call_id == "c1"
    assert f.content == "hi"


def test_complete_holds_assistant_message():
    msg = AssistantMessage(content="done")
    f = Complete(reply=msg)
    assert f.reply is msg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
source kc-supervisor/.venv/bin/activate
cd kc-core
pytest tests/test_stream_frames.py -v
```

Expected: FAIL — `kc_core.stream_frames` module not found.

- [ ] **Step 3: Implement `kc-core/src/kc_core/stream_frames.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Union
from kc_core.messages import AssistantMessage


# ---- Wire-level: what the model client yields ----

@dataclass(frozen=True)
class TextDelta:
    """Chunk of assistant text. May be empty."""
    content: str


@dataclass(frozen=True)
class ToolCallsBlock:
    """A complete set of tool calls emitted by the model in one turn."""
    calls: list[dict[str, Any]]


@dataclass(frozen=True)
class Done:
    """Final frame from the model client. finish_reason is e.g. 'stop' or 'tool_calls'."""
    finish_reason: str


ChatStreamFrame = Union[TextDelta, ToolCallsBlock, Done]


# ---- Agent-level: what Agent.send_stream yields ----

@dataclass(frozen=True)
class TokenDelta:
    """Forwarded text chunk during the model's text generation phase."""
    content: str


@dataclass(frozen=True)
class ToolCallStart:
    """A tool call about to be executed. `call` matches kc-core's tool-call dict shape."""
    call: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """The result of a tool call (or an Error: ... string on failure)."""
    call_id: str
    content: str


@dataclass(frozen=True)
class Complete:
    """Terminal frame. `reply` is the same AssistantMessage that `send` would return."""
    reply: AssistantMessage


StreamFrame = Union[TokenDelta, ToolCallStart, ToolResult, Complete]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_stream_frames.py -v
```

Expected: PASS — 7 tests green.

- [ ] **Step 5: Run full kc-core suite to confirm no regressions**

```bash
pytest tests/ --ignore=tests/live -q
```

Expected: PASS — 38 (was) + 7 (new) = 45 tests green.

- [ ] **Step 6: Commit (monorepo style — from SammyClaw root)**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-core/src/kc_core/stream_frames.py kc-core/tests/test_stream_frames.py
git commit -m "feat(kc-core): add stream_frames module (ChatStreamFrame + StreamFrame)"
```

---

## Task 2: kc-core OllamaClient.chat_stream

**Files:**
- Modify: `kc-core/src/kc_core/ollama_client.py`
- Modify: `kc-core/tests/test_ollama_client.py`

**Why:** Add a streaming variant that hits `/api/chat` with `stream=true` and yields one `ChatStreamFrame` per NDJSON line. Refactor the existing non-streaming `chat` to be a thin accumulator over `chat_stream` — eliminates duplication and ensures the two paths stay consistent.

- [ ] **Step 1: Read the existing OllamaClient surface**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-core
grep -n "^class\|^def \|async def\|def chat" src/kc_core/ollama_client.py
```

Confirm `OllamaClient.chat(messages, tools)` returns a `ChatResponse(text, tool_calls, finish_reason)`. Note the existing httpx client setup so the new method follows the same pattern.

- [ ] **Step 2: Write failing tests**

Append to `kc-core/tests/test_ollama_client.py`:

```python
import pytest
import respx
import httpx
from kc_core.ollama_client import OllamaClient
from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_yields_text_deltas_then_done():
    """Three text-delta lines followed by a done line."""
    body = (
        b'{"message":{"content":"hello "}}\n'
        b'{"message":{"content":"world"}}\n'
        b'{"done":true,"done_reason":"stop"}\n'
    )
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    client = OllamaClient(model="qwen2.5:7b")
    frames = []
    async for f in client.chat_stream(messages=[], tools=[]):
        frames.append(f)

    assert frames == [
        TextDelta(content="hello "),
        TextDelta(content="world"),
        Done(finish_reason="stop"),
    ]


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_yields_tool_calls_block():
    """One line with tool_calls, then done."""
    body = (
        b'{"message":{"tool_calls":[{"function":{"name":"echo","arguments":{"text":"hi"}}}]}}\n'
        b'{"done":true,"done_reason":"tool_calls"}\n'
    )
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    client = OllamaClient(model="qwen2.5:7b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    assert len(frames) == 2
    assert isinstance(frames[0], ToolCallsBlock)
    assert frames[0].calls[0]["name"] == "echo"
    assert frames[0].calls[0]["arguments"] == {"text": "hi"}
    assert isinstance(frames[1], Done)
    assert frames[1].finish_reason == "tool_calls"


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_skips_empty_message_lines():
    """Lines with no message and no done flag are ignored (Ollama emits these as keepalives)."""
    body = (
        b'{"created_at":"2026-05-04T00:00:00Z"}\n'
        b'{"message":{"content":"hi"}}\n'
        b'{"done":true,"done_reason":"stop"}\n'
    )
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    client = OllamaClient(model="qwen2.5:7b")
    frames = [f async for f in client.chat_stream(messages=[], tools=[])]
    assert frames == [TextDelta(content="hi"), Done(finish_reason="stop")]


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_raises_on_malformed_json():
    """A malformed line surfaces as a ValueError."""
    body = b'this is not json\n'
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    client = OllamaClient(model="qwen2.5:7b")
    with pytest.raises(ValueError):
        async for _ in client.chat_stream(messages=[], tools=[]):
            pass


@pytest.mark.asyncio
@respx.mock
async def test_chat_uses_chat_stream_internally_and_returns_accumulated_response():
    """The non-streaming chat() should still work — backed by chat_stream."""
    body = (
        b'{"message":{"content":"hi "}}\n'
        b'{"message":{"content":"there"}}\n'
        b'{"done":true,"done_reason":"stop"}\n'
    )
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    client = OllamaClient(model="qwen2.5:7b")
    resp = await client.chat(messages=[], tools=[])
    assert resp.text == "hi there"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-core
pytest tests/test_ollama_client.py -v
```

Expected: 5 new tests fail with `AttributeError: 'OllamaClient' object has no attribute 'chat_stream'`. The existing chat tests may also fail until the rewrite is in.

- [ ] **Step 4: Refactor `kc-core/src/kc_core/ollama_client.py`**

Read the current file to understand the existing `chat` method's request shape, then replace its body. The streaming method should:

1. POST to `f"{self.url}/api/chat"` with `{"model": self.model, "messages": messages, "tools": tools, "stream": True}` (set `stream=True`).
2. Stream lines with `httpx`'s `aiter_lines()`.
3. For each non-empty line: `data = json.loads(line)`. If JSON parsing fails, raise `ValueError` (don't swallow).
4. If `data.get("done")` is true: yield `Done(finish_reason=data.get("done_reason", "stop"))` and stop.
5. Else if `data.get("message", {}).get("tool_calls")`: yield `ToolCallsBlock(calls=...)` after normalizing each call into the canonical shape kc-core expects: `{"id": str, "name": str, "arguments": dict}`. Ollama emits `{"function": {"name": ..., "arguments": ...}}` — flatten and synthesize an `id` if none is provided (e.g., `call_<index>`).
6. Else if `data.get("message", {}).get("content")`: yield `TextDelta(content=...)` (skip empty content).
7. Else: skip (keepalive).

Then rewrite `chat()` to call `chat_stream` and accumulate. New skeleton:

```python
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
import httpx
from kc_core.stream_frames import (
    ChatStreamFrame, TextDelta, ToolCallsBlock, Done,
)


@dataclass
class ChatResponse:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"


class OllamaClient:
    """Async HTTP client for Ollama's /api/chat endpoint."""

    def __init__(self, model: str, url: str = "http://localhost:11434") -> None:
        self.model = model
        self.url = url.rstrip("/")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[ChatStreamFrame]:
        """Stream NDJSON frames from /api/chat?stream=true."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self.url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"malformed NDJSON line from Ollama: {line!r}") from e

                    if data.get("done"):
                        yield Done(finish_reason=data.get("done_reason", "stop"))
                        return

                    msg = data.get("message") or {}
                    tool_calls = msg.get("tool_calls")
                    if tool_calls:
                        normalized = []
                        for i, c in enumerate(tool_calls):
                            fn = c.get("function") or {}
                            normalized.append({
                                "id": c.get("id") or f"call_{i}",
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments") or {},
                            })
                        yield ToolCallsBlock(calls=normalized)
                        continue

                    content = msg.get("content")
                    if content:
                        yield TextDelta(content=content)
                        continue
                    # else: keepalive line, skip

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        """Non-streaming convenience: accumulate chat_stream into a ChatResponse."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish: str = "stop"
        async for frame in self.chat_stream(messages=messages, tools=tools):
            if isinstance(frame, TextDelta):
                text_parts.append(frame.content)
            elif isinstance(frame, ToolCallsBlock):
                tool_calls.extend(frame.calls)
            elif isinstance(frame, Done):
                finish = frame.finish_reason
                break
        return ChatResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
        )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_ollama_client.py -v
```

Expected: PASS — all new and existing OllamaClient tests green.

- [ ] **Step 6: Run full kc-core suite**

```bash
pytest tests/ --ignore=tests/live -q
```

Expected: PASS — 45 + 5 (new chat_stream tests) = 50 tests green.

- [ ] **Step 7: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-core/src/kc_core/ollama_client.py kc-core/tests/test_ollama_client.py
git commit -m "feat(kc-core): add OllamaClient.chat_stream; refactor chat as accumulator"
```

---

## Task 3: kc-core Agent.send_stream

**Files:**
- Modify: `kc-core/src/kc_core/agent.py`
- Modify: `kc-core/tests/conftest.py`
- Modify: `kc-core/tests/test_agent.py`

**Why:** Add `Agent.send_stream(user_text)` that runs the same ReAct loop as `send` but yields `StreamFrame`s as it goes. Add `chat_stream` to the `_ChatClient` Protocol so type-checkers know what `client.chat_stream` is. Extend the test fake to script streaming responses.

- [ ] **Step 1: Extend `kc-core/tests/conftest.py` to support `chat_stream`**

Read current conftest:

```bash
cat kc-core/tests/conftest.py
```

It defines `FakeOllamaClient` with `responses: list[ChatResponse]` and `async def chat`. We add a parallel `stream_responses: list[list[ChatStreamFrame]]` and an async-generator `chat_stream`. If the test only set `responses`, `chat_stream` synthesizes a single-frame `[TextDelta(...) , Done(...)]` from each `ChatResponse` for backward compat.

Replace the `FakeOllamaClient` definition with:

```python
import pytest
from dataclasses import dataclass, field
from typing import Iterator, AsyncIterator, Any
from kc_core.ollama_client import ChatResponse
from kc_core.stream_frames import (
    ChatStreamFrame, TextDelta, ToolCallsBlock, Done,
)


@dataclass
class FakeOllamaClient:
    """Returns a scripted sequence of ChatResponse objects (for non-streaming callers)
    and/or scripted ChatStreamFrame sequences (for streaming callers).

    Each call to .chat() consumes one ChatResponse from `responses`.
    Each call to .chat_stream() consumes one frame list from `stream_responses`.
    If only `responses` is set, .chat_stream() synthesizes frame lists from them.
    """
    responses: list[ChatResponse] = field(default_factory=list)
    stream_responses: list[list[ChatStreamFrame]] = field(default_factory=list)
    calls: list = field(default_factory=list)
    model: str = "fake-model"
    _iter: Iterator[ChatResponse] | None = None
    _stream_iter: Iterator[list[ChatStreamFrame]] | None = None

    def __post_init__(self):
        self._iter = iter(self.responses)
        # If stream_responses is empty, synthesize from responses
        if not self.stream_responses and self.responses:
            synth: list[list[ChatStreamFrame]] = []
            for r in self.responses:
                frames: list[ChatStreamFrame] = []
                if r.text:
                    frames.append(TextDelta(content=r.text))
                if r.tool_calls:
                    frames.append(ToolCallsBlock(calls=r.tool_calls))
                frames.append(Done(finish_reason=r.finish_reason))
                synth.append(frames)
            self.stream_responses = synth
        self._stream_iter = iter(self.stream_responses)

    async def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return next(self._iter)

    async def chat_stream(self, messages, tools) -> AsyncIterator[ChatStreamFrame]:
        self.calls.append({"messages": messages, "tools": tools})
        frames = next(self._stream_iter)
        for f in frames:
            yield f


@pytest.fixture
def fake_ollama():
    def _make(*responses: ChatResponse, stream_responses: list[list[ChatStreamFrame]] | None = None) -> FakeOllamaClient:
        kwargs: dict[str, Any] = {"responses": list(responses)}
        if stream_responses is not None:
            kwargs["stream_responses"] = stream_responses
        return FakeOllamaClient(**kwargs)
    return _make
```

- [ ] **Step 2: Write failing tests in `kc-core/tests/test_agent.py`**

Append:

```python
import pytest
from kc_core.stream_frames import (
    TextDelta, ToolCallsBlock, Done,
    TokenDelta, ToolCallStart, ToolResult, Complete,
)


@pytest.mark.asyncio
async def test_agent_send_stream_yields_token_deltas_and_complete(fake_ollama):
    """Single-turn text-only response: tokens stream, then Complete."""
    client = fake_ollama(
        stream_responses=[[
            TextDelta(content="hello "),
            TextDelta(content="world"),
            Done(finish_reason="stop"),
        ]],
    )
    reg = ToolRegistry()
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    frames = []
    async for f in agent.send_stream("hi"):
        frames.append(f)

    assert isinstance(frames[0], TokenDelta)
    assert frames[0].content == "hello "
    assert isinstance(frames[1], TokenDelta)
    assert frames[1].content == "world"
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "hello world"


@pytest.mark.asyncio
async def test_agent_send_stream_runs_tool_call_between_turns(fake_ollama):
    """First model turn calls a tool. Second turn replies with text. Frames cover all phases."""
    client = fake_ollama(
        stream_responses=[
            # Turn 1: tool call
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            # Turn 2: final text
            [
                TextDelta(content="echoed: hi"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)

    frames = [f async for f in agent.send_stream("please echo")]
    types = [type(f).__name__ for f in frames]
    # Expected ordering: ToolCallStart, ToolResult, TokenDelta, Complete
    assert "ToolCallStart" in types
    assert "ToolResult" in types
    # ToolCallStart must come before ToolResult
    assert types.index("ToolCallStart") < types.index("ToolResult")
    # Token deltas come after tool result
    assert types.index("ToolResult") < types.index("TokenDelta")
    # Complete is last
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "echoed: hi"


@pytest.mark.asyncio
async def test_agent_send_stream_propagates_permission_deny(fake_ollama):
    """Sync deny callback short-circuits the tool — ToolResult content begins with 'Denied'."""
    client = fake_ollama(
        stream_responses=[
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            [
                TextDelta(content="couldn't run it"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    def deny_all(agent_name, tool_name, args):
        return (False, "no")

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=deny_all,
    )
    frames = [f async for f in agent.send_stream("please echo")]
    tool_results = [f for f in frames if isinstance(f, ToolResult)]
    assert len(tool_results) == 1
    assert "Denied" in tool_results[0].content


@pytest.mark.asyncio
async def test_agent_send_stream_supports_async_permission_check(fake_ollama):
    """Async permission check works with streaming."""
    client = fake_ollama(
        stream_responses=[
            [
                ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]),
                Done(finish_reason="tool_calls"),
            ],
            [
                TextDelta(content="ok"),
                Done(finish_reason="stop"),
            ],
        ],
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    async def async_allow(agent_name, tool_name, args):
        return (True, None)

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=async_allow,
    )
    frames = [f async for f in agent.send_stream("please echo")]
    assert isinstance(frames[-1], Complete)
    assert frames[-1].reply.content == "ok"


@pytest.mark.asyncio
async def test_agent_send_stream_raises_on_max_iterations(fake_ollama):
    """If the model loops forever asking for tool calls, send_stream eventually raises."""
    looping = [
        ToolCallsBlock(calls=[{"id": "c1", "name": "echo", "arguments": {"text": "x"}}]),
        Done(finish_reason="tool_calls"),
    ]
    client = fake_ollama(
        stream_responses=[looping] * 11,  # max_tool_iterations + 1
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg, max_tool_iterations=10)
    with pytest.raises(RuntimeError):
        async for _ in agent.send_stream("loop"):
            pass


@pytest.mark.asyncio
async def test_agent_send_stream_appends_history_same_as_send(fake_ollama):
    """After a streaming turn, agent.history should be in the same shape send() would leave it in."""
    client = fake_ollama(
        stream_responses=[[
            TextDelta(content="hi back"),
            Done(finish_reason="stop"),
        ]],
    )
    reg = ToolRegistry()
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    async for _ in agent.send_stream("hello"):
        pass
    assert len(agent.history) == 2
    assert agent.history[0].__class__.__name__ == "UserMessage"
    assert agent.history[1].__class__.__name__ == "AssistantMessage"
    assert agent.history[1].content == "hi back"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_agent.py -k send_stream -v
```

Expected: FAIL — `Agent` has no `send_stream` method.

- [ ] **Step 4: Add `chat_stream` to `_ChatClient` Protocol and implement `Agent.send_stream`**

In `kc-core/src/kc_core/agent.py`:

(a) Add to imports at top:
```python
from typing import AsyncIterator
from kc_core.stream_frames import (
    ChatStreamFrame, TextDelta, ToolCallsBlock, Done,
    StreamFrame, TokenDelta, ToolCallStart, ToolResult, Complete,
)
```

(b) Update `_ChatClient` Protocol to add `chat_stream`:

```python
class _ChatClient(Protocol):
    model: str
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]): ...
    def chat_stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ChatStreamFrame]: ...
```

(c) Add `send_stream` method to `Agent`. Place it right after `send`:

```python
    async def send_stream(self, user_text: str) -> AsyncIterator[StreamFrame]:
        """Streaming variant of send(). Yields StreamFrame objects as the model produces them.

        Same ReAct loop semantics as send: tool-using agents loop between model calls and
        tool execution. Permission denial is honored exactly like in send (deny path appends
        a 'Denied: ...' tool result and continues).
        """
        self.history.append(UserMessage(content=user_text))
        for _ in range(self.max_tool_iterations + 1):
            wire = self._build_wire_messages()
            text_parts: list[str] = []
            tool_calls_block: list[dict[str, Any]] | None = None
            finish: str = "stop"

            # Drain one model turn
            async for cs_frame in self.client.chat_stream(messages=wire, tools=self.tools.to_openai_schema()):
                if isinstance(cs_frame, TextDelta):
                    text_parts.append(cs_frame.content)
                    yield TokenDelta(content=cs_frame.content)
                elif isinstance(cs_frame, ToolCallsBlock):
                    tool_calls_block = cs_frame.calls
                elif isinstance(cs_frame, Done):
                    finish = cs_frame.finish_reason

            # Decide next phase: tool calls, or terminate
            calls: list[dict[str, Any]] = list(tool_calls_block) if tool_calls_block else []
            if not calls and "".join(text_parts):
                # Try the JSON-in-text fallback for tool calls embedded in text
                calls = parse_text_tool_calls("".join(text_parts), known_tools=self.tools.names())

            if not calls:
                reply = AssistantMessage(content="".join(text_parts))
                self.history.append(reply)
                yield Complete(reply=reply)
                return

            # Record tool calls in history (matching send()'s ordering invariant)
            results: list[tuple[str, str]] = []
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                yield ToolCallStart(call=c)

                if self.permission_check is not None:
                    pc_result = self.permission_check(self.name, c["name"], c["arguments"])
                    if inspect.iscoroutine(pc_result):
                        pc_result = await pc_result
                    allowed, reason = pc_result
                    if not allowed:
                        deny_msg = f"Denied: {reason or 'permission_check returned False'}"
                        results.append((c["id"], deny_msg))
                        yield ToolResult(call_id=c["id"], content=deny_msg)
                        continue

                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    content = str(result)
                except KeyError:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                results.append((c["id"], content))
                yield ToolResult(call_id=c["id"], content=content)

            for call_id, content in results:
                self.history.append(ToolResultMessage(
                    tool_call_id=call_id,
                    content=content,
                ))
            # loop continues — call the model again
        raise RuntimeError(f"Agent {self.name} exceeded max_tool_iterations={self.max_tool_iterations}")
```

- [ ] **Step 5: Run streaming tests**

```bash
pytest tests/test_agent.py -k send_stream -v
```

Expected: PASS — 6 new tests green.

- [ ] **Step 6: Run full kc-core suite**

```bash
pytest tests/ --ignore=tests/live -q
```

Expected: PASS — 50 (was) + 6 (new) = 56 tests green. All existing `send` tests still pass (no behavior change to `send` — only `chat` was refactored, and `send_stream` is a new method).

- [ ] **Step 7: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-core/src/kc_core/agent.py kc-core/tests/conftest.py kc-core/tests/test_agent.py
git commit -m "feat(kc-core): add Agent.send_stream + _ChatClient.chat_stream protocol"
```

---

## Task 4: kc-supervisor Storage schema fix (audit_undo_link.undo_op_id INTEGER)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Modify: `kc-supervisor/tests/test_storage.py`

**Why:** v1 declared `audit_undo_link.undo_op_id` as `TEXT`, but kc-sandbox's `UndoLog.record() -> int` returns an integer eid. Fix the schema and method signatures so v0.2's audit pipeline can write the correct type. v1 has no callers writing this table, so this is a clean swap.

- [ ] **Step 1: Modify the failing tests in `kc-supervisor/tests/test_storage.py`**

Find each test that uses string op_ids and replace with integer eids. Specifically the four affected tests:

```python
def test_audit_undo_link_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(
        agent="kc", tool="file.delete", args_json="{}",
        decision="destructive·callback", result="ok", undoable=True,
    )
    s.link_audit_undo(audit_id=aid, undo_op_id=42)
    assert s.get_undo_op_for_audit(aid) == 42


def test_audit_undo_link_missing_returns_none(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    assert s.get_undo_op_for_audit(99) is None


def test_audit_undo_link_idempotent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, 42)
    s.link_audit_undo(aid, 42)
    assert s.get_undo_op_for_audit(aid) == 42


def test_audit_undo_link_first_wins_on_conflict(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(agent="kc", tool="x", args_json="{}",
                         decision="d", result="r", undoable=True)
    s.link_audit_undo(aid, 100)
    s.link_audit_undo(aid, 200)
    assert s.get_undo_op_for_audit(aid) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor
source .venv/bin/activate
pytest tests/test_storage.py -v
```

Expected: 4 tests fail (some with type errors comparing int to "op-..." strings, some with subtle SQLite issues — column type is text, value goes in, gets back as int via Python coercion, comparison fails).

- [ ] **Step 3: Update schema and method signatures in `kc-supervisor/src/kc_supervisor/storage.py`**

In the `SCHEMA` constant, change:
```sql
CREATE TABLE IF NOT EXISTS audit_undo_link (
    audit_id INTEGER PRIMARY KEY,
    undo_op_id TEXT NOT NULL,
    FOREIGN KEY(audit_id) REFERENCES audit(id)
);
CREATE INDEX IF NOT EXISTS ix_link_undo ON audit_undo_link(undo_op_id);
```
to:
```sql
CREATE TABLE IF NOT EXISTS audit_undo_link (
    audit_id INTEGER PRIMARY KEY,
    undo_op_id INTEGER NOT NULL,
    FOREIGN KEY(audit_id) REFERENCES audit(id)
);
CREATE INDEX IF NOT EXISTS ix_link_undo ON audit_undo_link(undo_op_id);
```

Update method signatures:
```python
    def link_audit_undo(self, audit_id: int, undo_op_id: int) -> None:
        """Record that audit row `audit_id` produced kc-sandbox UndoLog row `undo_op_id`.

        First link wins — a second call with a different undo_op_id for the same audit_id
        is silently dropped (one tool call = one journal op in kc-sandbox's contract).
        """
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO audit_undo_link (audit_id, undo_op_id) VALUES (?,?)",
                (audit_id, undo_op_id),
            )

    def get_undo_op_for_audit(self, audit_id: int) -> Optional[int]:
        """Look up the kc-sandbox UndoLog eid for an audit row, if any."""
        with self.connect() as c:
            row = c.execute(
                "SELECT undo_op_id FROM audit_undo_link WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
        return row["undo_op_id"] if row else None
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_storage.py -v
```

Expected: PASS — all 11 storage tests green.

- [ ] **Step 5: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: PASS — 50 tests green (no other test depended on the old TEXT type).

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_storage.py
git commit -m "fix(kc-supervisor): audit_undo_link.undo_op_id should be INTEGER (kc-sandbox UndoLog eid)"
```

---

## Task 5: kc-supervisor ConversationLocks

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/locks.py`
- Test: `kc-supervisor/tests/test_locks.py`

**Why:** Per-conversation `asyncio.Lock`s prevent two WS clients on the same cid from racing each other through `Agent.history` rebuild + `send_stream` + persistence. Different cids run in parallel.

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_locks.py`:

```python
import asyncio
import pytest
from kc_supervisor.locks import ConversationLocks


def test_get_returns_lock():
    cl = ConversationLocks()
    lock = cl.get(1)
    assert isinstance(lock, asyncio.Lock)


def test_same_cid_returns_same_lock():
    cl = ConversationLocks()
    assert cl.get(1) is cl.get(1)


def test_different_cids_get_different_locks():
    cl = ConversationLocks()
    assert cl.get(1) is not cl.get(2)


@pytest.mark.asyncio
async def test_concurrent_acquires_on_same_cid_serialize():
    """If task A holds the lock for cid=1, task B has to wait."""
    cl = ConversationLocks()
    order: list[str] = []

    async def task_a():
        async with cl.get(1):
            order.append("a-start")
            await asyncio.sleep(0.05)
            order.append("a-end")

    async def task_b():
        await asyncio.sleep(0.01)  # ensure A grabs first
        async with cl.get(1):
            order.append("b-start")
            order.append("b-end")

    await asyncio.gather(task_a(), task_b())
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_different_cids_run_in_parallel():
    """Two concurrent acquires on different cids do not block each other."""
    cl = ConversationLocks()
    started: list[int] = []
    finished: list[int] = []

    async def task(cid: int):
        async with cl.get(cid):
            started.append(cid)
            await asyncio.sleep(0.05)
            finished.append(cid)

    await asyncio.gather(task(1), task(2))
    # Both should have started before either finished
    assert started == [1, 2] or started == [2, 1]
    assert sorted(finished) == [1, 2]
    # Total elapsed should be ~50ms not ~100ms — covered by gather timing implicitly
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_locks.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `kc-supervisor/src/kc_supervisor/locks.py`**

```python
from __future__ import annotations
import asyncio


class ConversationLocks:
    """Lazy per-conversation-id asyncio locks.

    Locks are created on first access and never evicted. For a single-user local
    app with finite conversations this is fine; switch to ``WeakValueDictionary``
    if multi-user support ever lands.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, cid: int) -> asyncio.Lock:
        """Return the lock for ``cid``, creating it on first call."""
        lock = self._locks.get(cid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cid] = lock
        return lock
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_locks.py -v
```

Expected: PASS — 5 tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/locks.py kc-supervisor/tests/test_locks.py
git commit -m "feat(kc-supervisor): add ConversationLocks for per-cid serialization"
```

---

## Task 6: kc-supervisor audit_tools (RecordingUndoLog + AuditingToolRegistry + decision contextvar)

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/audit_tools.py`
- Test: `kc-supervisor/tests/test_audit_tools.py`

**Why:** This is the heart of v0.2's audit pipeline. Three pieces:

1. `_decision_contextvar` and `_eid_contextvar` — `contextvars.ContextVar` instances that carry the most-recent `Decision` and the most-recent `UndoLog.record()` eid through the kc-core async tool-execution path. Per-cid serialization (the WS lock) guarantees no cross-tool bleed within a single agent turn.
2. `RecordingUndoLog` — subclass of `kc_sandbox.undo.UndoLog` that, on every `record()` call, writes the returned eid into `_eid_contextvar` before returning.
3. `AuditingToolRegistry` — wraps each registered tool's `impl` so that, after the tool runs (or raises), it writes the audit row using the captured Decision + result and (if a new eid was captured) calls `audit_storage.link_audit_undo(audit_id, eid)`.
4. `make_audit_aware_callback(engine, agent_name)` — returns an async permission_check that calls `engine.check_async`, sets `_decision_contextvar`, and returns `(decision.allowed, decision.reason)`. Replaces `engine.to_async_agent_callback`.

- [ ] **Step 1: Write failing tests**

Create `kc-supervisor/tests/test_audit_tools.py`:

```python
import asyncio
import pytest
from pathlib import Path
from kc_core.tools import Tool, ToolRegistry
from kc_sandbox.permissions import PermissionEngine, Tier
from kc_sandbox.undo import UndoEntry
from kc_supervisor.storage import Storage
from kc_supervisor.audit_tools import (
    RecordingUndoLog, AuditingToolRegistry, make_audit_aware_callback,
    _decision_contextvar, _eid_contextvar,
)


def test_recording_undo_log_captures_eid_in_contextvar(tmp_path):
    log = RecordingUndoLog(tmp_path / "u.db"); log.init()
    _eid_contextvar.set(None)
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write",
        reverse_kind="git-revert", reverse_payload={"share": "r", "sha": "abc"},
    ))
    assert isinstance(eid, int)
    assert _eid_contextvar.get() == eid


def test_auditing_tool_registry_writes_audit_row_on_success(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")
    reg.register(Tool(name="echo", description="", parameters={},
                      impl=lambda text: f"echoed: {text}"))

    # Simulate the permission-check callback having stashed a Decision
    from kc_sandbox.permissions import Decision
    _decision_contextvar.set(Decision(allowed=True, tier=Tier.SAFE, source="tier", reason=None))
    _eid_contextvar.set(None)

    result = reg.invoke("echo", {"text": "hi"})
    assert result == "echoed: hi"

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["agent"] == "kc"
    assert rows[0]["tool"] == "echo"
    assert rows[0]["decision"] == "tier"
    assert rows[0]["result"] == "echoed: hi"
    assert rows[0]["undoable"] == 0
    # No link row written
    assert storage.get_undo_op_for_audit(rows[0]["id"]) is None


def test_auditing_tool_registry_writes_link_row_when_eid_present(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")

    def journaling_tool(text):
        # Simulate the underlying impl recording an undo entry
        _eid_contextvar.set(99)
        return f"wrote {text}"

    reg.register(Tool(name="file.write", description="", parameters={},
                      impl=journaling_tool))

    from kc_sandbox.permissions import Decision
    _decision_contextvar.set(Decision(
        allowed=True, tier=Tier.MUTATING, source="tier", reason=None,
    ))
    _eid_contextvar.set(None)

    reg.invoke("file.write", {"text": "x"})

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["undoable"] == 1
    assert storage.get_undo_op_for_audit(rows[0]["id"]) == 99


def test_auditing_tool_registry_writes_audit_row_on_exception(tmp_path):
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")

    def boom(text):
        raise ValueError(f"boom: {text}")

    reg.register(Tool(name="bad", description="", parameters={}, impl=boom))

    from kc_sandbox.permissions import Decision
    _decision_contextvar.set(Decision(allowed=True, tier=Tier.SAFE, source="tier", reason=None))
    _eid_contextvar.set(None)

    with pytest.raises(ValueError):
        reg.invoke("bad", {"text": "x"})

    rows = storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["result"].startswith("Error: ValueError: boom")
    assert rows[0]["undoable"] == 0


def test_auditing_tool_registry_uses_destructive_decision_source(tmp_path):
    """Decision.source 'override+callback' is recorded verbatim."""
    storage = Storage(tmp_path / "kc.db"); storage.init()
    reg = AuditingToolRegistry(audit_storage=storage, agent_name="kc")
    reg.register(Tool(name="t", description="", parameters={}, impl=lambda: "ok"))

    from kc_sandbox.permissions import Decision
    _decision_contextvar.set(Decision(
        allowed=True, tier=Tier.DESTRUCTIVE, source="override+callback", reason=None,
    ))
    _eid_contextvar.set(None)

    reg.invoke("t", {})
    rows = storage.list_audit()
    assert rows[0]["decision"] == "override+callback"


@pytest.mark.asyncio
async def test_make_audit_aware_callback_sets_decision_contextvar():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=lambda agent, tool, args: (True, None),
    )
    cb = make_audit_aware_callback(eng, agent_name="kc")
    _decision_contextvar.set(None)

    allowed, reason = await cb("ignored-runtime-name", "file.delete", {})
    assert allowed is True
    d = _decision_contextvar.get()
    assert d is not None
    assert d.tier == Tier.DESTRUCTIVE
    assert d.source == "callback"


@pytest.mark.asyncio
async def test_decision_contextvar_isolation_between_concurrent_tool_calls():
    """Two parallel tool invocations don't cross-pollinate decisions."""
    from kc_sandbox.permissions import Decision

    async def fake_tool_call(label: str, decision_source: str, results: dict):
        _decision_contextvar.set(Decision(
            allowed=True, tier=Tier.SAFE, source=decision_source, reason=None,
        ))
        await asyncio.sleep(0.01)
        results[label] = _decision_contextvar.get().source

    results: dict = {}
    await asyncio.gather(
        fake_tool_call("a", "source-a", results),
        fake_tool_call("b", "source-b", results),
    )
    assert results == {"a": "source-a", "b": "source-b"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_audit_tools.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `kc-supervisor/src/kc_supervisor/audit_tools.py`**

```python
from __future__ import annotations
import contextvars
import json
from typing import Any, Optional
from kc_core.tools import Tool, ToolRegistry
from kc_sandbox.permissions import Decision, PermissionEngine
from kc_sandbox.undo import UndoLog, UndoEntry
from kc_supervisor.storage import Storage


# Contextvars that thread Decision and eid through the async tool-execution path.
# Per-cid lock (in ws_routes) guarantees no cross-conversation bleed.
_decision_contextvar: contextvars.ContextVar[Optional[Decision]] = contextvars.ContextVar(
    "kc_supervisor_decision", default=None,
)
_eid_contextvar: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "kc_supervisor_eid", default=None,
)


class RecordingUndoLog(UndoLog):
    """UndoLog subclass that captures each record()'s returned eid into a contextvar.

    Used by kc-supervisor's audit pipeline so the AuditingToolRegistry can link an
    audit row to its journal op without modifying kc-sandbox's tool surface.
    """

    def record(self, e: UndoEntry) -> int:
        eid = super().record(e)
        _eid_contextvar.set(eid)
        return eid


class AuditingToolRegistry(ToolRegistry):
    """ToolRegistry that wraps each registered tool's impl with an audit writer.

    After every invoke (success or exception):
      - Writes one row to the supervisor's `audit` table, capturing the Decision
        (set by make_audit_aware_callback earlier in the same async task) and the
        tool result (or stringified exception).
      - If a new eid was captured by RecordingUndoLog during the invoke, writes
        a corresponding `audit_undo_link` row.
    """

    def __init__(self, *, audit_storage: Storage, agent_name: str) -> None:
        super().__init__()
        self._audit_storage = audit_storage
        self._agent_name = agent_name

    def register(self, tool: Tool) -> None:  # type: ignore[override]
        wrapped = self._wrap(tool)
        super().register(wrapped)

    def _wrap(self, tool: Tool) -> Tool:
        original_impl = tool.impl
        agent_name = self._agent_name
        storage = self._audit_storage

        def audited_impl(*args, **kwargs):
            # Reset eid contextvar — only count eids written during THIS call
            _eid_contextvar.set(None)
            decision = _decision_contextvar.get()
            decision_source = decision.source if decision is not None else "unknown"

            args_json = json.dumps(kwargs if kwargs else list(args), default=str)

            try:
                result = original_impl(*args, **kwargs)
                result_str = str(result)
                exc: Optional[BaseException] = None
            except Exception as e:
                result_str = f"Error: {type(e).__name__}: {e}"
                exc = e

            captured_eid = _eid_contextvar.get()
            undoable = captured_eid is not None
            audit_id = storage.append_audit(
                agent=agent_name,
                tool=tool.name,
                args_json=args_json,
                decision=decision_source,
                result=result_str,
                undoable=undoable,
            )
            if captured_eid is not None:
                storage.link_audit_undo(audit_id, captured_eid)

            if exc is not None:
                raise exc
            return result

        return Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            impl=audited_impl,
        )


def make_audit_aware_callback(engine: PermissionEngine, *, agent_name: str):
    """Return an async permission_check that calls engine.check_async and stashes
    the resulting Decision into _decision_contextvar so AuditingToolRegistry can
    record it after the tool runs.

    Replaces engine.to_async_agent_callback when wiring an AssembledAgent.
    """

    async def _check(_runtime_agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
        d = await engine.check_async(agent=agent_name, tool=tool, arguments=args)
        _decision_contextvar.set(d)
        return (d.allowed, d.reason)

    return _check
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_audit_tools.py -v
```

Expected: PASS — 7 tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/audit_tools.py kc-supervisor/tests/test_audit_tools.py
git commit -m "feat(kc-supervisor): add audit_tools (RecordingUndoLog, AuditingToolRegistry, decision contextvar)"
```

---

## Task 7: kc-supervisor assembly (AssembledAgent + assemble_agent)

**Files:**
- Create: `kc-supervisor/src/kc_supervisor/assembly.py`
- Test: `kc-supervisor/tests/test_assembly.py`

**Why:** kc-supervisor takes ownership of agent assembly — pulling together the kc-sandbox primitives and kc-core's `Agent` directly, so it can inject `RecordingUndoLog`, audit-wrap tools, and use the audit-aware permission callback. `kc_sandbox.wiring.build_sandboxed_agent` stays as-is for the `konaclaw` CLI; we don't call it.

- [ ] **Step 1: Read kc-sandbox primitives we'll consume**

```bash
grep -n "^class\|^def \|from_yaml\|build_file_tools" kc-sandbox/src/kc_sandbox/shares.py kc-sandbox/src/kc_sandbox/tools.py | head -20
```

Confirm: `SharesRegistry.from_yaml(path)`, `SharesRegistry.names()`, `SharesRegistry.get(name)`, `Journal(share_path)`, `build_file_tools(shares, journals, undo_log, agent_name) -> dict[str, Tool]`, `DEFAULT_FILE_TOOL_TIERS: dict[str, Tier]`, `PermissionEngine(tier_map, agent_overrides, approval_callback)`.

- [ ] **Step 2: Write failing tests**

Create `kc-supervisor/tests/test_assembly.py`:

```python
import asyncio
import pytest
import yaml
from pathlib import Path
from kc_core.config import AgentConfig, load_agent_config
from kc_sandbox.shares import SharesRegistry
from kc_sandbox.permissions import Tier
from kc_supervisor.storage import Storage
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.assembly import AssembledAgent, assemble_agent


@pytest.fixture
def home(tmp_path):
    """A populated KC_HOME with one share and one agent yaml."""
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "shares").mkdir(parents=True)
    (home / "shares" / "main").mkdir()

    # shares.yaml
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "writable": True}],
    }))
    # agent yaml
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: qwen2.5:7b\nsystem_prompt: I am alice.\n"
    )
    return home


def test_assemble_agent_happy_path(home):
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = load_agent_config(home / "agents" / "alice.yaml")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )

    assert isinstance(a, AssembledAgent)
    assert a.name == "alice"
    assert a.system_prompt == "I am alice."
    # File tools registered
    tool_names = a.registry.names()
    assert "file.read" in tool_names
    assert "file.write" in tool_names
    assert "file.list" in tool_names
    assert "file.delete" in tool_names
    # OllamaClient model from YAML
    assert a.ollama_client.model == "qwen2.5:7b"
    # Journals exist for each share
    assert "main" in a.journals
    # UndoLog exists
    assert a.undo_log is not None
    # PermissionEngine has the default tier map
    assert a.engine.tier_map["file.delete"] == Tier.DESTRUCTIVE
    # The kc_core.Agent is built and ready
    assert a.core_agent.name == "alice"


def test_assemble_agent_uses_default_model_when_yaml_omits(home):
    """If agent YAML has no model field, default_model is used."""
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nsystem_prompt: I am alice.\n"
    )
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = load_agent_config(home / "agents" / "alice.yaml", default_model="qwen2.5:7b")

    a = assemble_agent(
        cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
        ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    assert a.ollama_client.model == "qwen2.5:7b"


def test_assemble_agent_parses_permission_overrides(home, monkeypatch):
    """If kc-core's AgentConfig surfaces permission_overrides, assembly maps it into engine.agent_overrides."""
    # NOTE: kc-core v1's AgentConfig only carries (name, model, system_prompt). For v0.2,
    # if permission_overrides parsing in kc-core lands later, this test pins the contract.
    # For now we verify the path is wired by passing overrides explicitly via a config-shim.
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg,
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
        permission_overrides={"file.read": Tier.DESTRUCTIVE},
    )
    # The engine has an override for "alice"/"file.read"
    assert a.engine.agent_overrides["alice"]["file.read"] == Tier.DESTRUCTIVE


def test_assemble_agent_raises_on_bad_share_path(home):
    """If a share's path doesn't exist on disk, assembly raises (registry will mark agent DEGRADED)."""
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "nonexistent"), "writable": True}],
    }))
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    # SharesRegistry.from_yaml may or may not validate paths up front;
    # Journal init will surface the issue. Either way, we expect an exception.
    with pytest.raises(Exception):
        shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
        assemble_agent(
            cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
            ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
            undo_db_path=home / "data" / "undo.db",
        )


def test_assemble_agent_uses_audit_aware_callback(home):
    """Permission check is the audit-aware variant — set the decision contextvar on call."""
    from kc_supervisor.audit_tools import _decision_contextvar
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    cfg = AgentConfig(name="alice", model="qwen2.5:7b", system_prompt="hi")

    a = assemble_agent(
        cfg=cfg, shares=shares, audit_storage=storage, broker=broker,
        ollama_url="http://localhost:11434", default_model="qwen2.5:7b",
        undo_db_path=home / "data" / "undo.db",
    )
    # The Agent's permission_check should be a coroutine function — calling it returns a coroutine
    cb = a.core_agent.permission_check
    assert cb is not None
    coro = cb("alice", "file.read", {"share": "main", "relpath": "x"})
    import inspect
    assert inspect.iscoroutine(coro)

    async def runner():
        _decision_contextvar.set(None)
        await coro
        d = _decision_contextvar.get()
        assert d is not None
        # file.read is SAFE in the default tier map; broker isn't called
        assert d.tier == Tier.SAFE

    asyncio.run(runner())
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_assembly.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 4: Implement `kc-supervisor/src/kc_supervisor/assembly.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from kc_core.agent import Agent as CoreAgent
from kc_core.config import AgentConfig
from kc_core.ollama_client import OllamaClient
from kc_sandbox.shares import SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.tools import build_file_tools, DEFAULT_FILE_TOOL_TIERS
from kc_sandbox.permissions import PermissionEngine, Tier
from kc_supervisor.audit_tools import (
    RecordingUndoLog, AuditingToolRegistry, make_audit_aware_callback,
)
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.storage import Storage


@dataclass
class AssembledAgent:
    """A fully-wired agent: kc-core Agent + kc-sandbox primitives + supervisor audit hooks.

    Held by AgentRuntime. The kc-core Agent's `history` is reset before each turn from
    SQLite via ConversationManager — never carry per-turn state on this dataclass.
    """
    name: str
    system_prompt: str
    ollama_client: OllamaClient
    registry: AuditingToolRegistry
    engine: PermissionEngine
    journals: dict[str, Journal]
    undo_log: RecordingUndoLog
    core_agent: CoreAgent


def assemble_agent(
    *,
    cfg: AgentConfig,
    shares: SharesRegistry,
    audit_storage: Storage,
    broker: ApprovalBroker,
    ollama_url: str,
    default_model: str,
    undo_db_path: Path,
    permission_overrides: Optional[dict[str, Tier]] = None,
) -> AssembledAgent:
    """Build an AssembledAgent from a YAML config + supervisor singletons.

    Steps:
      1. Per-share Journals (init on disk if missing).
      2. RecordingUndoLog (single instance; eids are global across shares).
      3. AuditingToolRegistry; register kc-sandbox file tools (each is wrapped to audit).
      4. PermissionEngine with overrides for this agent and the broker as approval callback.
      5. kc-core Agent with the audit-aware async permission_check.
    """
    # 1. Journals
    journals: dict[str, Journal] = {name: Journal(shares.get(name).path) for name in shares.names()}
    for j in journals.values():
        j.init()

    # 2. Recording undo log
    undo_log = RecordingUndoLog(undo_db_path)
    undo_log.init()

    # 3. Tools — build raw, then register through the auditing registry (which wraps each)
    file_tools = build_file_tools(
        shares=shares,
        journals=journals,
        undo_log=undo_log,
        agent_name=cfg.name,
    )
    registry = AuditingToolRegistry(audit_storage=audit_storage, agent_name=cfg.name)
    for tool in file_tools.values():
        registry.register(tool)

    # 4. Permission engine — broker.request_approval is the destructive-tier callback
    overrides_for_agent = {cfg.name: permission_overrides} if permission_overrides else {}
    engine = PermissionEngine(
        tier_map=dict(DEFAULT_FILE_TOOL_TIERS),
        agent_overrides=overrides_for_agent,
        approval_callback=lambda agent, tool, args: broker.request_approval(
            agent=agent, tool=tool, arguments=args,
        ),
    )

    # 5. OllamaClient + kc-core Agent
    model = cfg.model or default_model
    ollama_client = OllamaClient(model=model, url=ollama_url)

    core_agent = CoreAgent(
        name=cfg.name,
        client=ollama_client,
        system_prompt=cfg.system_prompt,
        tools=registry,
        permission_check=make_audit_aware_callback(engine, agent_name=cfg.name),
    )

    return AssembledAgent(
        name=cfg.name,
        system_prompt=cfg.system_prompt,
        ollama_client=ollama_client,
        registry=registry,
        engine=engine,
        journals=journals,
        undo_log=undo_log,
        core_agent=core_agent,
    )
```

NOTE on broker as `approval_callback`: kc-sandbox's `ApprovalCallback` Protocol is sync, returning `(allowed, reason)`. But `broker.request_approval` is async, returning `Awaitable[(allowed, reason)]`. The lambda above wraps it — calling it returns a coroutine. `engine.check_async` (added in v1) detects coroutines via `inspect.iscoroutine` and awaits them. So this works as long as `engine.check_async` is the path, which `make_audit_aware_callback` ensures.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_assembly.py -v
```

Expected: PASS — 5 tests green. (The `permission_overrides` test verifies the wiring; if AgentConfig later surfaces permission_overrides natively, the call site in load_all is the only change.)

- [ ] **Step 6: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: PASS — 50 + 5 (locks) + 7 (audit_tools) + 5 (assembly) = 67 tests. Plus the schema-fix change to existing storage tests didn't add count. Total ~67.

- [ ] **Step 7: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/assembly.py kc-supervisor/tests/test_assembly.py
git commit -m "feat(kc-supervisor): add assembly module (AssembledAgent + assemble_agent)"
```

---

## Task 8: kc-supervisor AgentRegistry refactor (build AssembledAgents at load_all)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/agents.py`
- Modify: `kc-supervisor/tests/test_agents.py`
- Modify: `kc-supervisor/tests/conftest.py`

**Why:** v1's `AgentRegistry` was config-only. v0.2 wires it to assembly: each YAML becomes an `AssembledAgent` (or DEGRADED on failure). Add the dependencies (`audit_storage`, `broker`, `shares`, `ollama_url`, `undo_db_path`) to `__init__`. `AgentRuntime.assembled` replaces v1's `core_agent: Optional[CoreAgent]`.

- [ ] **Step 1: Update conftest.py to provide a richer `deps` fixture**

The existing `deps` fixture in `kc-supervisor/tests/conftest.py` doesn't pass assembly deps to the registry yet. Update it (read the current file, then replace the registry construction):

```python
from pathlib import Path
import pytest
import yaml
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.service import Deps, create_app
from kc_sandbox.shares import SharesRegistry


@pytest.fixture
def deps(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)

    # Two minimal agents
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: hi from alice\n"
    )
    (home / "agents" / "bob.yaml").write_text(
        "name: bob\nmodel: fake-model\nsystem_prompt: hi from bob\n"
    )

    # shares.yaml — one share so assembly succeeds
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "writable": True}],
    }))

    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
    )
    registry.load_all()
    convs = ConversationManager(storage=storage)
    return Deps(
        storage=storage,
        registry=registry,
        conversations=convs,
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=ConversationLocks(),
    )


@pytest.fixture
def app(deps):
    return create_app(deps)
```

(The `Deps` dataclass gains `shares` and `conv_locks` in Task 11; tests will fail temporarily until that's wired. We're updating conftest now to set the test target — the implementation tasks bring `Deps` into compliance.)

- [ ] **Step 2: Update existing test_agents.py to use new constructor + assert assembled state**

In `kc-supervisor/tests/test_agents.py`, every test currently constructs `AgentRegistry` directly with `agents_dir + shares_yaml + undo_db + default_model`. Update each to use the v0.2 signature, and add new tests for degraded handling.

Replace the entire test file body with:

```python
from pathlib import Path
import pytest
import yaml
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry, AgentRuntime, AgentStatus
from kc_supervisor.assembly import AssembledAgent
from kc_supervisor.storage import Storage
from kc_supervisor.approvals import ApprovalBroker


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: I am alice\n"
    )
    (home / "agents" / "bob.yaml").write_text(
        "name: bob\nmodel: fake-model\nsystem_prompt: I am bob\n"
    )
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "writable": True}],
    }))
    return home


def _build_registry(home: Path) -> AgentRegistry:
    storage = Storage(home / "data" / "kc.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    return AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url="http://localhost:11434",
        default_model="fake-model",
        undo_db_path=home / "data" / "undo.db",
    )


def test_load_from_dir(home):
    reg = _build_registry(home)
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]
    rt = reg.get("alice")
    assert rt.status == AgentStatus.IDLE
    assert isinstance(rt.assembled, AssembledAgent)


def test_get_unknown_raises(home):
    reg = _build_registry(home)
    reg.load_all()
    with pytest.raises(KeyError):
        reg.get("ghost")


def test_status_transitions(home):
    reg = _build_registry(home)
    reg.load_all()
    rt = reg.get("alice")
    rt.set_status(AgentStatus.THINKING)
    snap = reg.snapshot()
    alice_entry = next(e for e in snap if e["name"] == "alice")
    assert alice_entry["status"] == "thinking"


def test_disable_and_enable(home):
    reg = _build_registry(home)
    reg.load_all()
    reg.disable("alice")
    assert reg.get("alice").status == AgentStatus.DISABLED
    reg.enable("alice")
    assert reg.get("alice").status == AgentStatus.IDLE


def test_load_all_idempotent(home):
    reg = _build_registry(home)
    reg.load_all()
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]


def test_snapshot_shape(home):
    reg = _build_registry(home)
    reg.load_all()
    snap = reg.snapshot()
    assert len(snap) == 2
    for entry in snap:
        assert set(entry.keys()) == {"name", "model", "status", "last_error"}


def test_load_all_degrades_on_assembly_failure(home):
    """If a YAML's agent points at a missing share path, assembly fails — runtime ends up DEGRADED.

    The shares.yaml is rewritten BEFORE _build_registry is called so that
    SharesRegistry.from_yaml picks up the bad path. assemble_agent's
    Journal.init() then raises when it tries to operate on the missing dir.
    """
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "nonexistent_share"), "writable": True}],
    }))
    reg = _build_registry(home)
    reg.load_all()
    rt = reg.get("alice")
    assert rt.status == AgentStatus.DEGRADED
    assert rt.last_error is not None
    assert rt.assembled is None


def test_load_all_degrades_one_keeps_others(home):
    """A second yaml with a syntax error should not stop the registry from loading the first."""
    (home / "agents" / "broken.yaml").write_text(":::not yaml:::")
    reg = _build_registry(home)
    reg.load_all()
    # alice and bob should still load. broken should be present but DEGRADED.
    names = reg.names()
    assert "alice" in names
    assert "bob" in names
    assert "broken" in names
    assert reg.get("broken").status == AgentStatus.DEGRADED
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_agents.py -v
```

Expected: FAIL — `AgentRegistry.__init__` signature doesn't match.

- [ ] **Step 4: Update `kc-supervisor/src/kc_supervisor/agents.py`**

Replace the entire file with:

```python
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from kc_core.config import load_agent_config
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.assembly import AssembledAgent, assemble_agent
from kc_supervisor.storage import Storage

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    PAUSED = "paused"
    DISABLED = "disabled"
    DEGRADED = "degraded"


@dataclass
class AgentRuntime:
    name: str
    model: str
    system_prompt: str
    yaml_path: Path
    status: AgentStatus = AgentStatus.IDLE
    last_error: Optional[str] = None
    # Set on successful assembly. None means assembly failed (status=DEGRADED).
    assembled: Optional[AssembledAgent] = None

    def set_status(self, s: AgentStatus) -> None:
        self.status = s

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "status": self.status.value,
            "last_error": self.last_error,
        }


class AgentRegistry:
    """Loads agents from YAML files and constructs an AssembledAgent per file.

    On assembly failure (bad YAML, missing share, etc.) the runtime is created with
    status=DEGRADED, last_error set, and assembled=None. The supervisor still boots.
    """

    def __init__(
        self, *,
        agents_dir: Path,
        shares: SharesRegistry,
        audit_storage: Storage,
        broker: ApprovalBroker,
        ollama_url: str,
        default_model: str,
        undo_db_path: Path,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.shares = shares
        self.audit_storage = audit_storage
        self.broker = broker
        self.ollama_url = ollama_url
        self.default_model = default_model
        self.undo_db_path = Path(undo_db_path)
        self._by_name: dict[str, AgentRuntime] = {}

    def load_all(self) -> None:
        """Re-read every *.yaml in agents_dir. Replaces existing entries.

        Per-yaml failures (load_agent_config or assemble_agent raising) result in a
        DEGRADED runtime entry rather than aborting the whole load.
        """
        self._by_name.clear()
        for p in sorted(self.agents_dir.glob("*.yaml")):
            try:
                cfg = load_agent_config(p, default_model=self.default_model)
            except Exception as e:
                # Synthesize a degraded entry using the file stem as a fallback name
                stem = p.stem
                logger.warning("load_agent_config failed for %s: %s", p, e)
                self._by_name[stem] = AgentRuntime(
                    name=stem,
                    model="?",
                    system_prompt="",
                    yaml_path=p,
                    status=AgentStatus.DEGRADED,
                    last_error=f"load_agent_config: {e}",
                    assembled=None,
                )
                continue

            try:
                assembled = assemble_agent(
                    cfg=cfg,
                    shares=self.shares,
                    audit_storage=self.audit_storage,
                    broker=self.broker,
                    ollama_url=self.ollama_url,
                    default_model=self.default_model,
                    undo_db_path=self.undo_db_path,
                )
                self._by_name[cfg.name] = AgentRuntime(
                    name=cfg.name,
                    model=cfg.model,
                    system_prompt=cfg.system_prompt,
                    yaml_path=p,
                    assembled=assembled,
                )
            except Exception as e:
                logger.warning("assemble_agent failed for %s: %s", p, e)
                self._by_name[cfg.name] = AgentRuntime(
                    name=cfg.name,
                    model=cfg.model,
                    system_prompt=cfg.system_prompt,
                    yaml_path=p,
                    status=AgentStatus.DEGRADED,
                    last_error=f"assemble_agent: {e}",
                    assembled=None,
                )

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def get(self, name: str) -> AgentRuntime:
        if name not in self._by_name:
            raise KeyError(f"unknown agent: {name}")
        return self._by_name[name]

    def disable(self, name: str) -> None:
        self.get(name).set_status(AgentStatus.DISABLED)

    def enable(self, name: str) -> None:
        self.get(name).set_status(AgentStatus.IDLE)

    def snapshot(self) -> list[dict[str, Any]]:
        return [rt.to_dict() for rt in self._by_name.values()]
```

- [ ] **Step 5: Run agents tests**

```bash
pytest tests/test_agents.py -v
```

Expected: PASS — 8 tests green (6 existing-shape + 2 new degraded). The conftest changes will cause other tests to fail until `Deps` is updated in Task 11; we run the agents tests in isolation here.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/agents.py kc-supervisor/tests/test_agents.py kc-supervisor/tests/conftest.py
git commit -m "feat(kc-supervisor): AgentRegistry constructs AssembledAgent per YAML; degraded handling"
```

---

## Task 9: kc-supervisor Deps + service.py + main.py wiring

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/service.py`
- Modify: `kc-supervisor/src/kc_supervisor/main.py`
- Modify: `kc-supervisor/tests/test_http.py` (verify /health still works)

**Why:** Bring the dependency bundle into compliance with the conftest fixture: `Deps` gains `shares` and `conv_locks`. `main()` constructs everything in the right order, consuming `KC_OLLAMA_URL`.

- [ ] **Step 1: Modify `kc-supervisor/src/kc_supervisor/service.py`**

Replace the file body with:

```python
from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from fastapi import FastAPI
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.storage import Storage


@dataclass
class Deps:
    """Dependency bundle injected into the FastAPI app at construction.

    Tests build their own Deps with tmp_path-backed components.
    Production wiring lives in main.py.
    """
    storage: Storage
    registry: AgentRegistry
    conversations: ConversationManager
    approvals: ApprovalBroker
    home: Path
    shares: SharesRegistry
    conv_locks: ConversationLocks
    started_at: float = field(default_factory=time.time)


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kc-supervisor")
    app.state.deps = deps

    from kc_supervisor.http_routes import register_http_routes
    register_http_routes(app)

    from kc_supervisor.ws_routes import register_ws_routes
    register_ws_routes(app)

    return app
```

- [ ] **Step 2: Modify `kc-supervisor/src/kc_supervisor/main.py`**

Replace the file body with:

```python
from __future__ import annotations
import os
from pathlib import Path
import uvicorn
from kc_sandbox.shares import SharesRegistry
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks
from kc_supervisor.service import Deps, create_app
from kc_supervisor.storage import Storage


def main() -> None:
    home = Path(os.environ.get("KC_HOME", str(Path.home() / "KonaClaw")))
    default_model = os.environ.get("KC_DEFAULT_MODEL", "qwen2.5:7b")
    ollama_url = os.environ.get("KC_OLLAMA_URL", "http://localhost:11434")

    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    if not (home / "config" / "shares.yaml").exists():
        (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "konaclaw.db"); storage.init()
    broker = ApprovalBroker()
    shares = SharesRegistry.from_yaml(home / "config" / "shares.yaml")
    conv_locks = ConversationLocks()

    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares=shares,
        audit_storage=storage,
        broker=broker,
        ollama_url=ollama_url,
        default_model=default_model,
        undo_db_path=home / "data" / "undo.db",
    )
    registry.load_all()

    deps = Deps(
        storage=storage,
        registry=registry,
        conversations=ConversationManager(storage),
        approvals=broker,
        home=home,
        shares=shares,
        conv_locks=conv_locks,
    )
    app = create_app(deps)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_PORT", "8765")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run /health test to confirm app still boots**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor
source .venv/bin/activate
pytest tests/test_http.py -k health -v
```

Expected: PASS. If failing, the `Deps` fields don't match what conftest uses — re-check.

- [ ] **Step 4: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: most tests PASS now that `Deps` matches conftest. Some existing test_ws_chat / test_ws_approvals / test_http tests may still rely on v1 behavior — list any failures and address in their respective tasks (Task 10 and Task 12).

If new failures appear due to assembly-related side effects (e.g., bad share path in fixture), fix the fixture in this task — the goal is `pytest tests/` green except for the planned ws_chat / http_routes rewrites coming next.

- [ ] **Step 5: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/service.py kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-supervisor): Deps gains shares + conv_locks; main consumes KC_OLLAMA_URL"
```

---

## Task 10: kc-supervisor ws_chat rewrite (streaming + per-cid lock + history rehydration)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py`
- Modify: `kc-supervisor/tests/test_ws_chat.py`

**Why:** The ws_chat handler is the user-facing surface for everything we built. Each turn now: acquire per-cid lock → look up assembled → rehydrate kc-core `Agent.history` from SQLite → run `send_stream` → forward each frame to the client → persist `UserMessage`/`ToolCall`/`ToolResult`/`AssistantMessage` rows → release lock.

- [ ] **Step 1: Update existing test_ws_chat.py tests + add new streaming/lock/rehydration tests**

Existing tests use `rt.core_agent = CoreAgent(...)` to inject a fake. With the v0.2 model, the registry already constructed an `AssembledAgent` with a real `OllamaClient` per agent — but for tests we want to swap in a fake. The cleanest approach: **mutate `rt.assembled.core_agent.client = fake`** in tests. The Agent's `client` field is a regular dataclass attribute.

Replace the test file body with:

```python
import asyncio
import pytest
from fastapi.testclient import TestClient
from kc_core.ollama_client import ChatResponse
from kc_core.stream_frames import TextDelta, ToolCallsBlock, Done


@pytest.fixture
def fake_client_factory():
    """A minimal kc-core-compatible fake chat client supporting both chat and chat_stream."""
    from dataclasses import dataclass, field
    from kc_core.stream_frames import TextDelta, Done
    from typing import Any

    @dataclass
    class FakeClient:
        responses: list[ChatResponse] = field(default_factory=list)
        stream_responses: list[list[Any]] = field(default_factory=list)
        calls: list = field(default_factory=list)
        model: str = "fake-model"

        def __post_init__(self):
            self._iter = iter(self.responses)
            if not self.stream_responses and self.responses:
                self.stream_responses = [
                    (
                        ([TextDelta(content=r.text)] if r.text else [])
                        + ([] if not r.tool_calls else [])  # tests pass full stream_responses for tool calls
                        + [Done(finish_reason=r.finish_reason)]
                    ) for r in self.responses
                ]
            self._stream_iter = iter(self.stream_responses)

        async def chat(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            return next(self._iter)

        async def chat_stream(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            for f in next(self._stream_iter):
                yield f

    return FakeClient


def _inject_fake(deps, agent_name: str, fake):
    """Swap the OllamaClient on the assembled agent with a fake client."""
    rt = deps.registry.get(agent_name)
    assert rt.assembled is not None, f"agent {agent_name} not assembled in fixture"
    rt.assembled.core_agent.client = fake


# ---- v0.2: streaming round-trip ----

def test_ws_streaming_round_trip_yields_token_then_complete(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[[
        TextDelta(content="Hello "),
        TextDelta(content="back!"),
        Done(finish_reason="stop"),
    ]])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            seen = []
            while True:
                msg = ws.receive_json()
                seen.append(msg)
                if msg["type"] == "assistant_complete":
                    break

    types = [m["type"] for m in seen]
    assert "agent_status" in types
    assert "token" in types
    # Token deltas in order
    tokens = [m for m in seen if m["type"] == "token"]
    assert "".join(t["delta"] for t in tokens) == "Hello back!"
    # Completion
    assert seen[-1]["content"] == "Hello back!"

    # Persistence — UserMessage and AssistantMessage stored
    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" and m.content == "hi" for m in msgs)
    assert any(
        m.__class__.__name__ == "AssistantMessage" and m.content == "Hello back!"
        for m in msgs
    )


def test_ws_streaming_with_tool_call(app, deps, fake_client_factory):
    """Two-turn flow with a tool call. Expect token, tool_call, tool_result, token, assistant_complete."""
    # Need a wired agent with a tool. The fixture-built alice already has file.* tools.
    # Use file.list (SAFE — no approval needed) on the share.
    fake = fake_client_factory(stream_responses=[
        # Turn 1: model asks to list files
        [
            ToolCallsBlock(calls=[{
                "id": "c1", "name": "file.list", "arguments": {"share": "main", "relpath": "."},
            }]),
            Done(finish_reason="tool_calls"),
        ],
        # Turn 2: final text
        [
            TextDelta(content="Empty share."),
            Done(finish_reason="stop"),
        ],
    ])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "list files"})
            frames = []
            while True:
                m = ws.receive_json()
                frames.append(m)
                if m["type"] == "assistant_complete":
                    break

    types = [f["type"] for f in frames]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types.index("tool_call") < types.index("tool_result")
    assert frames[-1]["content"] == "Empty share."

    # Audit row was written for the tool call
    rows = deps.storage.list_audit()
    assert any(r["agent"] == "alice" and r["tool"] == "file.list" for r in rows)


def test_ws_history_rehydration_across_turns(app, deps, fake_client_factory):
    """A second turn sees the prior turn's history because we rehydrate from SQLite."""
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="reply 1"), Done(finish_reason="stop")],
        [TextDelta(content="reply 2"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "first"})
            while ws.receive_json()["type"] != "assistant_complete":
                pass
            ws.send_json({"type": "user_message", "content": "second"})
            while ws.receive_json()["type"] != "assistant_complete":
                pass

    # On the second model call, the messages should include the prior turn
    second_call_messages = fake.calls[1]["messages"]
    user_msgs = [m for m in second_call_messages if m.get("role") == "user"]
    assert len(user_msgs) == 2
    assert user_msgs[0]["content"] == "first"
    assert user_msgs[1]["content"] == "second"


def test_ws_unknown_conversation_id_emits_error_and_closes(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/99999") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "unknown conversation" in msg["message"]


def test_ws_agent_not_initialized_emits_error(app, deps):
    """If rt.assembled is None (degraded), the WS should send an error and close."""
    rt = deps.registry.get("alice")
    rt.assembled = None
    rt.last_error = "synthetic test failure"
    rt.set_status_DEGRADED = None  # noop; just to mark intent
    from kc_supervisor.agents import AgentStatus
    rt.set_status(AgentStatus.DEGRADED)

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "degraded" in msg["message"] or "synthetic" in msg["message"]


def test_ws_unexpected_inbound_type_emits_error_then_continues(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="ok"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)
    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "garbage"})
            err = ws.receive_json()
            assert err["type"] == "error"
            ws.send_json({"type": "user_message", "content": "hi"})
            while True:
                m = ws.receive_json()
                if m["type"] == "assistant_complete":
                    assert m["content"] == "ok"
                    break


def test_ws_user_message_with_empty_content_is_rejected(app, deps, fake_client_factory):
    fake = fake_client_factory(stream_responses=[
        [TextDelta(content="should not be called"), Done(finish_reason="stop")],
    ])
    _inject_fake(deps, "alice", fake)
    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": ""})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "non-empty" in err["message"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_ws_streaming_error_mid_stream_emits_error_frame_and_no_assistant_persisted(app, deps, fake_client_factory):
    """If chat_stream raises, the WS handler emits error frame and does NOT persist AssistantMessage."""
    from dataclasses import dataclass

    @dataclass
    class FailingClient:
        model: str = "fake-model"
        async def chat(self, messages, tools):  # noqa
            raise RuntimeError("ollama down")
        async def chat_stream(self, messages, tools):  # noqa
            raise RuntimeError("ollama down")
            yield  # pragma: no cover

    _inject_fake(deps, "alice", FailingClient())

    with TestClient(app) as client:
        cid = client.post(
            "/agents/alice/conversations", json={"channel": "dashboard"}
        ).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            seen = []
            try:
                while True:
                    seen.append(ws.receive_json())
            except Exception:
                pass

    err_frames = [f for f in seen if f["type"] == "error"]
    assert any("ollama" in f.get("message", "").lower() for f in err_frames)

    # AssistantMessage was NOT persisted (UserMessage was, before the failure)
    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" for m in msgs)
    assert not any(m.__class__.__name__ == "AssistantMessage" for m in msgs)
```

Note: The plan keeps the prior tests for `unknown_conversation_id`, `agent_not_initialized`, `unexpected_inbound`, and `empty_content` — those exercise the same error paths that v0.2 still honors.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ws_chat.py -v
```

Expected: most fail because ws_routes.ws_chat is still the v1 implementation that uses `rt.core_agent` (now removed) instead of `rt.assembled`.

- [ ] **Step 3: Rewrite `kc-supervisor/src/kc_supervisor/ws_routes.py` ws_chat handler**

Replace the entire file with:

```python
from __future__ import annotations
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage
from kc_core.stream_frames import (
    TokenDelta, ToolCallStart, ToolResult, Complete,
)
from kc_supervisor.agents import AgentStatus

logger = logging.getLogger(__name__)


def register_ws_routes(app: FastAPI) -> None:

    @app.websocket("/ws/chat/{conversation_id}")
    async def ws_chat(ws: WebSocket, conversation_id: int):
        await ws.accept()
        deps = app.state.deps

        conv = deps.storage.get_conversation(conversation_id)
        if conv is None:
            await ws.send_json({
                "type": "error",
                "message": f"unknown conversation {conversation_id}",
            })
            await ws.close()
            return

        try:
            rt = deps.registry.get(conv["agent"])
        except KeyError:
            await ws.send_json({
                "type": "error",
                "message": f"unknown agent {conv['agent']}",
            })
            await ws.close()
            return

        if rt.assembled is None or rt.status == AgentStatus.DEGRADED:
            err = rt.last_error or "agent not assembled"
            await ws.send_json({
                "type": "error",
                "message": f"agent {rt.name} is degraded: {err}",
            })
            await ws.close()
            return
        if rt.status == AgentStatus.DISABLED:
            await ws.send_json({
                "type": "error",
                "message": f"agent {rt.name} is disabled",
            })
            await ws.close()
            return

        lock = deps.conv_locks.get(conversation_id)

        try:
            while True:
                inbound = await ws.receive_json()
                if inbound.get("type") != "user_message":
                    await ws.send_json({
                        "type": "error",
                        "message": f"unexpected: {inbound.get('type')}",
                    })
                    continue
                content = inbound.get("content", "")
                if not content:
                    await ws.send_json({
                        "type": "error",
                        "message": "user_message must include non-empty content",
                    })
                    continue

                async with lock:
                    # Persist user message
                    deps.conversations.append(conversation_id, UserMessage(content=content))

                    # Rehydrate kc-core Agent.history from SQLite (excluding the just-added user
                    # message — we'll let send_stream re-append it). We pass list_messages and
                    # then drop the trailing UserMessage (since send_stream appends one too).
                    history = deps.conversations.list_messages(conversation_id)
                    # send_stream appends UserMessage(content) itself, so trim trailing UserMessage
                    if history and history[-1].__class__.__name__ == "UserMessage":
                        history = history[:-1]
                    rt.assembled.core_agent.history = list(history)

                    rt.set_status(AgentStatus.THINKING)
                    await ws.send_json({"type": "agent_status", "status": "thinking"})
                    try:
                        async for frame in rt.assembled.core_agent.send_stream(content):
                            if isinstance(frame, TokenDelta):
                                await ws.send_json({"type": "token", "delta": frame.content})
                            elif isinstance(frame, ToolCallStart):
                                await ws.send_json({
                                    "type": "tool_call",
                                    "call": frame.call,
                                })
                                # Persist ToolCallMessage too, so list_messages reflects the full turn
                                deps.conversations.append(conversation_id, ToolCallMessage(
                                    tool_call_id=frame.call["id"],
                                    tool_name=frame.call["name"],
                                    arguments=frame.call["arguments"],
                                ))
                            elif isinstance(frame, ToolResult):
                                await ws.send_json({
                                    "type": "tool_result",
                                    "call_id": frame.call_id,
                                    "content": frame.content,
                                })
                                deps.conversations.append(conversation_id, ToolResultMessage(
                                    tool_call_id=frame.call_id,
                                    content=frame.content,
                                ))
                            elif isinstance(frame, Complete):
                                deps.conversations.append(conversation_id, frame.reply)
                                await ws.send_json({
                                    "type": "assistant_complete",
                                    "content": frame.reply.content,
                                })
                        # Successful turn — clear last_error
                        rt.last_error = None
                    except Exception as e:
                        logger.exception("ws_chat send_stream raised")
                        rt.last_error = str(e)
                        rt.set_status(AgentStatus.DEGRADED)
                        await ws.send_json({
                            "type": "error",
                            "stage": "model_call",
                            "message": str(e),
                        })
                        # Don't persist AssistantMessage — the user message stays, history is honest about the failure
                        continue
                    finally:
                        if rt.status == AgentStatus.THINKING:
                            rt.set_status(AgentStatus.IDLE)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/approvals")
    async def ws_approvals(ws: WebSocket):
        # ws_approvals body is unchanged from v1 — copy it from the prior file content
        # The body below mirrors v1's working implementation.
        await ws.accept()
        deps = app.state.deps

        async def _send(req):
            try:
                await ws.send_json({
                    "type": "approval_request",
                    "request_id": req.request_id,
                    "agent": req.agent,
                    "tool": req.tool,
                    "arguments": req.arguments,
                })
            except Exception:
                logger.warning("ws_approvals failed to send request %s", req.request_id, exc_info=True)

        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        sub = deps.approvals.subscribe(
            lambda req: loop.call_soon_threadsafe(_asyncio.create_task, _send(req))
        )

        try:
            for req in deps.approvals.pending():
                await _send(req)

            while True:
                msg = await ws.receive_json()
                if msg.get("type") != "approval_response":
                    continue
                request_id = msg.get("request_id")
                if not isinstance(request_id, str):
                    logger.warning("ws_approvals received malformed approval_response (no request_id)")
                    continue
                deps.approvals.resolve(
                    request_id=request_id,
                    allowed=bool(msg.get("allowed", False)),
                    reason=msg.get("reason"),
                )
        except WebSocketDisconnect:
            return
        finally:
            sub.unsubscribe()
```

Note on history rehydration: `Agent.send_stream` (Task 3) starts with `self.history.append(UserMessage(content=user_text))`. If we pre-set history to include the just-persisted UserMessage, we'd duplicate. So we trim a trailing UserMessage from the rehydrated history before calling send_stream. (Alternative: persist after send_stream returns. Chose persist-first because it makes the user's input visible in `/conversations/{cid}/messages` even if the model call fails.)

- [ ] **Step 4: Run ws_chat tests**

```bash
pytest tests/test_ws_chat.py -v
```

Expected: PASS — 8 tests green.

- [ ] **Step 5: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: most tests PASS. Remaining failures should be in test_http for the /undo and /agents endpoints — handled in Tasks 11 and 12.

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/ws_routes.py kc-supervisor/tests/test_ws_chat.py
git commit -m "feat(kc-supervisor): ws_chat with streaming, per-cid lock, history rehydration"
```

---

## Task 11: kc-supervisor POST /undo/{audit_id} wired to Undoer

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Modify: `kc-supervisor/tests/test_http.py`

**Why:** v1 returned 501. Wire it for real: look up the linked eid via `storage.get_undo_op_for_audit`, locate the agent's AssembledAgent, and call `Undoer(journals, undo_log).undo(eid)`.

- [ ] **Step 1: Update tests in `kc-supervisor/tests/test_http.py`**

Find `test_undo_returns_501` and replace with the v0.2 set. Append all new undo tests:

```python
def test_undo_unknown_audit_id_returns_404(app):
    with TestClient(app) as client:
        r = client.post("/undo/99999")
    assert r.status_code == 404
    assert "unknown audit" in r.json()["detail"].lower()


def test_undo_audit_with_no_link_returns_422(app, deps):
    """An audit row with no audit_undo_link entry is not undoable."""
    aid = deps.storage.append_audit(
        agent="alice", tool="file.read", args_json="{}",
        decision="tier", result="ok", undoable=False,
    )
    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 422
    assert "no journal op" in r.json()["detail"].lower()


def test_undo_happy_path_reverses_a_real_file_write(app, deps, tmp_path):
    """End-to-end: write a file via assembly's tool registry, then undo via POST /undo/{audit_id}."""
    rt = deps.registry.get("alice")
    assert rt.assembled is not None

    # Set up the audit-aware permission contextvar (we're invoking outside a real WS request)
    from kc_supervisor.audit_tools import _decision_contextvar, _eid_contextvar
    from kc_sandbox.permissions import Decision, Tier
    _decision_contextvar.set(Decision(allowed=True, tier=Tier.MUTATING, source="tier", reason=None))
    _eid_contextvar.set(None)

    # Invoke file.write via the tool registry (audit hooks run; eid captured)
    target = "hello.txt"
    rt.assembled.registry.invoke("file.write", {
        "share": "main", "relpath": target, "content": "hi from test",
    })

    share_root = deps.shares.get("main").path
    assert (share_root / target).exists()

    # Look up the audit row
    rows = deps.storage.list_audit()
    write_rows = [r for r in rows if r["tool"] == "file.write"]
    assert len(write_rows) == 1
    aid = write_rows[0]["id"]
    assert deps.storage.get_undo_op_for_audit(aid) is not None  # link row exists

    # Hit /undo
    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 200
    body = r.json()
    assert "reversed" in body

    # The file should no longer exist (Undoer git-reverts the journal commit)
    assert not (share_root / target).exists()


def test_undo_returns_500_on_undoer_failure(app, deps):
    """If the Undoer raises (e.g., journal sha doesn't exist), /undo returns 500 with the audit_id."""
    rt = deps.registry.get("alice")
    assert rt.assembled is not None

    from kc_supervisor.audit_tools import _decision_contextvar, _eid_contextvar
    from kc_sandbox.permissions import Decision, Tier
    _decision_contextvar.set(Decision(allowed=True, tier=Tier.MUTATING, source="tier", reason=None))
    _eid_contextvar.set(None)

    # Force a fake undo entry
    eid = rt.assembled.undo_log.record(__import__("kc_sandbox.undo", fromlist=["UndoEntry"]).UndoEntry(
        agent="alice", tool="file.write",
        reverse_kind="git-revert",
        reverse_payload={"share": "main", "sha": "deadbeef"},  # nonexistent sha
    ))
    aid = deps.storage.append_audit(
        agent="alice", tool="file.write", args_json="{}",
        decision="tier", result="wrote", undoable=True,
    )
    deps.storage.link_audit_undo(aid, eid)

    with TestClient(app) as client:
        r = client.post(f"/undo/{aid}")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"].startswith("undo failed")
    assert body.get("audit_id") == aid
```

Remove the old `test_undo_returns_501`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_http.py -k undo -v
```

Expected: FAIL — current handler returns 501.

- [ ] **Step 3: Update `kc-supervisor/src/kc_supervisor/http_routes.py`**

Find the `undo` route and replace its body:

```python
    @app.post("/undo/{audit_id}")
    def undo(audit_id: int):
        deps = app.state.deps
        # 1. Find the audit row
        rows = deps.storage.list_audit(limit=1000000)  # full scan; v1 audits are small
        row = next((r for r in rows if r["id"] == audit_id), None)
        if row is None:
            raise HTTPException(404, detail=f"unknown audit_id: {audit_id}")
        # 2. Find the linked eid
        eid = deps.storage.get_undo_op_for_audit(audit_id)
        if eid is None:
            raise HTTPException(422, detail="this audit row has no journal op (only mutating/destructive file ops journal)")
        # 3. Find the agent's AssembledAgent
        try:
            rt = deps.registry.get(row["agent"])
        except KeyError:
            raise HTTPException(404, detail=f"agent {row['agent']!r} (from audit row) no longer exists")
        if rt.assembled is None:
            raise HTTPException(409, detail=f"agent {row['agent']!r} is degraded; cannot undo")
        # 4. Build an Undoer from this agent's journals + undo_log and run it
        from kc_sandbox.undo import Undoer
        undoer = Undoer(journals=rt.assembled.journals, log=rt.assembled.undo_log)
        try:
            undoer.undo(eid)
        except Exception as e:
            raise HTTPException(500, detail=f"undo failed: {type(e).__name__}: {e}", headers=None) from e
        # 5. Return success — kc-sandbox's Undoer doesn't return the reverse-action shape today,
        # so we synthesize it from the UndoEntry's reverse_kind.
        entry = rt.assembled.undo_log.get(eid)
        return {"reversed": {"kind": entry.reverse_kind, "details": entry.reverse_payload}}
```

(Note: `HTTPException(500, ...)` doesn't take audit_id directly. To include `audit_id` in the body, we need to return a `JSONResponse` instead of raising. Updating accordingly.)

Better approach — replace with a JSONResponse for the 500 case:

```python
    @app.post("/undo/{audit_id}")
    def undo(audit_id: int):
        from fastapi.responses import JSONResponse
        deps = app.state.deps
        rows = deps.storage.list_audit(limit=1000000)
        row = next((r for r in rows if r["id"] == audit_id), None)
        if row is None:
            raise HTTPException(404, detail=f"unknown audit_id: {audit_id}")
        eid = deps.storage.get_undo_op_for_audit(audit_id)
        if eid is None:
            raise HTTPException(422, detail="this audit row has no journal op (only mutating/destructive file ops journal)")
        try:
            rt = deps.registry.get(row["agent"])
        except KeyError:
            raise HTTPException(404, detail=f"agent {row['agent']!r} (from audit row) no longer exists")
        if rt.assembled is None:
            raise HTTPException(409, detail=f"agent {row['agent']!r} is degraded; cannot undo")
        from kc_sandbox.undo import Undoer
        undoer = Undoer(journals=rt.assembled.journals, log=rt.assembled.undo_log)
        try:
            undoer.undo(eid)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": f"undo failed: {type(e).__name__}: {e}",
                    "audit_id": audit_id,
                },
            )
        entry = rt.assembled.undo_log.get(eid)
        return {"reversed": {"kind": entry.reverse_kind, "details": entry.reverse_payload}}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_http.py -k undo -v
```

Expected: PASS — 4 tests green (404, 422, 200, 500).

- [ ] **Step 5: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: PASS except for any /agents POST test (added in Task 12).

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http.py
git commit -m "feat(kc-supervisor): wire POST /undo/{audit_id} to kc-sandbox Undoer"
```

---

## Task 12: kc-supervisor POST /agents (subagent spawn)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Modify: `kc-supervisor/tests/test_http.py`

**Why:** Dashboard's "+ New Agent" button. Validate name pattern, check collision, write YAML atomically, reload registry, return new agent's snapshot (degraded or healthy).

- [ ] **Step 1: Append failing tests**

In `kc-supervisor/tests/test_http.py`:

```python
def test_post_agents_creates_yaml_and_registry_picks_it_up(app, deps):
    body = {"name": "carol", "system_prompt": "I am carol", "model": "fake-model"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 200
    snap = r.json()
    assert snap["name"] == "carol"
    assert snap["status"] in ("idle", "degraded")
    # File on disk
    yaml_path = deps.home / "agents" / "carol.yaml"
    assert yaml_path.exists()
    # Registry sees it
    assert "carol" in deps.registry.names()


def test_post_agents_collision_returns_409(app, deps):
    """alice already exists in the fixture. POSTing alice again should 409."""
    body = {"name": "alice", "system_prompt": "another alice"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 409
    assert "exists" in r.json()["detail"].lower()


def test_post_agents_invalid_name_returns_422(app, deps):
    """Names with path traversal characters or starting with non-letter are rejected."""
    bad_names = ["../evil", "0name", "name with space", "x" * 80]
    for name in bad_names:
        with TestClient(app) as client:
            r = client.post("/agents", json={"name": name, "system_prompt": "x"})
        assert r.status_code == 422, f"expected 422 for {name!r}, got {r.status_code}"
        # No file created
        assert not (deps.home / "agents" / f"{name}.yaml").exists()


def test_post_agents_uses_default_model_when_omitted(app, deps):
    body = {"name": "dave", "system_prompt": "hi"}
    with TestClient(app) as client:
        r = client.post("/agents", json=body)
    assert r.status_code == 200
    yaml_text = (deps.home / "agents" / "dave.yaml").read_text()
    assert "name: dave" in yaml_text
    # The fixture's default_model is "fake-model"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_http.py -k post_agents -v
```

Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Add `POST /agents` to `http_routes.py`**

Add at the top of `http_routes.py`, after `CreateConversationRequest`:

```python
import re

_AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class CreateAgentRequest(BaseModel):
    name: str
    system_prompt: str
    model: Optional[str] = None
```

Then inside `register_http_routes`, before the `/conversations` block:

```python
    @app.post("/agents")
    def create_agent(req: CreateAgentRequest):
        deps = app.state.deps
        if not _AGENT_NAME_PATTERN.match(req.name):
            raise HTTPException(
                422,
                detail=f"name must match {_AGENT_NAME_PATTERN.pattern}",
            )
        agent_dir = deps.home / "agents"
        target = agent_dir / f"{req.name}.yaml"
        if target.exists():
            raise HTTPException(409, detail=f"agent {req.name!r} already exists")

        # Build YAML content. model is optional; load_agent_config will fall back.
        lines = [f"name: {req.name}", f"system_prompt: |"]
        for pl in req.system_prompt.splitlines() or [""]:
            lines.append(f"  {pl}")
        if req.model:
            lines.append(f"model: {req.model}")
        yaml_content = "\n".join(lines) + "\n"

        # Atomic write
        tmp = target.with_suffix(".yaml.tmp")
        tmp.write_text(yaml_content)
        tmp.rename(target)

        # Reload registry; the new agent shows up as IDLE or DEGRADED
        deps.registry.load_all()
        rt = deps.registry.get(req.name)
        return rt.to_dict()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_http.py -k post_agents -v
```

Expected: PASS — 4 tests green.

- [ ] **Step 5: Run full kc-supervisor suite**

```bash
pytest tests/ -q
```

Expected: PASS — full suite green (~70+ tests; exact count after final tally below).

- [ ] **Step 6: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http.py
git commit -m "feat(kc-supervisor): add POST /agents (subagent spawn) with name validation"
```

---

## Task 13: SMOKE.md and README polish

**Files:**
- Modify: `kc-supervisor/SMOKE.md`
- Modify: `kc-supervisor/README.md`

**Why:** v1's SMOKE.md said "expected: agent not initialized" because there was no real wiring. v0.2 has real wiring; the smoke test now exercises actual chat against Ollama. README needs the new endpoint (`POST /agents`) and the corrected `KC_OLLAMA_URL` description (now consumed).

- [ ] **Step 1: Replace `kc-supervisor/SMOKE.md` body**

```markdown
# kc-supervisor — Smoke Checklist (v0.2)

Run by hand on the target machine after `pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"`.

## Prereqs

- Ollama running locally with `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`).
- `wscat` for the WebSocket sections (`npm i -g wscat`).
- A share configured: edit `~/KonaClaw/config/shares.yaml` to add:
  ```yaml
  shares:
    - name: scratch
      path: /tmp/kc-scratch
      writable: true
  ```
  And `mkdir -p /tmp/kc-scratch`.

## Boot

- [ ] `KC_HOME=~/KonaClaw kc-supervisor` boots without error and binds to `127.0.0.1:8765`.
- [ ] `curl http://127.0.0.1:8765/health` returns `{"status":"ok",...}`.

## Define an agent

Drop a YAML at `~/KonaClaw/agents/kc.yaml`:
```yaml
name: KonaClaw
model: qwen2.5:7b
system_prompt: |
  You are KonaClaw, a helpful local agent with access to a scratch share. When
  the user asks you to read or write files, use the file.* tools. Always confirm
  destructive ops.
```
Restart the supervisor.

## HTTP

- [ ] `curl http://127.0.0.1:8765/agents` lists `KonaClaw` with `status: idle` (NOT `degraded`).
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"helper","system_prompt":"You assist KonaClaw.","model":"qwen2.5:7b"}'` returns 200 with the new agent. `~/KonaClaw/agents/helper.yaml` exists.
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"helper","system_prompt":"x"}'` returns 409 (collision).
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"../evil","system_prompt":"x"}'` returns 422 (bad name).
- [ ] `curl -XPOST -H 'content-type: application/json' -d '{"channel":"dashboard"}' http://127.0.0.1:8765/agents/KonaClaw/conversations` returns a `conversation_id`.
- [ ] `curl http://127.0.0.1:8765/audit` returns `{"entries":[]}` initially.

## WebSocket chat (real Ollama)

- [ ] `wscat -c ws://127.0.0.1:8765/ws/chat/<conversation_id>`.
- [ ] Send `{"type":"user_message","content":"Say hello in 5 words."}`. Receive a stream: `agent_status` → multiple `token` frames → `assistant_complete` with the model's actual output.
- [ ] Repeat with `{"type":"user_message","content":"Write a file at scratch/note.txt with the text Hello World."}`. Expect the model to call `file.write`. WS frame sequence: `agent_status` → `tool_call` → `tool_result` → `token` (model summarizing) → `assistant_complete`.
- [ ] `cat /tmp/kc-scratch/note.txt` shows `Hello World`.
- [ ] `curl http://127.0.0.1:8765/audit` shows two entries: one `file.write` (undoable=true) and the prior turn's read/list calls if any.
- [ ] `curl http://127.0.0.1:8765/conversations/<conversation_id>/messages` returns the full turn history including the tool_call and tool_result rows.

## Approvals (real Ollama, real /ws/approvals)

- [ ] In another terminal: `wscat -c ws://127.0.0.1:8765/ws/approvals` (kept open).
- [ ] In the chat WS, send `{"type":"user_message","content":"Delete scratch/note.txt"}`. The model should call `file.delete` (DESTRUCTIVE) — the supervisor pauses.
- [ ] The approvals WS receives `{"type":"approval_request","request_id":"...","agent":"KonaClaw","tool":"file.delete","arguments":{"share":"scratch","relpath":"note.txt"}}`.
- [ ] Send back `{"type":"approval_response","request_id":"<that-id>","allowed":true}`. The chat WS unblocks; tool runs; `assistant_complete` fires.
- [ ] `cat /tmp/kc-scratch/note.txt` → `No such file or directory`.

## Undo

- [ ] `curl http://127.0.0.1:8765/audit` and find the `file.delete` row's `id`. Note its `undoable: 1`.
- [ ] `curl -XPOST http://127.0.0.1:8765/undo/<that-id>` returns 200 with `{"reversed": {"kind": "git-revert", "details": {...}}}`.
- [ ] `cat /tmp/kc-scratch/note.txt` → `Hello World` (restored).

## Restart resume

- [ ] Stop the supervisor (Ctrl-C), restart it, hit `/conversations` — your old conversation is still listed. Re-open the WS for that cid; the model sees prior turns when you send a new message (history rehydration from SQLite).

## Negative cases

- [ ] WS chat against a non-existent `conversation_id` → server sends `{"type":"error","message":"unknown conversation 99999"}` then closes.
- [ ] Re-running `POST /undo/<same-id>` after a successful undo → 500 with detail mentioning `JournalError` (idempotent-applied tracking is a v0.3 follow-up).
- [ ] `POST /undo/<audit_id_for_a_file_read>` → 422 "no journal op".

## Known not-yet-wired (v0.3)

- Approval timeout (currently blocks indefinitely if no `/ws/approvals` client connects).
- Idempotent undo (re-running undo on the same audit_id 500s today; v0.3 will return "already undone").
- Streaming-while-tool-running (today, tool execution pauses the token stream).
- Encrypted secrets store, launchd auto-restart, Prometheus `/metrics`.
```

- [ ] **Step 2: Replace `kc-supervisor/README.md` body**

```markdown
# kc-supervisor

KonaClaw supervisor — sub-project 3 of 8. FastAPI service hosting kc-core
agents with kc-sandbox tools, persisting state in SQLite, and exposing
HTTP + WebSocket APIs for the dashboard.

Depends on `kc-core` and `kc-sandbox`.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"

## Run

    KC_HOME=~/KonaClaw kc-supervisor
    # Then: http://127.0.0.1:8765/health

## Test

    pytest tests/ -v

See `SMOKE.md` for the manual end-to-end checklist.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Process status, uptime, agent count |
| GET | `/agents` | List configured agents and their status |
| POST | `/agents` | Create a new agent YAML and reload registry |
| GET | `/conversations[?agent=name]` | List conversations |
| POST | `/agents/{name}/conversations` | Start a new conversation |
| GET | `/conversations/{id}/messages` | List messages in a conversation |
| GET | `/audit[?agent=name][&limit=N]` | Recent tool-call audit (limit capped at 1000) |
| POST | `/undo/{audit_id}` | Reverse a journaled action via kc-sandbox `Undoer` |
| WS | `/ws/chat/{conversation_id}` | Streaming chat: token deltas, tool_call/tool_result frames, assistant_complete |
| WS | `/ws/approvals` | Approval request stream + responses |

## Environment

- `KC_HOME` — root for `agents/`, `data/`, `config/` (default `~/KonaClaw`)
- `KC_OLLAMA_URL` — Ollama URL (default `http://localhost:11434`); consumed by per-agent OllamaClients at registry-load time
- `KC_DEFAULT_MODEL` — default model when YAML omits one (default `qwen2.5:7b`)
- `KC_PORT` — bind port (default `8765`)

## v0.3 Follow-ups

- Approval timeout knob (currently blocks indefinitely)
- Idempotent undo (re-running undo on the same audit_id currently 500s)
- Token streaming during tool execution (today, tool-call frames pause the stream)
- Encrypted secrets store at `~/KonaClaw/data/secrets.enc`
- launchd plist for auto-restart on crash
- `/metrics` Prometheus endpoint
- Per-agent loop locking against multi-tab races (currently per-conversation only)
- Shared httpx connection pool across per-agent OllamaClients
```

- [ ] **Step 3: Run full test suite one final time**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor
source .venv/bin/activate
pytest tests/ -v
```

Expected: PASS — all tests green.

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-core
pytest tests/ --ignore=tests/live -v
```

Expected: PASS — kc-core 56 tests green (38 v1 + 7 stream_frames + 6 send_stream + 5 chat_stream).

- [ ] **Step 4: Commit**

```bash
cd /Users/sammydallal/Desktop/claudeCode/SammyClaw
git add kc-supervisor/SMOKE.md kc-supervisor/README.md
git commit -m "docs(kc-supervisor): rewrite SMOKE.md for v0.2 wiring; update README"
```

---

## Done Criteria

When all 13 tasks are committed:

- `kc-core` has streaming surface (`stream_frames`, `Agent.send_stream`, `OllamaClient.chat_stream`); ~56 tests green.
- `kc-sandbox` is unchanged; existing 44 tests green.
- `kc-supervisor` v0.2 wiring complete: real chat against Ollama, audit log populated, working `POST /undo/{audit_id}`, per-conversation lock prevents same-cid races, `POST /agents` for spawn, all per-agent OllamaClients use the YAML's model.
- All three test suites green (~157 tests total).
- SMOKE.md walks through chat + tool call + approval + undo against real Ollama, end-to-end.
- Dashboard (sub-project 4) can be implemented against this backend without further wiring changes.

## Cross-references

- Spec: `docs/superpowers/specs/2026-05-04-kc-supervisor-v02-wiring-design.md`.
- v1 supervisor merge: commit `3e18727` (2026-05-04).
- kc-sandbox merge: commit `efd927a` (2026-05-04).
- Dashboard plan (next sub-project): `docs/superpowers/plans/2026-05-02-kc-dashboard.md`. Audit pass needed before dispatch (per recipe lessons).
