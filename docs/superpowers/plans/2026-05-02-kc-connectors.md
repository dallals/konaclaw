# kc-connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four external connectors so KonaClaw is reachable and useful from outside the dashboard: Telegram (bot API), iMessage (chat.db tail + AppleScript send), Gmail (OAuth2), Google Calendar (OAuth2). Each adapter implements a small `Connector` interface; supervisor's router picks which agent handles each inbound chat. Connectors **never** see the filesystem; attachments are deposited into auto-created inbox shares before any agent sees them.

**Architecture:** A package `kc_connectors` containing one module per connector and a small router. Supervisor wires the connectors at boot. Telegram and iMessage are *channels* (inbound + outbound message streams). Gmail and Calendar are *tool-providers* (no inbound — they just expose tools).

**Tech Stack:** Python 3.11+. Telegram: [`python-telegram-bot`](https://docs.python-telegram-bot.org/) v21+. iMessage: stdlib `sqlite3` (read `chat.db`) + `subprocess` (`osascript` send). Gmail/Calendar: [`google-api-python-client`](https://github.com/googleapis/google-api-python-client) + [`google-auth-oauthlib`](https://github.com/googleapis/google-auth-library-python-oauthlib). Depends on `kc-core`, `kc-sandbox`, `kc-supervisor`.

**Repo bootstrap:** `~/Desktop/claudeCode/SammyClaw/kc-connectors/`.

**Scope decisions:**
- ✅ Telegram bot (long-poll), iMessage (chat.db + AppleScript), Gmail OAuth, Calendar OAuth
- ✅ Per-chat routing table (which agent handles which chat)
- ✅ Pairing only via dashboard (never via inbound message — security rule from spec)
- ⏸ Encrypted secrets store integration — deferred to kc-supervisor v0.2; until then we read tokens from a `~/KonaClaw/config/secrets.yaml` (gitignored)
- ⏸ Group reactions, edit-in-place, OAuth token refresh UI polish

---

## File Structure

```
kc-connectors/
├── pyproject.toml
├── README.md
├── SMOKE.md
├── src/
│   └── kc_connectors/
│       ├── __init__.py
│       ├── base.py              # Connector ABC, MessageEnvelope, ConnectorRegistry
│       ├── routing.py           # RoutingTable: chat_id -> agent_name
│       ├── inbox.py             # auto-create per-connector inbox shares
│       ├── telegram_adapter.py
│       ├── imessage_adapter.py  # macOS-only
│       ├── gmail_adapter.py
│       ├── gcal_adapter.py
│       └── secrets.py           # tiny secrets.yaml loader
└── tests/
    ├── conftest.py
    ├── test_base.py
    ├── test_routing.py
    ├── test_telegram.py
    ├── test_imessage.py
    ├── test_gmail.py
    └── test_gcal.py
```

---

## Task 0: Bootstrap

```bash
mkdir -p ~/Desktop/claudeCode/SammyClaw/kc-connectors/src/kc_connectors ~/Desktop/claudeCode/SammyClaw/kc-connectors/tests
cd ~/Desktop/claudeCode/SammyClaw/kc-connectors && git init -b main
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-connectors"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "kc-core",
    "kc-sandbox",
    "kc-supervisor",
    "python-telegram-bot>=21.0",
    "google-api-python-client>=2.130",
    "google-auth-oauthlib>=1.2",
    "google-auth-httplib2>=0.2",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "respx>=0.21", "ruff>=0.4"]

[tool.hatch.build.targets.wheel]
packages = ["src/kc_connectors"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v"
```

```python
# src/kc_connectors/__init__.py
__version__ = "0.1.0"
```

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ../kc-core -e ../kc-sandbox -e ../kc-supervisor -e ".[dev]"
git add . && git commit -m "chore: bootstrap kc-connectors"
```

---

## Task 1: Connector Base + MessageEnvelope + RoutingTable

**Files:**
- Create: `src/kc_connectors/base.py`, `routing.py`, `secrets.py`
- Test: `tests/test_base.py`, `tests/test_routing.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_base.py
import pytest
from kc_connectors.base import MessageEnvelope, Connector


def test_envelope_has_required_fields():
    m = MessageEnvelope(channel="telegram", chat_id="42", sender_id="user42",
                        content="hi", attachments=[])
    assert m.channel == "telegram"


def test_connector_is_abstract():
    with pytest.raises(TypeError):
        Connector("test")  # type: ignore
```

```python
# tests/test_routing.py
import pytest
from kc_connectors.routing import RoutingTable


def test_default_route_to_main_agent():
    rt = RoutingTable(default_agent="KonaClaw")
    assert rt.route(channel="telegram", chat_id="42") == "KonaClaw"


def test_specific_route_overrides():
    rt = RoutingTable(default_agent="KonaClaw")
    rt.set_route(channel="telegram", chat_id="42", agent="ResearchBot")
    assert rt.route(channel="telegram", chat_id="42") == "ResearchBot"
    assert rt.route(channel="telegram", chat_id="99") == "KonaClaw"


def test_yaml_round_trip(tmp_path):
    p = tmp_path / "routes.yaml"
    rt = RoutingTable(default_agent="KonaClaw")
    rt.set_route(channel="telegram", chat_id="42", agent="ResearchBot")
    rt.save_to_yaml(p)
    rt2 = RoutingTable.load_from_yaml(p)
    assert rt2.route("telegram", "42") == "ResearchBot"
```

- [ ] **Step 2: Implement base.py**

```python
# src/kc_connectors/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class MessageEnvelope:
    channel: str            # "telegram", "imessage", "gmail" (inbound is rare here)
    chat_id: str            # connector-scoped chat identifier
    sender_id: str          # connector-scoped sender identifier
    content: str
    attachments: list[Path] = field(default_factory=list)  # paths in the inbox share
    metadata: dict[str, Any] = field(default_factory=dict)


class Connector(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def start(self, supervisor) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, chat_id: str, content: str, attachments: Optional[list[Path]] = None) -> None: ...

    capabilities: set[str] = set()  # e.g., {"send", "react", "edit"}


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Connector] = {}

    def register(self, c: Connector) -> None:
        if c.name in self._by_name:
            raise ValueError(f"connector {c.name!r} already registered")
        self._by_name[c.name] = c

    def get(self, name: str) -> Connector:
        return self._by_name[name]

    def all(self) -> list[Connector]:
        return list(self._by_name.values())
