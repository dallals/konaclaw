# kc-sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the sandbox layer for KonaClaw — named allowlisted "shares" with path-traversal protection, a tiered permission engine with per-agent overrides, sandboxed file tools that an agent can use, a git-backed write journal per share, and an undo API. When done, a kc-core agent can read/write/delete files inside an allowlisted share, every write is journaled as a git commit, and a single delete can be undone via a Python API.

**Architecture:** A separate Python package `kc_sandbox` that depends on `kc_core` via local install. Adds (1) a one-line hook in `kc_core.Agent` so a permission check can fire before any tool runs (the only kc-core change in this plan), (2) a `SharesRegistry` that resolves `share + relpath` safely, (3) a `PermissionEngine` that classifies every tool call by tier and consults per-agent overrides, (4) a set of sandboxed `file.*` tools that take a share + relpath, (5) a git-backed `Journal` that commits every mutating op, and (6) an `UndoLog` (SQLite) + `Undoer` that can reverse file ops.

**Tech Stack:** Python 3.11+, depends on `kc-core` (sub-project 1). Uses stdlib `subprocess` to drive system `git` for the per-share journal (no GitPython — keeps the dep tree minimal and avoids a fragile `.git`/`.kc-journal` rename pattern), stdlib `sqlite3` for the undo log, stdlib `dataclasses` for all config/model objects (matching kc-core's choice — pydantic is not used). Tests use pytest + `tmp_path` for filesystem isolation. Requires the `git` binary on `PATH`.

**Repo bootstrap:** Build in `~/Desktop/claudeCode/SammyClaw/kc-sandbox/` alongside `kc-core/`. The two repos sit side-by-side; `kc-sandbox` installs `kc-core` in editable mode (`pip install -e ../kc-core`).

---

## File Structure

```
kc-sandbox/
├── pyproject.toml
├── README.md
├── SMOKE.md
├── src/
│   └── kc_sandbox/
│       ├── __init__.py
│       ├── shares.py            # Share, SharesRegistry, path resolution + traversal guard
│       ├── permissions.py       # Tier enum, PermissionEngine, Decision, ApprovalCallback
│       ├── journal.py           # Per-share git journal: init + commit
│       ├── undo.py              # UndoLog (SQLite) + Undoer (revert by audit_id)
│       ├── tools.py             # file.read / file.list / file.grep / file.write / file.delete
│       └── wiring.py            # build_sandboxed_agent(): one-call wiring of all of the above
└── tests/
    ├── conftest.py              # tmp share, fake approval callback, helper to wire kc-core agent
    ├── test_shares.py
    ├── test_permissions.py
    ├── test_journal.py
    ├── test_undo.py
    ├── test_tools.py
    ├── test_wiring.py
    └── fixtures/
        └── agents/
            └── filebot.yaml
```

Plus one small change in the **kc-core** repo (Task 1): add a `permission_check` hook on `Agent`.

---

## Task 0: Bootstrap kc-sandbox Repo

**Files:**
- Create: `kc-sandbox/pyproject.toml`
- Create: `kc-sandbox/.gitignore`
- Create: `kc-sandbox/README.md`
- Create: `kc-sandbox/src/kc_sandbox/__init__.py`
- Create: `kc-sandbox/tests/__init__.py`

- [ ] **Step 1: Create the project directory and initialize git**

```bash
mkdir -p ~/Desktop/claudeCode/SammyClaw/kc-sandbox
cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
git init -b main
```

- [ ] **Step 2: Create `pyproject.toml` (note local kc-core dep)**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-sandbox"
version = "0.1.0"
description = "KonaClaw sandbox: shares, permissions, undo"
requires-python = ">=3.11"
dependencies = [
    "kc-core",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
]

[tool.hatch.build.targets.wheel]
packages = ["src/kc_sandbox"]

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
# src/kc_sandbox/__init__.py
__version__ = "0.1.0"
```

```python
# tests/__init__.py
```

- [ ] **Step 5: Create README stub**

```markdown
# kc-sandbox

KonaClaw sandbox layer (sub-project 2 of 8). Provides named "shares," tiered
permissions, sandboxed file tools, a git-backed write journal, and undo.

Depends on `kc-core`. See umbrella spec for context.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core
    pip install -e ".[dev]"
```

- [ ] **Step 6: Create venv, install both packages, verify import**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ../kc-core
pip install -e ".[dev]"
python -c "import kc_sandbox; import kc_core; print(kc_sandbox.__version__, kc_core.__version__)"
```
Expected: `0.1.0 0.1.0`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore README.md src/ tests/
git commit -m "chore: bootstrap kc-sandbox package"
```

---

## Task 1: Extend kc-core — Add `permission_check` Hook on Agent

**Files (in the `kc-core` repo, not kc-sandbox):**
- Modify: `kc-core/src/kc_core/agent.py`
- Modify: `kc-core/tests/test_agent.py`

**Why:** kc-sandbox's permission engine needs to gate every tool call from inside the agent loop. The cleanest seam is a single optional callback on `Agent` that fires right before `self.tools.invoke(...)`. If the callback returns `False`, the tool result is replaced with a deny message, the agent gets it back, and the loop continues. This is the **only** kc-core change in this whole plan.

- [ ] **Step 1: Add a failing test to `kc-core/tests/test_agent.py`**

```python
@pytest.mark.asyncio
async def test_agent_permission_check_can_deny_tool(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="couldn't run it", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))

    seen_calls = []
    def deny_all(agent_name: str, tool_name: str, arguments: dict) -> tuple[bool, str | None]:
        seen_calls.append((agent_name, tool_name, arguments))
        return (False, "Denied: this is a test")

    agent = Agent(
        name="kc", client=client, system_prompt="sys", tools=reg,
        permission_check=deny_all,
    )
    reply = await agent.send("please echo hi")
    assert reply.content == "couldn't run it"
    # The denied tool result should have been surfaced back to the model
    second = client.calls[1]["messages"]
    err = next(m for m in second if m["role"] == "tool")
    assert "Denied" in err["content"]
    assert seen_calls == [("kc", "echo", {"text": "hi"})]


