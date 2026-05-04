# kc-supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap kc-core + kc-sandbox in a long-running FastAPI service that the React dashboard (kc-dashboard, sub-project 4) can drive over HTTP + WebSocket. Persist conversations, messages, and the audit log in SQLite. Manage multiple agents loaded from a directory of YAML configs. Support a real human-in-the-loop approval flow where destructive tool calls pause the agent loop and resume only after the user clicks Approve/Deny in the dashboard.

**Architecture:** Single Python process running FastAPI. State lives in SQLite at `~/KonaClaw/data/konaclaw.db`. Agents are loaded once at boot from `~/KonaClaw/agents/*.yaml`, each agent represented by an `AgentRuntime` async task that owns the kc-core `Agent` instance, its conversation history, and its asyncio inbox queue. Approval requests originate inside the agent loop, are broadcast to all connected dashboard WebSockets, and resolve when one of them sends back an approval verdict — implemented via per-request `asyncio.Future` indexed by request_id so the agent loop can `await` the human decision without blocking the FastAPI event loop.

**Tech Stack:** Python 3.11+, FastAPI 0.110+, uvicorn, sqlite3 (stdlib), pydantic v2 (request/response models), starlette WebSockets. Depends on `kc-core` (sub-project 1) and `kc-sandbox` (sub-project 2). Tests use FastAPI's `TestClient` (HTTP) and `httpx-ws` for WebSocket integration tests, with a fake Ollama from kc-core.

**Repo bootstrap:** Build in `~/Desktop/claudeCode/SammyClaw/kc-supervisor/` alongside the prior two repos.