```

- [ ] **Step 3: Implement routing.py**

```python
# src/kc_connectors/routing.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class RoutingTable:
    default_agent: str
    routes: dict[str, dict[str, str]] = field(default_factory=dict)
    # routes[channel][chat_id] -> agent_name

    def route(self, channel: str, chat_id: str) -> str:
        return self.routes.get(channel, {}).get(chat_id, self.default_agent)

    def set_route(self, channel: str, chat_id: str, agent: str) -> None:
        self.routes.setdefault(channel, {})[chat_id] = agent

    def save_to_yaml(self, path: Path) -> None:
        Path(path).write_text(yaml.safe_dump({
            "default_agent": self.default_agent, "routes": self.routes,
        }))

    @classmethod
    def load_from_yaml(cls, path: Path) -> "RoutingTable":
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            default_agent=data.get("default_agent", "KonaClaw"),
            routes=data.get("routes", {}),
        )
```

- [ ] **Step 4: Implement secrets.py**

```python
# src/kc_connectors/secrets.py
from __future__ import annotations
from pathlib import Path
import os
import yaml


def load_secrets() -> dict:
    """Load ~/KonaClaw/config/secrets.yaml (or KC_SECRETS_PATH override).
    NOT encrypted in v1 — encrypted secrets store comes in kc-supervisor v0.2.
    """
    p = Path(os.environ.get("KC_SECRETS_PATH",
                           Path.home() / "KonaClaw" / "config" / "secrets.yaml"))
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}
```

- [ ] **Step 5: Verify + commit**

`pytest tests/test_base.py tests/test_routing.py -v` → PASS.

```bash
git add src/kc_connectors/base.py src/kc_connectors/routing.py src/kc_connectors/secrets.py tests/test_base.py tests/test_routing.py
git commit -m "feat(kc-connectors): add Connector base + RoutingTable + secrets loader"
```

---

## Task 2: Telegram Adapter

**Files:**
- Create: `src/kc_connectors/telegram_adapter.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_telegram.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from kc_connectors.telegram_adapter import TelegramConnector
from kc_connectors.base import MessageEnvelope