@pytest.mark.asyncio
async def test_agent_no_permission_check_allows_all(fake_ollama):
    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="done", finish_reason="stop"),
    )
    reg = ToolRegistry()
    reg.register(Tool(name="echo", description="", parameters={}, impl=lambda text: text))
    agent = Agent(name="kc", client=client, system_prompt="sys", tools=reg)
    reply = await agent.send("echo")
    assert reply.content == "done"
```

- [ ] **Step 2: Run test to verify it fails**

In `kc-core/`: `pytest tests/test_agent.py::test_agent_permission_check_can_deny_tool -v`
Expected: FAIL — `Agent.__init__()` doesn't accept `permission_check`.

- [ ] **Step 3: Modify `kc-core/src/kc_core/agent.py`**

Update the `Agent` dataclass and `_run_loop`:

```python
# Add this type alias at module top:
from typing import Callable, Optional
PermissionCheck = Callable[[str, str, dict], tuple[bool, Optional[str]]]
# (agent_name, tool_name, arguments) -> (allowed, optional_deny_reason)
```

Add the field to `Agent`:

```python
@dataclass
class Agent:
    name: str
    client: _ChatClient
    system_prompt: str
    tools: ToolRegistry
    max_tool_iterations: int = 10
    history: list[Message] = field(default_factory=list)
    permission_check: Optional[PermissionCheck] = None   # <-- new
```

Update the tool-execution branch in `_run_loop`. The existing code records ALL `ToolCallMessage`s first, then ALL `ToolResultMessage`s — this is load-bearing because `_build_wire_messages()` collects consecutive `ToolCallMessage`s into a single OpenAI assistant message. The permission check must preserve that two-pass structure: insert it INSIDE the existing `for c in calls:` loop, before `self.tools.invoke(...)`, and on deny append the deny string to the local `results` list (do NOT append a `ToolResultMessage` directly to `self.history` and do NOT add a second results loop):

```python
            results: list[tuple[str, str]] = []
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                # NEW: permission check — short-circuits before tool execution.
                # On deny, push the deny message into `results` so it lands in
                # the second loop alongside any allowed results.
                if self.permission_check is not None:
                    allowed, reason = self.permission_check(self.name, c["name"], c["arguments"])
                    if not allowed:
                        results.append((c["id"], f"Denied: {reason or 'permission_check returned False'}"))
                        continue
                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    content = str(result)
                except KeyError:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                results.append((c["id"], content))
            for call_id, content in results:
                self.history.append(ToolResultMessage(
                    tool_call_id=call_id,
                    content=content,
                ))
```

Note: leave the second `for call_id, content in results:` loop unchanged from the existing implementation — only the `for c in calls:` loop changes (added permission check, replaced direct `self.history.append(ToolResultMessage(...))` with `results.append(...)`).

- [ ] **Step 4: Run all kc-core tests to verify nothing regressed**

In `kc-core/`: `pytest tests/ --ignore=tests/live -v`
Expected: PASS — all kc-core tests green, including the 2 new ones.

- [ ] **Step 5: Commit (in the kc-core repo)**

```bash
cd ~/Desktop/claudeCode/SammyClaw/kc-core
git add src/kc_core/agent.py tests/test_agent.py
git commit -m "feat(kc-core): add optional permission_check hook on Agent"
cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
```

---

## Task 2: Shares Model + Path Resolution

**Files:**
- Create: `src/kc_sandbox/shares.py`
- Test: `tests/test_shares.py`

**Why:** A "share" is the only way the sandbox sees the filesystem. This module models a share, loads them from YAML, and provides `resolve(share_name, relpath)` that returns an absolute path *only* if the result is genuinely inside the share root after symlink resolution. Anything that escapes — `../`, absolute paths, symlinks pointing out — is rejected.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shares.py
from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry, ShareError


def test_resolve_inside_share(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    p = r.resolve("research", "notes/today.md")
    assert p == (tmp_path / "research" / "notes" / "today.md").resolve()


def test_resolve_unknown_share(tmp_path):
    r = SharesRegistry([])
    with pytest.raises(ShareError, match="unknown share"):
        r.resolve("nope", "x.txt")


def test_resolve_rejects_dotdot(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="escapes share"):
        r.resolve("research", "../secrets.txt")


def test_resolve_rejects_absolute_relpath(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="must be relative"):
        r.resolve("research", "/etc/passwd")


def test_resolve_rejects_symlink_escape(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    (tmp_path / "research" / "link").symlink_to(tmp_path / "outside.txt")
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="escapes share"):
        r.resolve("research", "link")


def test_can_write_respects_mode(tmp_path):
    (tmp_path / "ro").mkdir()
    s = Share(name="ro", path=tmp_path / "ro", mode="read-only")
    r = SharesRegistry([s])
    assert r.can_write("ro") is False
    assert r.can_read("ro") is True


def test_load_from_yaml(tmp_path):
    cfg = tmp_path / "shares.yaml"
    (tmp_path / "research").mkdir()
    cfg.write_text(f"""
shares:
  - name: research
    path: {tmp_path / 'research'}
    mode: read-write
""")
    r = SharesRegistry.from_yaml(cfg)
    assert r.resolve("research", "x.md") == (tmp_path / "research" / "x.md").resolve()
```

- [ ] **Step 2: Run test to verify it fails**