**Scope decisions for v1:**
- ✅ HTTP API (agents, conversations, audit, undo, health)
- ✅ WebSocket streaming chat
- ✅ WebSocket approval flow
- ✅ SQLite persistence
- ✅ Multi-agent runtime
- ⏸ Encrypted secrets store — deferred to v0.2 (no connectors needing secrets in this sub-project)
- ⏸ launchd auto-restart plist — deferred to v0.2 (manual `uvicorn` start is fine for hand-driven smoke tests)
- ⏸ Prometheus `/metrics` endpoint — deferred to v0.2 (the dashboard's Monitor view can read from `/audit` + `/health` for v1)

---

## File Structure

```
kc-supervisor/
├── pyproject.toml
├── README.md
├── SMOKE.md
├── src/
│   └── kc_supervisor/
│       ├── __init__.py
│       ├── storage.py           # SQLite schema + Storage class (conversations, messages, audit)
│       ├── audit.py             # AuditWriter — writes to audit table, query API
│       ├── approvals.py         # ApprovalBroker — async per-request_id futures + dashboard broadcast
│       ├── agents.py            # AgentRuntime, AgentRegistry — multi-agent lifecycle
│       ├── conversations.py     # ConversationManager — start/list/append
│       ├── service.py           # FastAPI app factory + dependency wiring
│       ├── http_routes.py       # GET /agents, /conversations, /audit, POST /undo, GET /health
│       ├── ws_routes.py         # WebSocket /chat/{conv_id}, WebSocket /approvals
│       └── main.py              # `python -m kc_supervisor` entry point
└── tests/
    ├── conftest.py
    ├── test_storage.py
    ├── test_audit.py
    ├── test_approvals.py
    ├── test_agents.py
    ├── test_conversations.py
    ├── test_http.py
    ├── test_ws_chat.py
    └── test_ws_approvals.py
```

Plus small upstream changes (Task 1):
- **kc-core**: make `permission_check` accept an async callable in addition to sync.
- **kc-sandbox**: make `PermissionEngine.check` async-aware (await the callback if it's a coroutine).

---

## Task 0: Bootstrap kc-supervisor Repo

**Files:**
- Create: `kc-supervisor/pyproject.toml`
- Create: `kc-supervisor/.gitignore`
- Create: `kc-supervisor/README.md`
- Create: `kc-supervisor/src/kc_supervisor/__init__.py`
- Create: `kc-supervisor/tests/__init__.py`

- [ ] **Step 1: Create the project directory and init git**

```bash
mkdir -p ~/Desktop/claudeCode/SammyClaw/kc-supervisor
cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
git init -b main
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-supervisor"
version = "0.1.0"
description = "KonaClaw supervisor — FastAPI service hosting agents"
requires-python = ">=3.11"
dependencies = [
    "kc-core",
    "kc-sandbox",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "httpx-ws>=0.6",
    "ruff>=0.4",
]

[project.scripts]
kc-supervisor = "kc_supervisor.main:main"

[tool.hatch.build.targets.wheel]
packages = ["src/kc_supervisor"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v"

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
build/
dist/
.coverage
```

- [ ] **Step 4: Create empty package files**

```python
# src/kc_supervisor/__init__.py
__version__ = "0.1.0"
```

```python
# tests/__init__.py
```

- [ ] **Step 5: Create README stub**

```markdown
# kc-supervisor

KonaClaw supervisor — sub-project 3 of 8. FastAPI service that hosts kc-core
agents with kc-sandbox tools, persists state in SQLite, and exposes HTTP +
WebSocket APIs for the dashboard.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"

## Run

    KC_HOME=~/KonaClaw kc-supervisor
```

- [ ] **Step 6: Create venv, install all three packages**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"
python -c "import kc_supervisor, kc_sandbox, kc_core; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore README.md src/ tests/
git commit -m "chore: bootstrap kc-supervisor package"
```

---

## Task 1: Upstream — Async Approval Callback Support

**Files (in two upstream repos):**
- Modify: `kc-core/src/kc_core/agent.py`
- Modify: `kc-core/tests/test_agent.py`
- Modify: `kc-sandbox/src/kc_sandbox/permissions.py`
- Modify: `kc-sandbox/tests/test_permissions.py`

**Why:** The supervisor's approval callback needs to **await a human verdict over WebSocket** — fundamentally async. The kc-sandbox callback today is sync, and kc-core's `permission_check` is sync. We add async support to both without breaking sync usage. This is the only upstream change in this entire plan.

- [ ] **Step 1: Add a failing test in `kc-core/tests/test_agent.py`**

```python
@pytest.mark.asyncio
async def test_agent_permission_check_supports_async_callback(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="ok done", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    async def async_allow(agent_name, tool_name, args):
        return (True, None)

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=async_allow,
    )
    reply = await agent.send("echo hi")
    assert reply.content == "ok done"
```

- [ ] **Step 2: Verify it fails**

In `kc-core/`: `pytest tests/test_agent.py::test_agent_permission_check_supports_async_callback -v`
Expected: FAIL — current sync code can't `await` the result.

- [ ] **Step 3: Update `kc-core/src/kc_core/agent.py` permission check branch**

Replace the permission-check block in `_run_loop`:

```python
                if self.permission_check is not None:
                    result = self.permission_check(self.name, c["name"], c["arguments"])
                    if asyncio.iscoroutine(result):
                        result = await result
                    allowed, reason = result
                    if not allowed:
                        self.history.append(ToolResultMessage(
                            tool_call_id=c["id"],
                            content=f"Denied: {reason or 'permission_check returned False'}",
                        ))
                        continue
```

Add `import asyncio` at the top of the file.

- [ ] **Step 4: Run all kc-core tests**

In `kc-core/`: `pytest tests/ --ignore=tests/live -v`
Expected: PASS — all green (sync test from prior task still works, new async test passes).

- [ ] **Step 5: Commit kc-core change**

```bash
cd ~/Desktop/claudeCode/SammyClaw/kc-core
git add src/kc_core/agent.py tests/test_agent.py
git commit -m "feat(kc-core): support async permission_check callbacks"
cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
```

- [ ] **Step 6: Add a failing test in `kc-sandbox/tests/test_permissions.py`**

```python
@pytest.mark.asyncio
async def test_engine_supports_async_callback():
    async def async_allow(agent, tool, arguments):
        return (True, None)
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=async_allow,
    )
    d = await eng.check_async(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is True
```

- [ ] **Step 7: Verify it fails**

In `kc-sandbox/`: `pytest tests/test_permissions.py::test_engine_supports_async_callback -v`
Expected: FAIL — `check_async` doesn't exist.

- [ ] **Step 8: Add `check_async` and async-friendly `to_agent_callback`**

Append to `kc-sandbox/src/kc_sandbox/permissions.py`:

```python
import asyncio
import inspect


    async def check_async(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        override = self.agent_overrides.get(agent, {}).get(tool)
        if override is not None:
            tier = override
            source = "override"
        else:
            tier = self.tier_map.get(tool, Tier.DESTRUCTIVE)
            source = "tier"
        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)
        result = self.approval_callback(agent, tool, arguments)
        if inspect.iscoroutine(result):
            result = await result
        allowed, reason = result
        return Decision(
            allowed=allowed, tier=tier,
            source="callback" if source != "override" else source,
            reason=reason,
        )

    def to_async_agent_callback(self, agent: str):
        """Returns an async callable in the shape kc_core.Agent.permission_check expects."""
        async def _check(agent_name: str, tool: str, args: dict):
            d = await self.check_async(agent=agent_name, tool=tool, arguments=args)
            return (d.allowed, d.reason)
        return _check
```

(These are added *alongside* the existing sync `check` and `to_agent_callback`, not replacing them — sync usage in test fixtures still works.)

- [ ] **Step 9: Run all kc-sandbox tests**

In `kc-sandbox/`: `pytest tests/ -v`
Expected: PASS — all green, including the new async test.

- [ ] **Step 10: Commit kc-sandbox change**

```bash
cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
git add src/kc_sandbox/permissions.py tests/test_permissions.py
git commit -m "feat(kc-sandbox): add async-aware check_async + to_async_agent_callback"
cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
```

---

## Task 2: SQLite Storage Layer

**Files:**
- Create: `src/kc_supervisor/storage.py`
- Test: `tests/test_storage.py`

**Why:** Single SQLite file at `~/KonaClaw/data/konaclaw.db` holds conversations, messages, and audit. Schema matches umbrella spec §9. The `Storage` class owns the schema, the connection, and CRUD; everything else in the supervisor goes through it. We use stdlib `sqlite3` — no ORM, queries are short and explicit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
from pathlib import Path
import time
from kc_supervisor.storage import Storage


def test_init_creates_tables(tmp_path):
    s = Storage(db_path=tmp_path / "kc.db"); s.init()
    with s.connect() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "messages", "audit"} <= names


def test_create_conversation_returns_id(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    assert isinstance(cid, int) and cid > 0


def test_append_and_list_messages(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cid = s.create_conversation(agent="kc", channel="dashboard")
    s.append_message(cid, role="user", content="hi", tool_call_json=None)
    s.append_message(cid, role="assistant", content="hello", tool_call_json=None)
    msgs = s.list_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["content"] == "hello"


def test_list_conversations_filters_by_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.create_conversation(agent="kc", channel="dashboard")
    s.create_conversation(agent="EmailBot", channel="dashboard")
    convs = s.list_conversations(agent="kc")
    assert len(convs) == 1
    assert convs[0]["agent"] == "kc"


def test_audit_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    aid = s.append_audit(
        agent="kc", tool="file.read",
        args_json='{"share":"r","relpath":"x"}',
        decision="safe·auto", result="14 bytes", undoable=False,
    )
    rows = s.list_audit(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == aid
    assert rows[0]["tool"] == "file.read"


def test_audit_filter_by_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    s.append_audit(agent="kc", tool="file.read", args_json="{}", decision="safe", result="ok", undoable=False)
    s.append_audit(agent="EmailBot", tool="file.read", args_json="{}", decision="safe", result="ok", undoable=False)
    rows = s.list_audit(agent="kc")
    assert len(rows) == 1
    assert rows[0]["agent"] == "kc"
```

- [ ] **Step 2: Verify it fails**

`pytest tests/test_storage.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement storage.py**

```python
# src/kc_supervisor/storage.py
from __future__ import annotations
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    channel TEXT NOT NULL,
    started_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conv_agent ON conversations(agent);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_json TEXT,
    ts REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    decision TEXT NOT NULL,
    result TEXT,
    undoable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_audit_agent ON audit(agent);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);
"""


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    # ----- conversations -----

    def create_conversation(self, agent: str, channel: str) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                (agent, channel, time.time()),
            )
            return int(cur.lastrowid)

    def list_conversations(self, agent: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self.connect() as c:
            if agent:
                rows = c.execute(
                    "SELECT * FROM conversations WHERE agent=? ORDER BY started_at DESC LIMIT ?",
                    (agent, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM conversations ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ----- messages -----

    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: Optional[str],
        tool_call_json: Optional[str],
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_call_json, ts) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, role, content, tool_call_json, time.time()),
            )
            return int(cur.lastrowid)

    def list_messages(self, conversation_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----- audit -----

    def append_audit(
        self, *,
        agent: str, tool: str, args_json: str,
        decision: str, result: Optional[str], undoable: bool,
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO audit (ts, agent, tool, args_json, decision, result, undoable) "
                "VALUES (?,?,?,?,?,?,?)",
                (time.time(), agent, tool, args_json, decision, result, 1 if undoable else 0),
            )
            return int(cur.lastrowid)

    def list_audit(self, agent: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self.connect() as c:
            if agent:
                rows = c.execute(
                    "SELECT * FROM audit WHERE agent=? ORDER BY ts DESC LIMIT ?",
                    (agent, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,),
                ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Verify tests pass**

`pytest tests/test_storage.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_supervisor/storage.py tests/test_storage.py
git commit -m "feat(kc-supervisor): add SQLite Storage layer (conversations, messages, audit)"
```

---

## Task 3: ApprovalBroker — Async Per-Request Approval Futures

**Files:**
- Create: `src/kc_supervisor/approvals.py`
- Test: `tests/test_approvals.py`

**Why:** When a destructive tool fires, the agent loop calls into the broker, gets back an awaitable future, and the broker simultaneously emits the request to every connected dashboard WebSocket. The first dashboard to respond resolves the future and the agent loop continues. The broker decouples agent loops (consumers of decisions) from the WebSocket layer (producers of decisions).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approvals.py
import asyncio
import pytest
from kc_supervisor.approvals import ApprovalBroker, ApprovalRequest


@pytest.mark.asyncio
async def test_resolve_request_fulfills_waiter():
    b = ApprovalBroker()
    seen = []
    b.subscribe(lambda req: seen.append(req))
    task = asyncio.create_task(b.request_approval(agent="kc", tool="file.delete", arguments={}))
    await asyncio.sleep(0)  # let the task post the request
    assert len(seen) == 1
    req_id = seen[0].request_id
    b.resolve(req_id, allowed=True, reason=None)
    allowed, reason = await task
    assert allowed is True


@pytest.mark.asyncio
async def test_deny_request():
    b = ApprovalBroker()
    seen = []
    b.subscribe(lambda req: seen.append(req))
    task = asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    await asyncio.sleep(0)
    b.resolve(seen[0].request_id, allowed=False, reason="user said no")
    allowed, reason = await task
    assert allowed is False
    assert reason == "user said no"


@pytest.mark.asyncio
async def test_resolve_unknown_request_id_is_no_op():
    b = ApprovalBroker()
    b.resolve("nonexistent", allowed=True, reason=None)  # must not raise


@pytest.mark.asyncio
async def test_pending_lists_open_requests():
    b = ApprovalBroker()
    asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    asyncio.create_task(b.request_approval(agent="kc", tool="y", arguments={}))
    await asyncio.sleep(0)
    p = b.pending()
    assert len(p) == 2


@pytest.mark.asyncio
async def test_unsubscribe_stops_notifications():
    b = ApprovalBroker()
    seen = []
    handle = b.subscribe(lambda req: seen.append(req))
    handle.unsubscribe()
    asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    await asyncio.sleep(0)
    assert seen == []
```

- [ ] **Step 2: Verify tests fail**

`pytest tests/test_approvals.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement approvals.py**

```python
# src/kc_supervisor/approvals.py
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ApprovalRequest:
    request_id: str
    agent: str
    tool: str
    arguments: dict[str, Any]


@dataclass
class _Subscription:
    callback: Callable[[ApprovalRequest], None]

    def unsubscribe(self) -> None:
        self._broker._subs.discard(self)  # set in subscribe()


class ApprovalBroker:
    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[tuple[bool, Optional[str]]]] = {}
        self._requests: dict[str, ApprovalRequest] = {}
        self._subs: set[_Subscription] = set()

    def subscribe(self, callback: Callable[[ApprovalRequest], None]) -> _Subscription:
        sub = _Subscription(callback=callback)
        sub._broker = self  # type: ignore[attr-defined]
        self._subs.add(sub)
        return sub

    async def request_approval(
        self, agent: str, tool: str, arguments: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        request_id = uuid.uuid4().hex
        req = ApprovalRequest(request_id=request_id, agent=agent, tool=tool, arguments=arguments)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._futures[request_id] = fut
        self._requests[request_id] = req
        for sub in list(self._subs):
            try:
                sub.callback(req)
            except Exception:
                pass  # never let a bad subscriber kill an approval
        try:
            return await fut
        finally:
            self._futures.pop(request_id, None)
            self._requests.pop(request_id, None)

    def resolve(self, request_id: str, allowed: bool, reason: Optional[str]) -> None:
        fut = self._futures.get(request_id)
        if fut is None or fut.done():
            return
        fut.set_result((allowed, reason))

    def pending(self) -> list[ApprovalRequest]:
        return list(self._requests.values())
```

- [ ] **Step 4: Verify tests pass**

`pytest tests/test_approvals.py -v`
Expected: PASS — all 5 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_supervisor/approvals.py tests/test_approvals.py
git commit -m "feat(kc-supervisor): add ApprovalBroker with async per-request futures"
```

---

## Task 4: AgentRegistry + AgentRuntime

**Files:**
- Create: `src/kc_supervisor/agents.py`
- Test: `tests/test_agents.py`

**Why:** The supervisor manages multiple agents. Each agent has a `kc_core.Agent` instance, a status (`idle`/`thinking`/`paused`), and is identified by name. The registry loads all `*.yaml` files from a directory at boot and provides lookup. Spawning new agents at runtime (the dashboard's "+ New Agent" button) writes a new YAML and reloads.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents.py
from pathlib import Path
import pytest
from kc_supervisor.agents import AgentRegistry, AgentRuntime, AgentStatus


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"; d.mkdir()
    (d / "alice.yaml").write_text("name: alice\nmodel: m\nsystem_prompt: I am alice\n")
    (d / "bob.yaml").write_text("name: bob\nmodel: m\nsystem_prompt: I am bob\n")
    return d


def test_load_from_dir(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    assert sorted(reg.names()) == ["alice", "bob"]
    assert reg.get("alice").status == AgentStatus.IDLE


def test_get_unknown_raises(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    with pytest.raises(KeyError):
        reg.get("ghost")


def test_status_transitions(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    rt = reg.get("alice")
    rt.set_status(AgentStatus.THINKING)
    assert rt.status == AgentStatus.THINKING
    assert reg.snapshot()[0]["status"] in {"thinking", "idle"}


def test_disable_and_enable(agents_dir, tmp_path):
    shares_yaml = tmp_path / "shares.yaml"; shares_yaml.write_text("shares: []\n")
    reg = AgentRegistry(agents_dir=agents_dir, shares_yaml=shares_yaml,
                        undo_db=tmp_path / "u.db", default_model="m")
    reg.load_all()
    reg.disable("alice")
    assert reg.get("alice").status == AgentStatus.DISABLED
    reg.enable("alice")
    assert reg.get("alice").status == AgentStatus.IDLE
```

- [ ] **Step 2: Verify tests fail**

`pytest tests/test_agents.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement agents.py**

```python
# src/kc_supervisor/agents.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Any
from kc_core.agent import Agent as CoreAgent
from kc_core.config import load_agent_config


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
    core_agent: Optional[CoreAgent] = None  # built lazily on first use

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
    def __init__(
        self, *,
        agents_dir: Path,
        shares_yaml: Path,
        undo_db: Path,
        default_model: str,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.shares_yaml = Path(shares_yaml)
        self.undo_db = Path(undo_db)
        self.default_model = default_model
        self._by_name: dict[str, AgentRuntime] = {}

    def load_all(self) -> None:
        self._by_name.clear()
        for p in sorted(self.agents_dir.glob("*.yaml")):
            cfg = load_agent_config(p, default_model=self.default_model)
            self._by_name[cfg.name] = AgentRuntime(
                name=cfg.name, model=cfg.model, system_prompt=cfg.system_prompt,
                yaml_path=p,
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

- [ ] **Step 4: Verify tests pass**

`pytest tests/test_agents.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_supervisor/agents.py tests/test_agents.py
git commit -m "feat(kc-supervisor): add AgentRegistry + AgentRuntime"
```

---

## Task 5: ConversationManager

**Files:**
- Create: `src/kc_supervisor/conversations.py`
- Test: `tests/test_conversations.py`

**Why:** The runtime needs to: start a new conversation for an agent, append every user/assistant/tool message, list conversations and messages for the dashboard. This wraps `Storage` with the agent-aware semantics. Includes a helper that, given an agent name + conversation_id, returns a `kc_core.Agent` whose `history` is rehydrated from SQLite — so the dashboard can resume any past thread.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_conversations.py
from pathlib import Path
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.conversations import ConversationManager
from kc_core.messages import UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage


def test_start_appends_persists(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    cm.append(cid, UserMessage("hi"))
    cm.append(cid, AssistantMessage("hello"))
    msgs = cm.list_messages(cid)
    assert len(msgs) == 2
    assert isinstance(msgs[0], UserMessage)
    assert msgs[1].content == "hello"


def test_tool_call_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    cm.append(cid, ToolCallMessage(tool_call_id="c1", tool_name="echo", arguments={"text": "hi"}))
    cm.append(cid, ToolResultMessage(tool_call_id="c1", content="hi"))
    msgs = cm.list_messages(cid)
    assert isinstance(msgs[0], ToolCallMessage)
    assert msgs[0].tool_name == "echo"
    assert isinstance(msgs[1], ToolResultMessage)
    assert msgs[1].content == "hi"


def test_list_for_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cm.start(agent="kc", channel="dashboard")
    cm.start(agent="EmailBot", channel="dashboard")
    convs = cm.list_for_agent("kc")
    assert len(convs) == 1
    assert convs[0]["agent"] == "kc"
```

- [ ] **Step 2: Verify it fails**

`pytest tests/test_conversations.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement conversations.py**

```python
# src/kc_supervisor/conversations.py
from __future__ import annotations
import json
from typing import Optional
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage, Message,
)
from kc_supervisor.storage import Storage


class ConversationManager:
    def __init__(self, storage: Storage) -> None:
        self.s = storage

    def start(self, agent: str, channel: str) -> int:
        return self.s.create_conversation(agent=agent, channel=channel)

    def list_for_agent(self, agent: str) -> list[dict]:
        return self.s.list_conversations(agent=agent)

    def list_all(self, limit: int = 50) -> list[dict]:
        return self.s.list_conversations(limit=limit)

    def append(self, conversation_id: int, msg: Message) -> int:
        if isinstance(msg, UserMessage):
            return self.s.append_message(conversation_id, "user", msg.content, None)
        if isinstance(msg, AssistantMessage):
            return self.s.append_message(conversation_id, "assistant", msg.content, None)
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

    def list_messages(self, conversation_id: int) -> list[Message]:
        out: list[Message] = []
        for row in self.s.list_messages(conversation_id):
            role = row["role"]
            if role == "user":
                out.append(UserMessage(content=row["content"]))
            elif role == "assistant":
                out.append(AssistantMessage(content=row["content"] or ""))
            elif role == "tool_call":
                d = json.loads(row["tool_call_json"])
                out.append(ToolCallMessage(
                    tool_call_id=d["tool_call_id"], tool_name=d["tool_name"], arguments=d["arguments"],
                ))
            elif role == "tool_result":
                d = json.loads(row["tool_call_json"])
                out.append(ToolResultMessage(
                    tool_call_id=d["tool_call_id"], content=d["content"],
                ))
        return out
```

- [ ] **Step 4: Verify it passes**

`pytest tests/test_conversations.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_supervisor/conversations.py tests/test_conversations.py
git commit -m "feat(kc-supervisor): add ConversationManager"
```

---

## Task 6: FastAPI Service Skeleton + Dependency Wiring

**Files:**
- Create: `src/kc_supervisor/service.py`
- Create: `src/kc_supervisor/main.py`
- Create: `src/kc_supervisor/http_routes.py` (stub)
- Test: `tests/conftest.py` (shared fixture)
- Test: `tests/test_http.py` (just /health for now)

**Why:** Wire all the pieces into a FastAPI app with a single `create_app(deps)` factory. `deps` is a small dataclass holding `Storage`, `AgentRegistry`, `ConversationManager`, `ApprovalBroker` — explicit injection so tests can override anything. The CLI entry `python -m kc_supervisor` reads env vars (`KC_HOME`, `KC_OLLAMA_URL`, `KC_DEFAULT_MODEL`), constructs the deps, and runs uvicorn.

- [ ] **Step 1: Add a shared test fixture**

```python
# tests/conftest.py
from pathlib import Path
import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.service import Deps, create_app


@pytest.fixture
def deps(tmp_path):
    home = tmp_path / "kc-home"
    (home / "agents").mkdir(parents=True)
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir(parents=True)

    # Two minimal agents
    (home / "agents" / "alice.yaml").write_text(
        "name: alice\nmodel: fake-model\nsystem_prompt: hi from alice\n"
    )

    # Empty shares.yaml
    (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "kc.db"); storage.init()
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares_yaml=home / "config" / "shares.yaml",
        undo_db=home / "data" / "undo.db",
        default_model="fake-model",
    )
    registry.load_all()
    convs = ConversationManager(storage=storage)
    broker = ApprovalBroker()
    return Deps(storage=storage, registry=registry, conversations=convs, approvals=broker, home=home)


@pytest.fixture
def app(deps):
    return create_app(deps)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_http.py
from fastapi.testclient import TestClient


def test_health_returns_ok(app):
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_s" in body
```

- [ ] **Step 3: Verify it fails**

`pytest tests/test_http.py -v`
Expected: FAIL — modules don't exist.

- [ ] **Step 4: Implement service.py**

```python
# src/kc_supervisor/service.py
from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from fastapi import FastAPI
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker


@dataclass
class Deps:
    storage: Storage
    registry: AgentRegistry
    conversations: ConversationManager
    approvals: ApprovalBroker
    home: Path
    started_at: float = 0.0

    def __post_init__(self) -> None:
        self.started_at = time.time()


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kc-supervisor")
    app.state.deps = deps

    from kc_supervisor.http_routes import register_http_routes
    register_http_routes(app)

    return app
```

- [ ] **Step 5: Implement http_routes.py (stub with /health only)**

```python
# src/kc_supervisor/http_routes.py
from __future__ import annotations
import time
from fastapi import FastAPI


def register_http_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        deps = app.state.deps
        return {
            "status": "ok",
            "uptime_s": round(time.time() - deps.started_at, 2),
            "agents": len(deps.registry.names()),
        }
```

- [ ] **Step 6: Implement main.py**

```python
# src/kc_supervisor/main.py
from __future__ import annotations
import os
from pathlib import Path
import uvicorn
from kc_supervisor.storage import Storage
from kc_supervisor.agents import AgentRegistry
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.approvals import ApprovalBroker
from kc_supervisor.service import Deps, create_app


def main() -> None:
    home = Path(os.environ.get("KC_HOME", str(Path.home() / "KonaClaw")))
    default_model = os.environ.get("KC_DEFAULT_MODEL", "llama3.1")

    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    if not (home / "config" / "shares.yaml").exists():
        (home / "config" / "shares.yaml").write_text("shares: []\n")

    storage = Storage(home / "data" / "konaclaw.db"); storage.init()
    registry = AgentRegistry(
        agents_dir=home / "agents",
        shares_yaml=home / "config" / "shares.yaml",
        undo_db=home / "data" / "undo.db",
        default_model=default_model,
    )
    registry.load_all()
    deps = Deps(
        storage=storage,
        registry=registry,
        conversations=ConversationManager(storage),
        approvals=ApprovalBroker(),
        home=home,
    )
    app = create_app(deps)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_PORT", "8765")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Verify tests pass**

`pytest tests/test_http.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/kc_supervisor/service.py src/kc_supervisor/main.py src/kc_supervisor/http_routes.py tests/conftest.py tests/test_http.py
git commit -m "feat(kc-supervisor): add FastAPI app factory + /health endpoint"
```

---

## Task 7: HTTP Endpoints — Agents, Conversations, Audit, Undo

**Files:**
- Modify: `src/kc_supervisor/http_routes.py`
- Modify: `tests/test_http.py`

**Why:** Now we add the read endpoints the dashboard needs to populate every view: list agents, list conversations, list a conversation's messages, list audit, and POST undo. POST /undo wires through to kc-sandbox's `Undoer` once we plumb it; for v1 it's a stub that returns the entry's reverse_kind so the dashboard can show what would be reversed.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_http.py`:

```python
def test_list_agents(app):
    with TestClient(app) as client:
        r = client.get("/agents")
    assert r.status_code == 200
    names = [a["name"] for a in r.json()["agents"]]
    assert "alice" in names


def test_list_conversations_empty(app):
    with TestClient(app) as client:
        r = client.get("/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_create_conversation(app):
    with TestClient(app) as client:
        r = client.post("/agents/alice/conversations", json={"channel": "dashboard"})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]
    assert isinstance(cid, int)


def test_list_messages_for_conversation(app):
    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


def test_audit_endpoint(app, deps):
    deps.storage.append_audit(
        agent="alice", tool="file.read",
        args_json='{"share":"r"}', decision="safe·auto", result="ok", undoable=False,
    )
    with TestClient(app) as client:
        r = client.get("/audit")
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 1
```

- [ ] **Step 2: Verify tests fail**

`pytest tests/test_http.py -v`
Expected: 4 new tests fail with 404s.

- [ ] **Step 3: Replace `register_http_routes` with the full set**

```python
# src/kc_supervisor/http_routes.py
from __future__ import annotations
import time
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CreateConversationRequest(BaseModel):
    channel: str = "dashboard"


def register_http_routes(app: FastAPI) -> None:

    @app.get("/health")
    def health():
        deps = app.state.deps
        return {
            "status": "ok",
            "uptime_s": round(time.time() - deps.started_at, 2),
            "agents": len(deps.registry.names()),
        }

    @app.get("/agents")
    def list_agents():
        return {"agents": app.state.deps.registry.snapshot()}

    @app.get("/conversations")
    def list_conversations(agent: Optional[str] = None):
        cm = app.state.deps.conversations
        if agent:
            return {"conversations": cm.list_for_agent(agent)}
        return {"conversations": cm.list_all()}

    @app.post("/agents/{name}/conversations")
    def create_conversation(name: str, req: CreateConversationRequest):
        try:
            app.state.deps.registry.get(name)  # validate name
        except KeyError:
            raise HTTPException(404, detail=f"unknown agent: {name}")
        cid = app.state.deps.conversations.start(agent=name, channel=req.channel)
        return {"conversation_id": cid}

    @app.get("/conversations/{cid}/messages")
    def list_messages(cid: int):
        msgs = app.state.deps.conversations.list_messages(cid)
        # Convert dataclass messages to plain dicts for JSON
        out = []
        for m in msgs:
            out.append({"type": m.__class__.__name__, **m.__dict__})
        return {"messages": out}

    @app.get("/audit")
    def list_audit(agent: Optional[str] = None, limit: int = 100):
        rows = app.state.deps.storage.list_audit(agent=agent, limit=limit)
        return {"entries": rows}

    @app.post("/undo/{audit_id}")
    def undo(audit_id: int):
        # v1 returns 501 with a clear message; kc-sandbox Undoer wiring is
        # added in a follow-up patch when shares are configured at boot.
        raise HTTPException(
            501, detail="Undo not yet wired in kc-supervisor v1 — see roadmap.",
        )
```

- [ ] **Step 4: Verify tests pass**

`pytest tests/test_http.py -v`
Expected: PASS — all 6 HTTP tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_supervisor/http_routes.py tests/test_http.py
git commit -m "feat(kc-supervisor): add HTTP endpoints for agents/conversations/audit"
```

---

## Task 8: WebSocket Chat — `/ws/chat/{conversation_id}`

**Files:**
- Create: `src/kc_supervisor/ws_routes.py`
- Modify: `src/kc_supervisor/service.py` (register ws routes)
- Test: `tests/test_ws_chat.py`

**Why:** This is the primary surface the dashboard uses. Client opens a WebSocket per conversation. Inbound: `{"type": "user_message", "content": "..."}`. Outbound: streaming token deltas (`{"type": "token", "content": "..."}`), tool-call events (`{"type": "tool_call", ...}`), tool results, completion events. The agent loop runs in a server-side task; everything emitted to the client also gets persisted via `ConversationManager`.

For v1 we use the **non-streaming** `Agent.send()` and emit one assistant `{"type": "assistant_complete"}` event with the full text — token streaming integration is a v0.2 polish (the kc-core streaming path bypasses tool execution, so unifying the two needs more design).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ws_chat.py
import json
import pytest
from fastapi.testclient import TestClient
from kc_core.ollama_client import ChatResponse


@pytest.fixture
def fake_ollama_factory():
    """Build a fake ollama client and inject into the registry's runtime."""
    from kc_core.tools import ToolRegistry
    from kc_core.agent import Agent
    from dataclasses import dataclass, field
    from typing import Iterator

    @dataclass
    class FakeClient:
        responses: list[ChatResponse]
        calls: list = field(default_factory=list)
        model: str = "fake-model"
        def __post_init__(self): self._iter = iter(self.responses)
        async def chat(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            return next(self._iter)

    return FakeClient


def test_ws_user_message_round_trip(app, deps, fake_ollama_factory):
    fake = fake_ollama_factory(responses=[ChatResponse(text="Hello back!", finish_reason="stop")])

    # Inject the fake into the runtime: monkey-patch the agent build
    from kc_core.agent import Agent as CoreAgent
    from kc_core.tools import ToolRegistry
    rt = deps.registry.get("alice")
    rt.core_agent = CoreAgent(
        name="alice", client=fake, system_prompt=rt.system_prompt, tools=ToolRegistry(),
    )

    with TestClient(app) as client:
        cid = client.post("/agents/alice/conversations", json={"channel": "dashboard"}).json()["conversation_id"]
        with client.websocket_connect(f"/ws/chat/{cid}") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            # Drain events until we see assistant_complete
            seen = []
            while True:
                msg = ws.receive_json()
                seen.append(msg)
                if msg["type"] == "assistant_complete":
                    break
            assert any(e["type"] == "assistant_complete" and "Hello back" in e["content"] for e in seen)

    # Persistence
    msgs = deps.conversations.list_messages(cid)
    assert any(m.__class__.__name__ == "UserMessage" and m.content == "hi" for m in msgs)
    assert any(m.__class__.__name__ == "AssistantMessage" and "Hello back" in m.content for m in msgs)
```

- [ ] **Step 2: Verify it fails**

`pytest tests/test_ws_chat.py -v`
Expected: FAIL — `ws_routes` doesn't exist.

- [ ] **Step 3: Implement ws_routes.py**

```python
# src/kc_supervisor/ws_routes.py
from __future__ import annotations
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import UserMessage, AssistantMessage
from kc_supervisor.agents import AgentStatus


def register_ws_routes(app: FastAPI) -> None:

    @app.websocket("/ws/chat/{conversation_id}")
    async def ws_chat(ws: WebSocket, conversation_id: int):
        await ws.accept()
        deps = app.state.deps
        # Look up the agent that owns this conversation
        convs = deps.storage.list_conversations(limit=1000)
        conv = next((c for c in convs if c["id"] == conversation_id), None)
        if conv is None:
            await ws.send_json({"type": "error", "message": f"unknown conversation {conversation_id}"})
            await ws.close()
            return
        try:
            rt = deps.registry.get(conv["agent"])
        except KeyError:
            await ws.send_json({"type": "error", "message": f"unknown agent {conv['agent']}"})
            await ws.close()
            return
        if rt.core_agent is None:
            await ws.send_json({"type": "error", "message": f"agent {rt.name} not initialized"})
            await ws.close()
            return

        try:
            while True:
                inbound = await ws.receive_json()
                if inbound.get("type") != "user_message":
                    await ws.send_json({"type": "error", "message": f"unexpected: {inbound.get('type')}"})
                    continue
                content = inbound.get("content", "")

                # Persist + run + persist + emit
                deps.conversations.append(conversation_id, UserMessage(content=content))
                rt.set_status(AgentStatus.THINKING)
                await ws.send_json({"type": "agent_status", "status": "thinking"})
                try:
                    reply = await rt.core_agent.send(content)
                finally:
                    rt.set_status(AgentStatus.IDLE)
                deps.conversations.append(conversation_id, reply)
                await ws.send_json({
                    "type": "assistant_complete",
                    "content": reply.content,
                })
        except WebSocketDisconnect:
            return
```

- [ ] **Step 4: Wire ws routes into the app factory**

In `src/kc_supervisor/service.py`, append to `create_app`:

```python
    from kc_supervisor.ws_routes import register_ws_routes
    register_ws_routes(app)
```

- [ ] **Step 5: Verify tests pass**

`pytest tests/test_ws_chat.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kc_supervisor/ws_routes.py src/kc_supervisor/service.py tests/test_ws_chat.py
git commit -m "feat(kc-supervisor): add WebSocket /ws/chat/{cid} for streaming-style chat"
```

---

## Task 9: WebSocket Approvals — `/ws/approvals`

**Files:**
- Modify: `src/kc_supervisor/ws_routes.py`
- Test: `tests/test_ws_approvals.py`

**Why:** The dashboard opens this WebSocket once and keeps it for the session. The supervisor uses it to broadcast `ApprovalRequest`s; the dashboard sends back `{"type": "approval_response", "request_id": "...", "allowed": true|false, "reason": "..."}` to resolve them. We hook this to `ApprovalBroker`'s subscribe/resolve.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ws_approvals.py
import asyncio
import pytest
from fastapi.testclient import TestClient


def test_approval_request_broadcasts_and_resolves(app, deps):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/approvals") as ws:
            # Kick off an approval from another task
            broker = deps.approvals
            async def trigger():
                return await broker.request_approval(agent="alice", tool="file.delete", arguments={"share": "r"})

            loop = asyncio.new_event_loop()
            try:
                fut = loop.create_task(trigger())
                # Wait briefly for broker -> ws broadcast
                msg = ws.receive_json()
                assert msg["type"] == "approval_request"
                assert msg["agent"] == "alice"
                assert msg["tool"] == "file.delete"
                req_id = msg["request_id"]

                ws.send_json({"type": "approval_response", "request_id": req_id, "allowed": True, "reason": None})
                allowed, reason = loop.run_until_complete(fut)
                assert allowed is True
            finally:
                loop.close()
```

(Note: the test runs the broker call in a thread-local loop; in production, the broker call originates from the agent loop running in the supervisor's main loop, where the FastAPI server lives. The key contract proven here is: `ws_approvals` forwards both directions correctly.)

- [ ] **Step 2: Verify it fails**

`pytest tests/test_ws_approvals.py -v`
Expected: FAIL — `/ws/approvals` doesn't exist.

- [ ] **Step 3: Add `/ws/approvals` to ws_routes.py**

Append inside `register_ws_routes`:

```python
    @app.websocket("/ws/approvals")
    async def ws_approvals(ws: WebSocket):
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
                pass

        # Subscribe with a sync callback that schedules the async send
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        sub = deps.approvals.subscribe(lambda req: loop.call_soon_threadsafe(
            _asyncio.create_task, _send(req)
        ))

        try:
            # Replay any currently pending requests
            for req in deps.approvals.pending():
                await _send(req)

            while True:
                msg = await ws.receive_json()
                if msg.get("type") != "approval_response":
                    continue
                deps.approvals.resolve(
                    request_id=msg["request_id"],
                    allowed=bool(msg["allowed"]),
                    reason=msg.get("reason"),
                )
        except WebSocketDisconnect:
            return
        finally:
            sub.unsubscribe()
```

- [ ] **Step 4: Verify the test passes**

`pytest tests/test_ws_approvals.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

`pytest tests/ -v`
Expected: PASS — all kc-supervisor tests green (~30 tests).

- [ ] **Step 6: Commit**

```bash
git add src/kc_supervisor/ws_routes.py tests/test_ws_approvals.py
git commit -m "feat(kc-supervisor): add WebSocket /ws/approvals broker"
```

---

## Task 10: SMOKE.md and README Polish

**Files:**
- Create: `SMOKE.md`
- Modify: `README.md`

- [ ] **Step 1: Write SMOKE.md**

```markdown
# kc-supervisor — Smoke Checklist

Run by hand on the target machine after `pip install -e ../kc-core -e ../kc-sandbox ".[dev]"`.

## Prereqs

- Ollama running locally with `llama3.1` (or `qwen2.5:32b`) pulled.
- A `~/KonaClaw/agents/` directory exists with at least one `*.yaml` (the supervisor creates an empty dir on first run; you'll need to add one agent file by hand).

Example minimal agent:

```yaml
# ~/KonaClaw/agents/kc.yaml
name: KonaClaw
model: llama3.1
system_prompt: |
  You are KonaClaw, a helpful local agent.
```

## Boot

- [ ] `KC_HOME=~/KonaClaw kc-supervisor` boots without error and binds to `127.0.0.1:8765`.
- [ ] `curl http://127.0.0.1:8765/health` returns `{"status":"ok",...}`.

## HTTP

- [ ] `curl http://127.0.0.1:8765/agents` lists `KonaClaw`.
- [ ] `curl -XPOST -H 'content-type: application/json' -d '{"channel":"dashboard"}' http://127.0.0.1:8765/agents/KonaClaw/conversations` returns a `conversation_id`.
- [ ] `curl http://127.0.0.1:8765/conversations` shows the new conversation.
- [ ] `curl http://127.0.0.1:8765/audit` returns `{"entries":[]}` initially.

## WebSocket chat (use `wscat` or any WS client)

- [ ] `wscat -c ws://127.0.0.1:8765/ws/chat/<conversation_id>`
- [ ] Send `{"type":"user_message","content":"hello"}` — receive an `assistant_complete` event with text from your local model.
- [ ] After the round-trip, `curl http://127.0.0.1:8765/conversations/<id>/messages` shows both the user and assistant messages persisted.

## Restart resume

- [ ] Stop the supervisor (Ctrl-C), restart it, hit `/conversations` — your old conversation is still listed.

## Negative cases

- [ ] `curl -XPOST .../agents/ghost/conversations` → 404 with `unknown agent`.
- [ ] WebSocket chat against a non-existent `conversation_id` → server sends `{"type":"error",...}` then closes.

## Known not-yet-wired (v0.2)

- Encrypted secrets store, launchd auto-restart, Prometheus `/metrics`, and `POST /undo/{id}` (returns 501 today).
```

- [ ] **Step 2: Polish README**

```markdown
# kc-supervisor

KonaClaw supervisor — sub-project 3 of 8. FastAPI service hosting kc-core
agents with kc-sandbox tools, persisting state in SQLite, and exposing
HTTP + WebSocket APIs for the dashboard.

Depends on `kc-core` and `kc-sandbox`.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
    python3.11 -m venv .venv
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
| GET | `/conversations[?agent=name]` | List conversations |
| POST | `/agents/{name}/conversations` | Start a new conversation |
| GET | `/conversations/{id}/messages` | List messages in a conversation |
| GET | `/audit[?agent=name][&limit=N]` | Recent tool-call audit |
| POST | `/undo/{audit_id}` | (501 in v1) Undo a journaled action |
| WS | `/ws/chat/{conversation_id}` | Send/receive messages, agent runs |
| WS | `/ws/approvals` | Approval request stream + responses |

## Environment

- `KC_HOME` — root for `agents/`, `data/`, `config/` (default `~/KonaClaw`)
- `KC_OLLAMA_URL` — Ollama URL (default `http://localhost:11434`)
- `KC_DEFAULT_MODEL` — default model name (default `llama3.1`)
- `KC_PORT` — bind port (default `8765`)

## Roadmap (v0.2 follow-ups)

- Wire `POST /undo/{audit_id}` to kc-sandbox `Undoer`
- Encrypted secrets store at `~/KonaClaw/data/secrets.enc`
- launchd plist for auto-restart on crash
- `/metrics` Prometheus endpoint
- Wire each `AgentRuntime` to its sandboxed kc-core agent at boot (today the test inject a fake; production wiring lands once shares are typically configured)
```

- [ ] **Step 3: Run the full test suite one more time**

`pytest tests/ -v`
Expected: PASS — all green (~30 tests).

- [ ] **Step 4: Commit**

```bash
git add SMOKE.md README.md
git commit -m "docs(kc-supervisor): add SMOKE.md and polish README"
```

---

## Done Criteria

When all 11 tasks (Task 0 through 10) are committed:

- `kc-core` has async-aware `permission_check`; all kc-core tests green.
- `kc-sandbox` has `check_async` + `to_async_agent_callback`; all kc-sandbox tests green.
- `kc-supervisor` test suite passes (~30 tests).
- `kc-supervisor` boots against a real `~/KonaClaw/agents/*.yaml` directory and a real Ollama, serves the HTTP endpoints listed above, and round-trips a chat over `/ws/chat/{cid}` with state persisted across restarts.
- `/ws/approvals` broadcasts pending approvals and resolves them when the dashboard responds.
- `SMOKE.md` end-to-end walkthrough passes by hand.

This unblocks **kc-dashboard** (sub-project 4): it consumes exactly these HTTP + WebSocket endpoints and provides the React UI shown in the dashboard mockup.

## Known v0.2 Follow-Ups

1. Wire `POST /undo/{id}` to `kc_sandbox.undo.Undoer`. Requires the agent runtime to know its share `Journal` instances at boot — a small refactor.
2. Wire each `AgentRuntime.core_agent` to a sandboxed kc-core agent at registry-load time (today the test injects fakes; production wiring lands once shares are typically configured per-agent).
3. Streaming token output over `/ws/chat`. kc-core's streaming path bypasses tool execution today; unifying the two needs design.
4. Encrypted secrets store + key derivation from a passphrase set on first run.
5. launchd plist + crash-resume hook (currently relies on manual `kc-supervisor` re-invocation).
6. Prometheus `/metrics` endpoint.