@pytest.mark.asyncio
async def test_send_calls_bot_send_message():
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    c._app.bot.send_message = AsyncMock()
    await c.send(chat_id="42", content="hello", attachments=None)
    c._app.bot.send_message.assert_awaited_once()
    kwargs = c._app.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["text"] == "hello"


@pytest.mark.asyncio
async def test_send_to_non_allowlisted_raises():
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    c._app.bot.send_message = AsyncMock()
    with pytest.raises(PermissionError, match="not allowlisted"):
        await c.send(chat_id="999", content="hi", attachments=None)


@pytest.mark.asyncio
async def test_inbound_from_unallowlisted_dropped(tmp_path):
    received = []
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._on_envelope = lambda env: received.append(env)
    fake_update = MagicMock(); fake_update.effective_chat.id = 999
    fake_update.effective_user.id = 1; fake_update.message.text = "hi"
    fake_update.message.photo = []; fake_update.message.document = None
    await c._handle_update(fake_update, MagicMock())
    assert received == []
```

- [ ] **Step 2: Implement telegram_adapter.py**

```python
# src/kc_connectors/telegram_adapter.py
from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional
from kc_connectors.base import Connector, MessageEnvelope


class TelegramConnector(Connector):
    capabilities = {"send"}

    def __init__(
        self,
        token: str,
        allowlist: set[str],
        inbox_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(name="telegram")
        self.token = token
        self.allowlist = set(allowlist)  # set of chat_id strings
        self.inbox_dir = inbox_dir
        self._app = None
        self._on_envelope: Optional[Callable[[MessageEnvelope], None]] = None

    async def start(self, supervisor) -> None:
        from telegram.ext import Application, MessageHandler, filters
        self._on_envelope = supervisor.handle_inbound  # supervisor exposes this
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(MessageHandler(filters.ALL, self._handle_update))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app is None: return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def _handle_update(self, update, context) -> None:
        chat_id = str(update.effective_chat.id)
        if chat_id not in self.allowlist:
            return  # spec: silently drop messages from non-allowlisted chats
        text = update.message.text or ""
        attachments: list[Path] = []
        # (Attachment download into inbox_dir is a v0.2 polish; v1 forwards text only.)
        env = MessageEnvelope(
            channel=self.name, chat_id=chat_id,
            sender_id=str(update.effective_user.id),
            content=text, attachments=attachments,
        )
        if self._on_envelope is not None:
            self._on_envelope(env)

    async def send(self, chat_id: str, content: str, attachments=None) -> None:
        if chat_id not in self.allowlist:
            raise PermissionError(f"chat {chat_id} not allowlisted")
        await self._app.bot.send_message(chat_id=int(chat_id), text=content)
```

- [ ] **Step 3: Verify + commit**

`pytest tests/test_telegram.py -v` → PASS.

```bash
git add src/kc_connectors/telegram_adapter.py tests/test_telegram.py
git commit -m "feat(kc-connectors): add Telegram adapter with allowlist enforcement"
```

---

## Task 3: iMessage Adapter (macOS)

**Files:**
- Create: `src/kc_connectors/imessage_adapter.py`
- Test: `tests/test_imessage.py`

**Why:** Reads `~/Library/Messages/chat.db` for inbound; AppleScript via `osascript` for outbound. Pure-stdlib polling tailer (1-second poll). Allowlist enforced after read so we never expose non-allowlisted messages even within the process.

- [ ] **Step 1: Failing test**

```python
# tests/test_imessage.py
import sqlite3
import pytest
from pathlib import Path
from kc_connectors.imessage_adapter import IMessageConnector


def make_chat_db(p: Path):
    """Build a tiny mock of the chat.db schema we read from."""
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, handle_id INTEGER, is_from_me INTEGER, date INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
    """)
    con.executescript("""
        INSERT INTO chat (ROWID, guid) VALUES (1, 'iMessage;-;+15555550100');
        INSERT INTO handle (ROWID, id) VALUES (1, '+15555550100');
    """)
    con.commit()
    return con


def insert_msg(con, rowid, text, handle, chat=1, from_me=0):
    con.execute("INSERT INTO message (ROWID, text, handle_id, is_from_me, date) VALUES (?,?,?,?,?)",
                (rowid, text, handle, from_me, 0))
    con.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?,?)", (chat, rowid))
    con.commit()


@pytest.mark.asyncio
async def test_poll_reads_new_messages_only(tmp_path):
    db = tmp_path / "chat.db"; con = make_chat_db(db)
    insert_msg(con, 1, "hi from outside", handle=1)

    received = []
    c = IMessageConnector(chat_db_path=db, allowlist={"+15555550100"})
    c._on_envelope = lambda env: received.append(env)
    await c._poll_once()
    assert len(received) == 1
    assert received[0].content == "hi from outside"

    # No new rows -> no duplicate
    await c._poll_once()
    assert len(received) == 1

    # New row -> new envelope
    insert_msg(con, 2, "another", handle=1)
    await c._poll_once()
    assert len(received) == 2


@pytest.mark.asyncio
async def test_non_allowlisted_dropped(tmp_path):
    db = tmp_path / "chat.db"; con = make_chat_db(db)
    insert_msg(con, 1, "from blocked", handle=1, chat=1)
    received = []
    c = IMessageConnector(chat_db_path=db, allowlist={"+15555550999"})  # different number
    c._on_envelope = lambda env: received.append(env)
    await c._poll_once()
    assert received == []
```

- [ ] **Step 2: Implement imessage_adapter.py**

```python
# src/kc_connectors/imessage_adapter.py
from __future__ import annotations
import asyncio
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable, Optional
from kc_connectors.base import Connector, MessageEnvelope


class IMessageConnector(Connector):
    capabilities = {"send"}

    def __init__(
        self,
        chat_db_path: Path,
        allowlist: set[str],
        poll_interval_s: float = 1.0,
    ) -> None:
        super().__init__(name="imessage")
        self.chat_db_path = Path(chat_db_path)
        self.allowlist = set(allowlist)
        self._poll_interval = poll_interval_s
        self._last_rowid = 0
        self._task: Optional[asyncio.Task] = None
        self._on_envelope: Optional[Callable[[MessageEnvelope], None]] = None

    async def start(self, supervisor) -> None:
        self._on_envelope = supervisor.handle_inbound
        # Find current max ROWID so we don't re-emit old messages on first start
        with sqlite3.connect(f"file:{self.chat_db_path}?mode=ro", uri=True) as con:
            cur = con.execute("SELECT IFNULL(MAX(ROWID),0) FROM message")
            self._last_rowid = cur.fetchone()[0]
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task: self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                pass
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        with sqlite3.connect(f"file:{self.chat_db_path}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT m.ROWID, m.text, m.is_from_me, h.id as handle_id, c.guid as chat_guid
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                JOIN chat_message_join j ON j.message_id = m.ROWID
                JOIN chat c ON c.ROWID = j.chat_id
                WHERE m.ROWID > ?
                ORDER BY m.ROWID ASC
            """, (self._last_rowid,)).fetchall()

        for r in rows:
            self._last_rowid = max(self._last_rowid, r["ROWID"])
            if r["is_from_me"]:
                continue
            if r["handle_id"] not in self.allowlist:
                continue  # spec: silently ignore non-allowlisted senders
            env = MessageEnvelope(
                channel=self.name, chat_id=r["chat_guid"],
                sender_id=r["handle_id"],
                content=r["text"] or "",
                attachments=[],  # attachment-into-inbox is v0.2
            )
            if self._on_envelope is not None:
                self._on_envelope(env)

    async def send(self, chat_id: str, content: str, attachments=None) -> None:
        # Send via AppleScript Messages app. chat_id is the chat guid; for DM
        # we recover the handle from the guid.
        handle = chat_id.split(";")[-1]
        if handle not in self.allowlist:
            raise PermissionError(f"chat {chat_id} not allowlisted")
        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            set targetBuddy to buddy "{handle}" of targetService
            send "{content.replace('"', '\\"')}" to targetBuddy
        end tell
        '''
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
```

- [ ] **Step 3: Verify + commit**

`pytest tests/test_imessage.py -v` → PASS (works on Linux too because we use a synthesized chat.db).

```bash
git add src/kc_connectors/imessage_adapter.py tests/test_imessage.py
git commit -m "feat(kc-connectors): add iMessage adapter with chat.db tail + AppleScript send"
```

---

## Task 4: Gmail Adapter (Tools, No Inbound)

**Files:**
- Create: `src/kc_connectors/gmail_adapter.py`
- Test: `tests/test_gmail.py`

**Why:** Gmail isn't an inbound channel here — it's a tool provider. Builds 4 `kc_core.Tool`s: `gmail.search`, `gmail.read_thread`, `gmail.draft`, `gmail.send`. Scopes are pinned to mail-only. Tokens load from `secrets.yaml` for v1.

- [ ] **Step 1: Failing test (with mocked Google client)**

```python
# tests/test_gmail.py
from unittest.mock import MagicMock
from kc_connectors.gmail_adapter import build_gmail_tools


def fake_service(threads_data=None, drafts_data=None):
    """Build a MagicMock that mimics googleapiclient discovery's chained calls."""
    svc = MagicMock()
    svc.users().threads().list().execute.return_value = {"threads": threads_data or []}
    svc.users().threads().get().execute.return_value = {"messages": [{"snippet": "hello"}]}
    svc.users().drafts().create().execute.return_value = {"id": "d1"}
    svc.users().drafts().send().execute.return_value = {"id": "m1"}
    return svc


def test_gmail_search():
    svc = fake_service(threads_data=[{"id": "t1"}, {"id": "t2"}])
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.search"].impl(query="from:billing")
    assert "t1" in out and "t2" in out


def test_gmail_read_thread():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.read_thread"].impl(thread_id="t1")
    assert "hello" in out


def test_gmail_draft_then_send():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    d = tools["gmail.draft"].impl(to="x@y", subject="s", body="b")
    assert "d1" in d
    s = tools["gmail.send"].impl(draft_id="d1")
    assert "m1" in s
```

- [ ] **Step 2: Implement gmail_adapter.py**

```python
# src/kc_connectors/gmail_adapter.py
from __future__ import annotations
import base64
from email.message import EmailMessage
from typing import Any
from kc_core.tools import Tool


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # read + label + drafts
    "https://www.googleapis.com/auth/gmail.send",
]


def build_gmail_tools(service: Any) -> dict[str, Tool]:
    """`service` is a googleapiclient discovery object for gmail v1."""

    def search(query: str, max_results: int = 10) -> str:
        r = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
        threads = r.get("threads", [])
        return "\n".join(f"thread:{t['id']}" for t in threads) or "(no threads)"

    def read_thread(thread_id: str) -> str:
        r = service.users().threads().get(userId="me", id=thread_id).execute()
        msgs = r.get("messages", [])
        return "\n\n".join(m.get("snippet", "") for m in msgs)

    def draft(to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["To"] = to; msg["Subject"] = subject; msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return f"draft created: {r['id']}"

    def send(draft_id: str) -> str:
        r = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return f"sent: {r['id']}"

    def make(name, desc, params, impl):
        return Tool(name=name, description=desc, parameters=params, impl=impl)

    return {
        "gmail.search":      make("gmail.search", "Search Gmail threads.",
            {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}, search),
        "gmail.read_thread": make("gmail.read_thread", "Read a Gmail thread.",
            {"type": "object", "properties": {"thread_id": {"type": "string"}}, "required": ["thread_id"]}, read_thread),
        "gmail.draft":       make("gmail.draft", "Save a Gmail draft.",
            {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}, draft),
        "gmail.send":        make("gmail.send", "Send a previously-saved draft. Destructive.",
            {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"]}, send),
    }


def build_gmail_service(credentials):
    """Real-Google helper; left out of unit tests."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=credentials)
```

- [ ] **Step 3: Verify + commit**

`pytest tests/test_gmail.py -v` → PASS.

```bash
git add src/kc_connectors/gmail_adapter.py tests/test_gmail.py
git commit -m "feat(kc-connectors): add Gmail tools (search/read/draft/send)"
```

---

## Task 5: Google Calendar Adapter (Tools)

**Files:**
- Create: `src/kc_connectors/gcal_adapter.py`
- Test: `tests/test_gcal.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_gcal.py
from unittest.mock import MagicMock
from kc_connectors.gcal_adapter import build_gcal_tools


def fake_service():
    svc = MagicMock()
    svc.events().list().execute.return_value = {"items": [{"id": "e1", "summary": "demo"}]}
    svc.events().insert().execute.return_value = {"id": "e2"}
    svc.events().update().execute.return_value = {"id": "e2"}
    svc.events().delete().execute.return_value = ""
    return svc


def test_list_events():
    svc = fake_service()
    tools = build_gcal_tools(service=svc)
    assert "demo" in tools["gcal.list_events"].impl(time_min="2026-01-01T00:00:00Z", time_max="2026-12-31T00:00:00Z")


def test_create_update_delete():
    svc = fake_service()
    tools = build_gcal_tools(service=svc)
    assert "e2" in tools["gcal.create_event"].impl(summary="x", start="2026-01-01T10:00:00Z", end="2026-01-01T11:00:00Z")
    assert "e2" in tools["gcal.update_event"].impl(event_id="e2", summary="y")
    assert "deleted" in tools["gcal.delete_event"].impl(event_id="e2")
```

- [ ] **Step 2: Implement gcal_adapter.py**

```python
# src/kc_connectors/gcal_adapter.py
from __future__ import annotations
from typing import Any, Optional
from kc_core.tools import Tool


GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def build_gcal_tools(service: Any, calendar_id: str = "primary") -> dict[str, Tool]:

    def list_events(time_min: str, time_max: str) -> str:
        r = service.events().list(calendarId=calendar_id, timeMin=time_min, timeMax=time_max).execute()
        items = r.get("items", [])
        return "\n".join(f"{e['id']}: {e.get('summary', '(no title)')}" for e in items) or "(no events)"

    def create_event(summary: str, start: str, end: str, description: str = "") -> str:
        body = {"summary": summary, "description": description,
                "start": {"dateTime": start}, "end": {"dateTime": end}}
        r = service.events().insert(calendarId=calendar_id, body=body).execute()
        return f"created event {r['id']}"

    def update_event(event_id: str, summary: Optional[str] = None,
                     start: Optional[str] = None, end: Optional[str] = None) -> str:
        body: dict = {}
        if summary is not None: body["summary"] = summary
        if start is not None: body["start"] = {"dateTime": start}
        if end is not None: body["end"] = {"dateTime": end}
        r = service.events().update(calendarId=calendar_id, eventId=event_id, body=body).execute()
        return f"updated event {r['id']}"

    def delete_event(event_id: str) -> str:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return f"deleted event {event_id}"

    def make(n, d, p, i): return Tool(name=n, description=d, parameters=p, impl=i)

    return {
        "gcal.list_events":  make("gcal.list_events", "List calendar events between two RFC3339 times.",
            {"type": "object", "properties": {"time_min": {"type": "string"}, "time_max": {"type": "string"}}, "required": ["time_min", "time_max"]}, list_events),
        "gcal.create_event": make("gcal.create_event", "Create a calendar event. Destructive.",
            {"type": "object", "properties": {"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}}, "required": ["summary", "start", "end"]}, create_event),
        "gcal.update_event": make("gcal.update_event", "Update a calendar event.",
            {"type": "object", "properties": {"event_id": {"type": "string"}, "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["event_id"]}, update_event),
        "gcal.delete_event": make("gcal.delete_event", "Delete a calendar event. Destructive.",
            {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}, delete_event),
    }


def build_gcal_service(credentials):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=credentials)
```

- [ ] **Step 3: Verify + commit**

`pytest tests/test_gcal.py -v` → PASS.

```bash
git add src/kc_connectors/gcal_adapter.py tests/test_gcal.py
git commit -m "feat(kc-connectors): add Google Calendar tools"
```

---

## Task 6: SMOKE.md + README

```markdown
# kc-connectors — Smoke Checklist

## Prereqs

- `~/KonaClaw/config/secrets.yaml` populated:
  ```yaml
  telegram_bot_token: "..."
  google_credentials_json_path: "/path/to/credentials.json"  # OAuth client secret
  ```

## Telegram

- [ ] Send a message from your phone to the bot. The supervisor logs the inbound, the routed agent replies via the bot, you see the reply on your phone.
- [ ] Send from a non-allowlisted chat — the supervisor sees nothing.

## iMessage (macOS only)

- [ ] System Settings → Privacy & Security → Full Disk Access → grant the Python interpreter / supervisor process.
- [ ] Send an iMessage from an allowlisted handle. Watch the supervisor log it; routed agent replies via Messages.app.

## Gmail / Calendar

- [ ] First run: `kc-supervisor` opens the Google OAuth consent flow in your browser; you grant only mail-modify, mail-send, calendar.
- [ ] Through the dashboard: ask KonaClaw to "list my next 3 calendar events" — agent calls `gcal.list_events`, you see the output.
- [ ] Ask "draft an email to me with subject Test and body Hello" — `gmail.draft` succeeds and shows the draft id.
- [ ] Ask "send draft <id>" — destructive approval pops in dashboard; on approve, the email is sent.

## Negative cases

- [ ] OAuth consent revoked externally (https://myaccount.google.com/permissions) — next gmail call fails cleanly with a token-error message that the agent surfaces back to the user.
- [ ] An inbound iMessage with `is_from_me=1` is silently ignored (the supervisor doesn't reply to itself).
```

```markdown
# kc-connectors

KonaClaw connectors — sub-project 6 of 8. Provides:

- Telegram bot (long-poll, allowlist, send)
- iMessage (chat.db tail + AppleScript send, allowlist)
- Gmail tools (search/read/draft/send) via OAuth2
- Google Calendar tools (list/create/update/delete) via OAuth2
- Routing table (per-chat → agent name)
- `secrets.yaml` loader (encrypted store comes in kc-supervisor v0.2)

Depends on `kc-core`, `kc-sandbox`, `kc-supervisor`.

Tests run on any platform; the iMessage adapter uses a synthesized chat.db
schema in tests so it doesn't require macOS for unit tests (only runtime).
```

```bash
git add SMOKE.md README.md && git commit -m "docs(kc-connectors): add SMOKE.md and README"
```

---

## Done Criteria

- All four adapters tested in CI (with mocks) and manually smoke-tested per SMOKE.md.
- Every adapter respects an allowlist; non-allowlisted inbound is silently dropped.
- Pairing only via dashboard — no inbound message can grant itself access.
- Gmail/Calendar limited to mail + calendar scopes; **no Drive scope ever**.

This unblocks **kc-zapier** (sub-project 7).

## Known v0.2 Follow-Ups

- Telegram & iMessage attachment download into auto-created inbox shares.
- OAuth refresh-flow polish + dashboard "Connect Google" button (today the consent runs in the terminal on first start).
- Encrypted secrets store (kc-supervisor v0.2).
- Telegram reactions, edit-in-place "thinking…" UX.
- iMessage group chat support beyond DMs.