`pytest tests/test_shares.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kc_sandbox.shares'`.

- [ ] **Step 3: Implement shares.py**

```python
# src/kc_sandbox/shares.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal
import yaml


class ShareError(Exception):
    pass


Mode = Literal["read-write", "read-only"]


@dataclass
class Share:
    name: str
    path: Path
    mode: Mode = "read-write"

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser().resolve()
        if not self.path.is_dir():
            raise ShareError(f"share {self.name!r}: path {self.path} is not a directory")
        if self.mode not in ("read-write", "read-only"):
            raise ShareError(f"share {self.name!r}: mode must be read-write or read-only")


class SharesRegistry:
    def __init__(self, shares: Iterable[Share]) -> None:
        self._by_name: dict[str, Share] = {}
        for s in shares:
            if s.name in self._by_name:
                raise ShareError(f"duplicate share: {s.name}")
            self._by_name[s.name] = s

    @classmethod
    def from_yaml(cls, path: Path | str) -> "SharesRegistry":
        data = yaml.safe_load(Path(path).read_text()) or {}
        shares = [
            Share(name=s["name"], path=Path(s["path"]), mode=s.get("mode", "read-write"))
            for s in data.get("shares", [])
        ]
        return cls(shares)

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def get(self, name: str) -> Share:
        if name not in self._by_name:
            raise ShareError(f"unknown share: {name}")
        return self._by_name[name]

    def can_read(self, name: str) -> bool:
        return name in self._by_name

    def can_write(self, name: str) -> bool:
        return self.get(name).mode == "read-write"

    def resolve(self, name: str, relpath: str) -> Path:
        share = self.get(name)
        rp = Path(relpath)
        if rp.is_absolute():
            raise ShareError(f"share {name!r}: relpath must be relative, got {relpath!r}")

        # Build the candidate path then fully resolve symlinks
        candidate = (share.path / rp).resolve()

        # The fully-resolved candidate must be inside the share root.
        try:
            candidate.relative_to(share.path)
        except ValueError:
            raise ShareError(f"share {name!r}: path {relpath!r} escapes share root")
        return candidate
```

- [ ] **Step 4: Run test to verify it passes**

`pytest tests/test_shares.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_sandbox/shares.py tests/test_shares.py
git commit -m "feat(kc-sandbox): add shares model with traversal-safe resolution"
```

---

## Task 3: Permission Engine

**Files:**
- Create: `src/kc_sandbox/permissions.py`
- Test: `tests/test_permissions.py`

**Why:** The engine classifies tools by tier (Safe / Mutating / Destructive), consults per-agent and global overrides, and either auto-allows or routes to an `ApprovalCallback` for human approval. The callback is async (so the supervisor can later wire it to a dashboard WebSocket); for kc-sandbox tests we use a synchronous fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_permissions.py
import pytest
from kc_sandbox.permissions import (
    Tier, PermissionEngine, Decision, AlwaysAllow, AlwaysDeny,
)


def test_safe_tool_auto_allowed():
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.source == "tier"


def test_destructive_tool_routes_to_callback():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={"share": "x", "relpath": "y"})
    assert d.allowed is True
    assert d.source == "callback"


def test_destructive_denied_by_callback():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=AlwaysDeny(reason="user said no"),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is False
    assert "user said no" in (d.reason or "")


def test_per_agent_override_promotes_safe_to_destructive():
    eng = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={"kc": {"file.read": Tier.DESTRUCTIVE}},
        approval_callback=AlwaysDeny(reason="nope"),
    )
    d = eng.check(agent="kc", tool="file.read", arguments={})
    assert d.allowed is False


def test_per_agent_override_demotes_destructive_to_safe():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={"kc": {"file.delete": Tier.SAFE}},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is True


def test_unknown_tool_defaults_to_destructive():
    """Spec rule: newly-installed/unknown tools must default destructive."""
    eng = PermissionEngine(
        tier_map={},  # tool unknown
        agent_overrides={},
        approval_callback=AlwaysDeny(),
    )
    d = eng.check(agent="kc", tool="mcp.something_new", arguments={})
    assert d.allowed is False  # destructive + AlwaysDeny


def test_other_agent_override_does_not_apply():
    eng = PermissionEngine(
        tier_map={"file.delete": Tier.DESTRUCTIVE},
        agent_overrides={"EmailBot": {"file.delete": Tier.SAFE}},
        approval_callback=AlwaysDeny(reason="x"),
    )
    d = eng.check(agent="kc", tool="file.delete", arguments={})
    assert d.allowed is False  # kc still destructive, callback denies
```

- [ ] **Step 2: Run test to verify it fails**

`pytest tests/test_permissions.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement permissions.py**

```python
# src/kc_sandbox/permissions.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol


class Tier(str, Enum):
    SAFE = "safe"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


@dataclass
class Decision:
    allowed: bool
    tier: Tier
    source: str            # "tier", "callback", "override"
    reason: Optional[str] = None


class ApprovalCallback(Protocol):
    """Returns (allowed, reason). Called only for DESTRUCTIVE tier."""
    def __call__(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[bool, Optional[str]]: ...


@dataclass
class AlwaysAllow:
    def __call__(self, agent, tool, arguments):
        return (True, None)


@dataclass
class AlwaysDeny:
    reason: Optional[str] = None
    def __call__(self, agent, tool, arguments):
        return (False, self.reason)


class PermissionEngine:
    def __init__(
        self,
        tier_map: dict[str, Tier],
        agent_overrides: dict[str, dict[str, Tier]],
        approval_callback: ApprovalCallback,
    ) -> None:
        self.tier_map = dict(tier_map)
        self.agent_overrides = {a: dict(o) for a, o in agent_overrides.items()}
        self.approval_callback = approval_callback

    def check(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        # Resolve effective tier
        override = self.agent_overrides.get(agent, {}).get(tool)
        if override is not None:
            tier = override
            source = "override"
        else:
            # Spec rule: unknown tools default to DESTRUCTIVE
            tier = self.tier_map.get(tool, Tier.DESTRUCTIVE)
            source = "tier"

        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)

        # DESTRUCTIVE — ask the callback
        allowed, reason = self.approval_callback(agent, tool, arguments)
        return Decision(allowed=allowed, tier=tier, source="callback" if source != "override" else source, reason=reason)

    def to_agent_callback(self, agent: str):
        """Returns a callable in the shape kc_core.Agent.permission_check expects."""
        def _check(agent_name: str, tool: str, args: dict) -> tuple[bool, Optional[str]]:
            d = self.check(agent=agent_name, tool=tool, arguments=args)
            return (d.allowed, d.reason)
        return _check
```

- [ ] **Step 4: Run test to verify it passes**

`pytest tests/test_permissions.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_sandbox/permissions.py tests/test_permissions.py
git commit -m "feat(kc-sandbox): add tiered permission engine with overrides"
```

---

## Task 4: Git-Backed Per-Share Journal

**Files:**
- Create: `src/kc_sandbox/journal.py`
- Test: `tests/test_journal.py`

**Why:** Every mutating file op becomes one commit in a per-share git directory at `<share_root>/.kc-journal/`. This gives us free history and free `git revert`-based undo.

**Approach:** the share root is the git work tree; `.kc-journal/` is the git directory. We drive plain `git` via `subprocess`, passing `--git-dir=<share>/.kc-journal --work-tree=<share>` on every call. There is **no** `.git` directory or file in the share root — the user sees a normal directory. This avoids both GitPython (one less dep) and any rename trick (no risk of leaving the share in an inconsistent state on crash). Requires `git` on `PATH`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_journal.py
from pathlib import Path
import pytest
from kc_sandbox.journal import Journal


def test_init_creates_journal_dir(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    assert (tmp_path / ".kc-journal").is_dir()
    assert (tmp_path / ".kc-journal" / "HEAD").is_file()


def test_init_idempotent(tmp_path):
    Journal(tmp_path).init()
    Journal(tmp_path).init()  # second call must not raise


def test_commit_records_a_write(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("hello\n")
    sha = j.commit(message="wrote notes.md", author_agent="kc", paths=[f])
    assert isinstance(sha, str) and len(sha) >= 7
    assert "wrote notes.md" in j.log()[0]["message"]


def test_revert_restores_previous_content(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("v1\n")
    sha1 = j.commit(message="v1", author_agent="kc", paths=[f])
    f.write_text("v2\n")
    sha2 = j.commit(message="v2", author_agent="kc", paths=[f])
    j.revert(sha2)
    assert f.read_text() == "v1\n"


def test_revert_restores_deleted_file(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("hello\n")
    j.commit(message="create", author_agent="kc", paths=[f])
    f.unlink()
    sha_del = j.commit(message="delete", author_agent="kc", paths=[f])
    j.revert(sha_del)
    assert f.read_text() == "hello\n"


def test_journal_dir_excluded_from_listing(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    assert ".kc-journal" not in [p.name for p in tmp_path.iterdir() if p.name != ".kc-journal"]
```

- [ ] **Step 2: Run test to verify it fails**

`pytest tests/test_journal.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement journal.py**

```python
# src/kc_sandbox/journal.py
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Iterable


class JournalError(Exception):
    pass


class Journal:
    """A per-share git journal stored at <share_root>/.kc-journal/.

    No .git directory or file is created in the share root. All git
    invocations pass --git-dir and --work-tree explicitly, so the share
    root looks like a normal directory to the user.
    """

    JOURNAL_DIR_NAME = ".kc-journal"

    def __init__(self, share_root: Path) -> None:
        self.root = Path(share_root).resolve()
        self.git_dir = self.root / self.JOURNAL_DIR_NAME

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.root}", *args]
        try:
            return subprocess.run(cmd, check=check, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise JournalError(
                f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e

    def init(self) -> None:
        if self.git_dir.is_dir():
            return
        self.git_dir.mkdir(parents=True)
        self._git("init", "--quiet", "--initial-branch=main")
        self._git("config", "user.name", "konaclaw")
        self._git("config", "user.email", "konaclaw@local")
        self._git("config", "commit.gpgsign", "false")
        # Empty initial commit so revert always has a parent.
        self._git("commit", "--allow-empty", "--quiet", "-m", "init journal")

    def commit(self, message: str, author_agent: str, paths: Iterable[Path]) -> str:
        rel = [Path(p).resolve().relative_to(self.root).as_posix() for p in paths]
        # `git add --all -- <paths>` covers create, modify, AND delete.
        self._git("add", "--all", "--", *rel)
        # Per-call author override so the commit reflects which agent acted.
        self._git(
            "-c", f"user.name=konaclaw {author_agent}",
            "commit", "--allow-empty", "--quiet", "-m", message,
        )
        return self._git("rev-parse", "HEAD").stdout.strip()

    def revert(self, sha: str) -> str:
        """Revert the given commit. Returns the new commit's SHA."""
        self._git("revert", "--no-edit", sha)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def log(self) -> list[dict]:
        out = self._git("log", "--pretty=format:%H%x1f%an%x1f%s").stdout
        entries: list[dict] = []
        for line in out.splitlines():
            if not line:
                continue
            sha, author, msg = line.split("\x1f", 2)
            entries.append({"sha": sha, "message": msg, "author": author})
        return entries
```

- [ ] **Step 4: Run test to verify it passes**

`pytest tests/test_journal.py -v`
Expected: PASS — all 6 tests green. (If `git revert` complains on a delete-restoration, the fix is to add `--strategy-option=theirs` to the `revert` call: `self._git("revert", "--no-edit", "--strategy-option=theirs", sha)`.)

- [ ] **Step 5: Commit**

```bash
git add src/kc_sandbox/journal.py tests/test_journal.py
git commit -m "feat(kc-sandbox): add per-share git journal with revert"
```

---

## Task 5: UndoLog (SQLite) + Undoer

**Files:**
- Create: `src/kc_sandbox/undo.py`
- Test: `tests/test_undo.py`

**Why:** The journal handles file-revert. The undo *log* records, for every reversible action, the kind of reversal needed and the payload to do it. For kc-sandbox v1 the only `reverse_kind` is `"git-revert"` (sha + share). External-action reversals (calendar event delete, MCP uninstall) come in later sub-projects but reuse the same table.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_undo.py
from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog, Undoer, UndoEntry


@pytest.fixture
def share_with_journal(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    j = Journal(root); j.init()
    s = Share(name="research", path=root, mode="read-write")
    return SharesRegistry([s]), j, root


def test_undo_log_round_trip(tmp_path):
    log = UndoLog(db_path=tmp_path / "undo.db")
    log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "research", "sha": "abc123"},
    ))
    assert eid > 0
    e = log.get(eid)
    assert e.reverse_kind == "git-revert"
    assert e.reverse_payload["sha"] == "abc123"


def test_undoer_reverts_a_recorded_entry(share_with_journal, tmp_path):
    shares, journal, root = share_with_journal
    f = root / "notes.md"
    f.write_text("v1\n")
    sha = journal.commit("v1", "kc", [f])
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "research", "sha": sha},
    ))
    undoer = Undoer(shares=shares, journals={"research": journal}, log=log)
    undoer.undo(eid)
    assert not f.exists()  # write is reverted -> file removed
    assert log.get(eid).applied_at is not None


def test_unknown_reverse_kind_raises(tmp_path, share_with_journal):
    shares, journal, root = share_with_journal
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="external", reverse_kind="not-implemented-yet",
        reverse_payload={},
    ))
    undoer = Undoer(shares=shares, journals={"research": journal}, log=log)
    with pytest.raises(NotImplementedError):
        undoer.undo(eid)
```

- [ ] **Step 2: Run test to verify it fails**

`pytest tests/test_undo.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement undo.py**

```python
# src/kc_sandbox/undo.py
from __future__ import annotations
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from kc_sandbox.journal import Journal
from kc_sandbox.shares import SharesRegistry


@dataclass
class UndoEntry:
    agent: str
    tool: str
    reverse_kind: str
    reverse_payload: dict[str, Any]
    id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    applied_at: Optional[float] = None


class UndoLog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def init(self) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    reverse_kind TEXT NOT NULL,
                    reverse_payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    applied_at REAL
                )
            """)

    def record(self, e: UndoEntry) -> int:
        with sqlite3.connect(self.db_path) as c:
            cur = c.execute(
                "INSERT INTO undo_log (agent, tool, reverse_kind, reverse_payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (e.agent, e.tool, e.reverse_kind, json.dumps(e.reverse_payload), e.created_at),
            )
            return int(cur.lastrowid)

    def get(self, eid: int) -> UndoEntry:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT id, agent, tool, reverse_kind, reverse_payload, created_at, applied_at "
                "FROM undo_log WHERE id = ?", (eid,)
            ).fetchone()
        if row is None:
            raise KeyError(f"undo entry {eid} not found")
        return UndoEntry(
            id=row[0], agent=row[1], tool=row[2], reverse_kind=row[3],
            reverse_payload=json.loads(row[4]),
            created_at=row[5], applied_at=row[6],
        )

    def mark_applied(self, eid: int) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute("UPDATE undo_log SET applied_at = ? WHERE id = ?", (time.time(), eid))


class Undoer:
    def __init__(self, shares: SharesRegistry, journals: dict[str, Journal], log: UndoLog) -> None:
        self.shares = shares
        self.journals = journals
        self.log = log

    def undo(self, entry_id: int) -> None:
        e = self.log.get(entry_id)
        if e.applied_at is not None:
            raise ValueError(f"undo {entry_id} already applied at {e.applied_at}")

        if e.reverse_kind == "git-revert":
            share = e.reverse_payload["share"]
            sha = e.reverse_payload["sha"]
            j = self.journals.get(share)
            if j is None:
                raise KeyError(f"no journal for share {share!r}")
            j.revert(sha)
            self.log.mark_applied(entry_id)
            return

        raise NotImplementedError(f"unknown reverse_kind: {e.reverse_kind!r}")
```

- [ ] **Step 4: Run test to verify it passes**

`pytest tests/test_undo.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_sandbox/undo.py tests/test_undo.py
git commit -m "feat(kc-sandbox): add undo log and Undoer"
```

---

## Task 6: Sandboxed File Tools

**Files:**
- Create: `src/kc_sandbox/tools.py`
- Test: `tests/test_tools.py`

**Why:** This is what the agent actually calls. Each tool takes `share` + `relpath`, resolves through `SharesRegistry`, performs the op, and (for mutating/destructive ops) commits via the share's `Journal` and records in `UndoLog`. All tools return strings (the agent ingests text).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools.py
from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry, ShareError
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog
from kc_sandbox.tools import build_file_tools


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    j = Journal(root); j.init()
    shares = SharesRegistry([Share("research", root, "read-write")])
    log = UndoLog(tmp_path / "u.db"); log.init()
    journals = {"research": j}
    tools = build_file_tools(shares=shares, journals=journals, undo_log=log, agent_name="kc")
    return shares, j, log, tools, root


def test_file_write_creates_file_and_journals(env):
    _, j, log, tools, root = env
    res = tools["file.write"].impl(share="research", relpath="x.md", content="hi\n")
    assert "wrote" in res
    assert (root / "x.md").read_text() == "hi\n"
    assert len(j.log()) == 2  # init + this write


def test_file_read_returns_content(env):
    _, _, _, tools, root = env
    (root / "x.md").write_text("hello\n")
    res = tools["file.read"].impl(share="research", relpath="x.md")
    assert "hello" in res


def test_file_list_lists_files(env):
    _, _, _, tools, root = env
    (root / "a.md").write_text("a")
    (root / "b.md").write_text("b")
    res = tools["file.list"].impl(share="research", relpath=".")
    assert "a.md" in res and "b.md" in res
    assert ".kc-journal" not in res  # never expose the journal dir


def test_file_delete_removes_and_journals_and_logs_undo(env):
    _, j, log, tools, root = env
    (root / "x.md").write_text("hello\n")
    j.commit("create x", "kc", [root / "x.md"])
    res = tools["file.delete"].impl(share="research", relpath="x.md")
    assert "deleted" in res
    assert not (root / "x.md").exists()
    # An UndoLog entry should exist
    e = log.get(1)
    assert e.reverse_kind == "git-revert"


def test_file_write_to_readonly_share_raises(tmp_path):
    root = tmp_path / "ro"; root.mkdir()
    Journal(root).init()
    shares = SharesRegistry([Share("ro", root, "read-only")])
    log = UndoLog(tmp_path / "u.db"); log.init()
    tools = build_file_tools(shares, {"ro": Journal(root)}, log, agent_name="kc")
    with pytest.raises(ShareError, match="read-only"):
        tools["file.write"].impl(share="ro", relpath="x", content="y")


def test_path_traversal_blocked(env):
    _, _, _, tools, _ = env
    with pytest.raises(ShareError, match="escapes"):
        tools["file.read"].impl(share="research", relpath="../etc/passwd")
```

- [ ] **Step 2: Run test to verify it fails**

`pytest tests/test_tools.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement tools.py**

```python
# src/kc_sandbox/tools.py
from __future__ import annotations
from pathlib import Path
from kc_core.tools import Tool
from kc_sandbox.journal import Journal
from kc_sandbox.shares import SharesRegistry, ShareError
from kc_sandbox.undo import UndoLog, UndoEntry


def build_file_tools(
    shares: SharesRegistry,
    journals: dict[str, Journal],
    undo_log: UndoLog,
    agent_name: str,
) -> dict[str, Tool]:
    """Construct a set of sandboxed file.* tools bound to a specific agent."""

    def _journal_for(share: str) -> Journal:
        j = journals.get(share)
        if j is None:
            raise ShareError(f"no journal configured for share {share!r}")
        return j

    # ---- READ ----
    def file_read(share: str, relpath: str) -> str:
        p = shares.resolve(share, relpath)
        return p.read_text()

    # ---- LIST ----
    def file_list(share: str, relpath: str = ".") -> str:
        p = shares.resolve(share, relpath)
        if not p.is_dir():
            raise ShareError(f"{relpath}: not a directory in share {share!r}")
        names = sorted([
            x.name + ("/" if x.is_dir() else "")
            for x in p.iterdir()
            if x.name != Journal.JOURNAL_DIR_NAME
        ])
        return "\n".join(names)

    # ---- WRITE ----
    def file_write(share: str, relpath: str, content: str) -> str:
        if not shares.can_write(share):
            raise ShareError(f"share {share!r} is read-only")
        p = shares.resolve(share, relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        sha = _journal_for(share).commit(
            message=f"file.write {share}/{relpath}",
            author_agent=agent_name,
            paths=[p],
        )
        undo_log.record(UndoEntry(
            agent=agent_name, tool="file.write",
            reverse_kind="git-revert",
            reverse_payload={"share": share, "sha": sha},
        ))
        return f"wrote {len(content)} bytes to {share}/{relpath} (commit {sha[:7]})"

    # ---- DELETE ----
    def file_delete(share: str, relpath: str) -> str:
        if not shares.can_write(share):
            raise ShareError(f"share {share!r} is read-only")
        p = shares.resolve(share, relpath)
        if not p.exists():
            raise ShareError(f"{relpath}: not found in share {share!r}")
        p.unlink()
        sha = _journal_for(share).commit(
            message=f"file.delete {share}/{relpath}",
            author_agent=agent_name,
            paths=[p],
        )
        undo_log.record(UndoEntry(
            agent=agent_name, tool="file.delete",
            reverse_kind="git-revert",
            reverse_payload={"share": share, "sha": sha},
        ))
        return f"deleted {share}/{relpath} (commit {sha[:7]})"

    return {
        "file.read": Tool(
            name="file.read",
            description="Read a UTF-8 text file from inside a share.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                },
                "required": ["share", "relpath"],
            },
            impl=file_read,
        ),
        "file.list": Tool(
            name="file.list",
            description="List entries in a directory inside a share.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string", "default": "."},
                },
                "required": ["share"],
            },
            impl=file_list,
        ),
        "file.write": Tool(
            name="file.write",
            description="Write a UTF-8 text file inside a share. Overwrites if it exists.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["share", "relpath", "content"],
            },
            impl=file_write,
        ),
        "file.delete": Tool(
            name="file.delete",
            description="Delete a file inside a share. Destructive — requires approval.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                },
                "required": ["share", "relpath"],
            },
            impl=file_delete,
        ),
    }


# Tier mapping for these tools — consumed by PermissionEngine
from kc_sandbox.permissions import Tier

DEFAULT_FILE_TOOL_TIERS: dict[str, Tier] = {
    "file.read":   Tier.SAFE,
    "file.list":   Tier.SAFE,
    "file.write":  Tier.MUTATING,
    "file.delete": Tier.DESTRUCTIVE,
}
```

- [ ] **Step 4: Run test to verify it passes**

`pytest tests/test_tools.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kc_sandbox/tools.py tests/test_tools.py
git commit -m "feat(kc-sandbox): add sandboxed file.* tools with journal + undo"
```

---

## Task 7: Wiring — `build_sandboxed_agent()`

**Files:**
- Create: `src/kc_sandbox/wiring.py`
- Create: `tests/fixtures/agents/filebot.yaml`
- Test: `tests/test_wiring.py`

**Why:** Each consumer (the future CLI in Task 8, the future supervisor in kc-supervisor) needs a one-call helper that takes config + paths and returns a fully-wired kc-core `Agent` with sandboxed file tools and the permission hook installed. `wiring.py` is that helper. It exists so we don't repeat the boilerplate in every entry point.

- [ ] **Step 1: Create the fixture**

```yaml
# tests/fixtures/agents/filebot.yaml
name: filebot
model: gemma3:4b
system_prompt: |
  You are filebot. You can read, list, write, and delete files inside the user's
  shares using file.read, file.list, file.write, file.delete. Always use a share
  name and a relative path.
shares: [research]
tools: ["file.*"]
permission_overrides: {}
spawn_policy: persistent
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_wiring.py
from pathlib import Path
import pytest
from kc_core.ollama_client import ChatResponse
from kc_sandbox.permissions import AlwaysAllow
from kc_sandbox.wiring import build_sandboxed_agent


@pytest.mark.asyncio
async def test_build_sandboxed_agent_runs_a_write(tmp_path, fake_ollama):
    research = tmp_path / "research"; research.mkdir()
    shares_yaml = tmp_path / "shares.yaml"
    shares_yaml.write_text(f"shares:\n  - name: research\n    path: {research}\n    mode: read-write\n")

    client = fake_ollama(
        ChatResponse(
            text="",
            tool_calls=[{
                "id": "c1",
                "name": "file.write",
                "arguments": {"share": "research", "relpath": "hello.md", "content": "hi\n"},
            }],
            finish_reason="tool_calls",
        ),
        ChatResponse(text="wrote it", finish_reason="stop"),
    )
    agent = build_sandboxed_agent(
        agent_yaml=Path(__file__).parent / "fixtures" / "agents" / "filebot.yaml",
        shares_yaml=shares_yaml,
        undo_db=tmp_path / "u.db",
        client=client,
        approval_callback=AlwaysAllow(),
    )
    reply = await agent.send("create hello.md saying 'hi'")
    assert "wrote it" in reply.content
    assert (research / "hello.md").read_text() == "hi\n"
```

- [ ] **Step 3: Run test to verify it fails**

`pytest tests/test_wiring.py -v`
Expected: FAIL — module missing. (Also requires the `fake_ollama` fixture; copy/include it in `tests/conftest.py` — see Step 4.)

- [ ] **Step 4: Add `tests/conftest.py` mirroring kc-core's fake**

```python
# tests/conftest.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterator
import pytest
from kc_core.ollama_client import ChatResponse


@dataclass
class FakeOllamaClient:
    responses: list[ChatResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _iter: Iterator[ChatResponse] | None = None
    model: str = "fake-model"

    def __post_init__(self) -> None:
        self._iter = iter(self.responses)

    async def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        try:
            assert self._iter is not None
            return next(self._iter)
        except StopIteration:
            raise AssertionError("FakeOllamaClient out of scripted responses")


@pytest.fixture
def fake_ollama():
    def _make(*responses: ChatResponse) -> FakeOllamaClient:
        return FakeOllamaClient(responses=list(responses))
    return _make
```

- [ ] **Step 5: Implement wiring.py**

```python
# src/kc_sandbox/wiring.py
from __future__ import annotations
from pathlib import Path
from typing import Any
from kc_core.agent import Agent
from kc_core.config import load_agent_config
from kc_core.tools import ToolRegistry
from kc_sandbox.shares import SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog
from kc_sandbox.tools import build_file_tools, DEFAULT_FILE_TOOL_TIERS
from kc_sandbox.permissions import PermissionEngine, Tier, ApprovalCallback


def build_sandboxed_agent(
    *,
    agent_yaml: Path,
    shares_yaml: Path,
    undo_db: Path,
    client: Any,
    approval_callback: ApprovalCallback,
    default_model: str | None = None,
) -> Agent:
    cfg = load_agent_config(agent_yaml, default_model=default_model or "gemma3:4b")
    shares = SharesRegistry.from_yaml(shares_yaml)

    # Init a journal for every share + a single undo log
    journals = {name: Journal(shares.get(name).path) for name in shares.names()}
    for j in journals.values():
        j.init()

    log = UndoLog(undo_db); log.init()

    # Build tool set + register
    file_tools = build_file_tools(shares=shares, journals=journals, undo_log=log, agent_name=cfg.name)
    registry = ToolRegistry()
    for t in file_tools.values():
        registry.register(t)

    # Permission engine with default tier map; agent-config overrides ignored in v1
    engine = PermissionEngine(
        tier_map=dict(DEFAULT_FILE_TOOL_TIERS),
        agent_overrides={},
        approval_callback=approval_callback,
    )

    return Agent(
        name=cfg.name,
        client=client,
        system_prompt=cfg.system_prompt,
        tools=registry,
        permission_check=engine.to_agent_callback(cfg.name),
    )
```

- [ ] **Step 6: Run test to verify it passes**

`pytest tests/test_wiring.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full kc-sandbox test suite**

`pytest tests/ -v`
Expected: PASS — every kc-sandbox test green (~30 tests across 6 files).

- [ ] **Step 8: Commit**

```bash
git add src/kc_sandbox/wiring.py tests/test_wiring.py tests/conftest.py tests/fixtures/agents/filebot.yaml
git commit -m "feat(kc-sandbox): add build_sandboxed_agent() wiring helper"
```

---

## Task 8: SMOKE.md and README Polish

**Files:**
- Create: `SMOKE.md`
- Modify: `README.md`

- [ ] **Step 1: Write SMOKE.md**

```markdown
# kc-sandbox — Smoke Checklist

Run by hand on the target machine after `pip install -e ../kc-core ".[dev]"`.

## Automated tests

- [ ] In `kc-core`: `pytest tests/ --ignore=tests/live` — all green (incl. 2 new permission_check tests).
- [ ] In `kc-sandbox`: `pytest tests/` — all green.

## End-to-end (Python REPL)

```python
from pathlib import Path
import asyncio, tempfile
from kc_core.ollama_client import OllamaClient
from kc_sandbox.permissions import AlwaysAllow
from kc_sandbox.wiring import build_sandboxed_agent

tmp = Path(tempfile.mkdtemp())
research = tmp / "research"; research.mkdir()
(tmp / "shares.yaml").write_text(f"shares:\n  - name: research\n    path: {research}\n    mode: read-write\n")

agent = build_sandboxed_agent(
    agent_yaml=Path("tests/fixtures/agents/filebot.yaml"),
    shares_yaml=tmp / "shares.yaml",
    undo_db=tmp / "undo.db",
    client=OllamaClient(model="gemma3:4b"),
    approval_callback=AlwaysAllow(),
)

reply = asyncio.run(agent.send(
    "create research/hello.md with the text 'hi from kc-sandbox', then list the share"
))
print(reply.content)
```

- [ ] The model writes the file. `cat <tmp>/research/hello.md` shows the expected content.
- [ ] `git --git-dir=<tmp>/research/.kc-journal log --oneline` shows the init commit + the file.write commit.

## Undo round-trip

- [ ] After the write above, run:

```python
from kc_sandbox.undo import UndoLog, Undoer
from kc_sandbox.journal import Journal

log = UndoLog(tmp / "undo.db")
journals = {"research": Journal(research)}
Undoer(journals=journals, log=log).undo(entry_id=1)
```

- [ ] `<tmp>/research/hello.md` is gone.
- [ ] `git --git-dir=<tmp>/research/.kc-journal log --oneline` shows a new "Revert" commit on top.

## Negative cases

- [ ] An agent call asking for `share="research", relpath="../etc/passwd"` returns a tool error containing `escapes share`, the agent loop continues, no file is touched outside the share.
- [ ] An agent call to `file.write` against a read-only share returns a tool error containing `read-only`.
- [ ] An agent call to `file.delete` with `approval_callback=AlwaysDeny()` results in a "Denied" tool result; no file is removed; no undo entry is created.
```

- [ ] **Step 2: Polish README**

```markdown
# kc-sandbox

KonaClaw sandbox — sub-project 2 of 8. Provides:

- Named "shares" (allowlisted folders) with traversal-safe path resolution
- A tiered permission engine (Safe / Mutating / Destructive) with per-agent overrides
- Sandboxed `file.*` tools (`read`, `list`, `write`, `delete`)
- A per-share git journal so every write is a commit
- An undo log + `Undoer` that can revert any journaled change

Depends on `kc-core` (sub-project 1). See the umbrella spec at
`../docs/superpowers/specs/2026-05-02-konaclaw-design.md`.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core
    pip install -e ".[dev]"

## Test

    pytest tests/ -v

See `SMOKE.md` for the manual end-to-end walkthrough.

## Public surface

- `kc_sandbox.shares.SharesRegistry`
- `kc_sandbox.permissions.PermissionEngine`, `Tier`, `AlwaysAllow`, `AlwaysDeny`
- `kc_sandbox.journal.Journal`
- `kc_sandbox.undo.UndoLog`, `UndoEntry`, `Undoer`
- `kc_sandbox.tools.build_file_tools`, `DEFAULT_FILE_TOOL_TIERS`
- `kc_sandbox.wiring.build_sandboxed_agent` — the one-call helper for entry points
```

- [ ] **Step 3: Run all tests one more time**

In `kc-core/`: `pytest tests/ --ignore=tests/live -v`
In `kc-sandbox/`: `pytest tests/ -v`
Expected: all green in both.

- [ ] **Step 4: Commit**

```bash
git add SMOKE.md README.md
git commit -m "docs(kc-sandbox): add SMOKE.md and polish README"
```

---

## Done Criteria

When all 9 tasks (Task 0 through 8) are committed:

- `kc-core` has the `permission_check` hook on `Agent`, all kc-core tests still green.
- `kc-sandbox` test suite green (~30 tests across 7 files).
- `build_sandboxed_agent()` returns a kc-core agent with `file.read/list/write/delete` tools, all writes journaled as git commits in `<share>/.kc-journal/`, all destructive actions gated by the supplied `ApprovalCallback`, all journaled actions undoable via `Undoer.undo(entry_id)`.
- `SMOKE.md` end-to-end walkthrough passes by hand on the target machine with a real Ollama running.

This unblocks **kc-supervisor** (sub-project 3): it will wrap `build_sandboxed_agent()` behind a FastAPI service, persist conversations in SQLite, and expose the audit + undo log over HTTP/WebSocket.
