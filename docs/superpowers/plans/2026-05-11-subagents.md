# KonaClaw Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ephemeral subagent spawning for Kona-AI: a new `kc-subagents` package whose `EphemeralInstance` wraps a freshly-`assemble_agent`'d real `AssembledAgent`, two new Kona-only tools (`spawn_subagent`, `await_subagents`), YAML templates at `~/KonaClaw/subagent-templates/` with full surface (model + prompt + tools + MCP + memory + shares + permission overrides + lifecycle caps), inline trace WS frames in the parent's chat, attributed approval cards, dashboard tab 09, and four seed templates (`web-researcher`, `coder`, `email-drafter`, `scheduler`).

**Architecture:** New monorepo-sibling package `kc-subagents` (parallel to `kc-skills`, `kc-terminal`, `kc-web`). `SubagentIndex` mirrors `SkillIndex` (mtime-invalidated YAML loader). `EphemeralInstance` builds a real `AssembledAgent` via `template_to_agent_config()` → `assemble_agent()`, owns timeout/stop/attribution via contextvars, emits WS trace frames to the parent's `/ws/chat/{conversation_id}`, and resolves a result future for `await_subagents` joins. Two new audit columns + a `subagent_runs` table track per-instance lifecycle. `ApprovalBroker.Request` gains attribution fields so approval cards render `"<template> (ep_..., child of Kona-AI)"`. Gated by `KC_SUBAGENTS_ENABLED` (default `false`), same rollout pattern as terminal/web.

**Tech Stack:** Python 3.11+, PyYAML, SQLite (existing `kc-sandbox` audit DB), `asyncio.wait_for` for timeout, `contextvars.ContextVar` for attribution, `pytest` + `pytest-asyncio`, React + TypeScript + Vitest for dashboard.

**Spec:** `docs/superpowers/specs/2026-05-11-subagents-design.md` (commit `4c1466a`).

---

## File Structure

**New package:**

```
kc-subagents/
  pyproject.toml
  src/kc_subagents/
    __init__.py
    templates.py          # SubagentTemplate dataclass + YAML loader + SubagentIndex
    runner.py             # EphemeralInstance + SubagentRunner registry
    trace.py              # WS frame shapes + per-conversation buffer
    tools.py              # build_subagent_tools(): spawn_subagent + await_subagents
  tests/
    test_templates.py
    test_runner.py
    test_trace.py
    test_tools.py
```

**Modified supervisor files:**

- `kc-supervisor/pyproject.toml` — add `kc-subagents` as a local editable dep.
- `kc-supervisor/src/kc_supervisor/main.py` — construct `SubagentIndex` + `SubagentRunner` when flag set; thread to registry.
- `kc-supervisor/src/kc_supervisor/agents.py` — `AgentRegistry.__init__` accepts `subagent_index` + `subagent_runner`; threads through to `assemble_agent`.
- `kc-supervisor/src/kc_supervisor/assembly.py` — accept the new kwargs; register `spawn_subagent` + `await_subagents` only on Kona; skip registration for ephemeral instances.
- `kc-supervisor/src/kc_supervisor/ws_routes.py` — handle inbound `subagent_stop` frames; emit buffered trace frames on reconnect.
- `kc-supervisor/src/kc_supervisor/http_routes.py` — add `/subagent-templates` CRUD + `/subagents/active` + `/subagents/{id}/stop`.
- `kc-supervisor/tests/test_assembly.py` — new test cases for tool registration gating.

**Modified audit/approval files:**

- `kc-sandbox/src/kc_sandbox/audit.py` (or equivalent) — schema migration: new nullable cols on `tool_calls`, new `subagent_runs` table; new helper `audit.start_subagent_run`, `audit.finish_subagent_run`.
- `kc-supervisor/src/kc_supervisor/approvals.py` — `ApprovalBroker.Request`/`Response` gain `parent_agent` + `subagent_id` optional fields; broker reads a contextvar at request time.

**New dashboard files:**

```
kc-dashboard/src/
  api/subagents.ts                       # typed wrapper around /subagent-templates + /subagents/active
  pages/SubagentsTab.tsx                 # tab 09 root
  components/SubagentTemplateCard.tsx    # one card in the grid
  components/SubagentTemplateEditor.tsx  # new/edit modal with full schema form
  components/SubagentActiveRunsPanel.tsx # live list of in-flight instances
  components/SubagentTraceBlock.tsx      # inline collapsible trace in chat transcript

kc-dashboard/tests/
  api/subagents.test.ts
  components/SubagentTemplateEditor.test.tsx
  components/SubagentTraceBlock.test.tsx
```

**Modified dashboard files:**

- `kc-dashboard/src/components/Sidebar.tsx` — add tab 09 "Subagents".
- `kc-dashboard/src/components/ChatTranscript.tsx` — handle `subagent_started`/`subagent_tool`/`subagent_approval`/`subagent_finished` frames; mount `SubagentTraceBlock`.
- `kc-dashboard/src/components/ApprovalCard.tsx` — render "via subagent" badge when attribution fields present.
- `kc-dashboard/src/lib/ws.ts` — type the new frame shapes in `ChatEvent` union.

**Seed templates installer:**

- `kc-subagents/src/kc_subagents/seeds/` — four bundled YAML files copied into `~/KonaClaw/subagent-templates/` on first startup with the flag enabled and an empty directory.

**SMOKE doc:**

- `docs/superpowers/specs/2026-05-11-subagents-SMOKE.md` — 9 manual gates from spec §14.4.

---

## Task 1: Bootstrap `kc-subagents` package

**Files:**
- Create: `kc-subagents/pyproject.toml`
- Create: `kc-subagents/src/kc_subagents/__init__.py`
- Create: `kc-subagents/tests/test_smoke.py`
- Modify: `kc-supervisor/pyproject.toml` (add local editable dep)

- [ ] **Step 1: Write the failing import test**

```python
# kc-subagents/tests/test_smoke.py
def test_package_imports():
    import kc_subagents
    assert kc_subagents.__name__ == "kc_subagents"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-subagents && python -m pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kc_subagents'`.

- [ ] **Step 3: Write `pyproject.toml` matching the kc-web shape**

```toml
# kc-subagents/pyproject.toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "kc-subagents"
version = "0.1.0"
description = "Ephemeral subagent spawning for KonaClaw"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 4: Create empty `__init__.py`**

```python
# kc-subagents/src/kc_subagents/__init__.py
"""Ephemeral subagent spawning for KonaClaw."""
__all__: list[str] = []
```

- [ ] **Step 5: Editable-install into the supervisor venv**

Run: `cd /Users/sammydallal/Desktop/claudeCode/SammyClaw && pip install -e kc-subagents/ --no-deps`
Expected: `Successfully installed kc-subagents-0.1.0`.

Then add to `kc-supervisor/pyproject.toml` under `[project] dependencies`:

```toml
"kc-subagents",
```

- [ ] **Step 6: Run smoke test to verify it passes**

Run: `cd /Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-subagents && python -m pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kc-subagents/pyproject.toml kc-subagents/src kc-subagents/tests kc-supervisor/pyproject.toml
git commit -m "feat(kc-subagents): bootstrap package skeleton (Subagents Task 1)"
```

---

## Task 2: `SubagentTemplate` dataclass + YAML loader (basic happy path)

**Files:**
- Create: `kc-subagents/src/kc_subagents/templates.py`
- Create: `kc-subagents/tests/test_templates.py`

- [ ] **Step 1: Write failing tests for the dataclass + loader**

```python
# kc-subagents/tests/test_templates.py
from pathlib import Path
import textwrap
import pytest
from kc_subagents.templates import SubagentTemplate, load_template_file

def write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(textwrap.dedent(body))
    return p

def test_load_minimal_template(tmp_path: Path):
    p = write(tmp_path, "web-researcher", """
        name: web-researcher
        model: claude-opus-4-7
        system_prompt: |
          You research things.
    """)
    t = load_template_file(p)
    assert isinstance(t, SubagentTemplate)
    assert t.name == "web-researcher"
    assert t.model == "claude-opus-4-7"
    assert t.system_prompt.strip() == "You research things."
    assert t.tools == {}
    assert t.mcp_servers == []
    assert t.timeout_seconds == 300
    assert t.max_tool_calls == 50

def test_load_full_template(tmp_path: Path):
    p = write(tmp_path, "coder", """
        name: coder
        description: A coding subagent.
        version: "1.0"
        model: claude-opus-4-7
        model_options:
          temperature: 0.2
        system_prompt: |
          You code.
        tools:
          terminal_run: {}
          skill_view: {}
        mcp_servers: [zapier]
        mcp_action_filter:
          zapier: [gmail_find_email]
        memory:
          mode: read-only
          scope: research/
        shares: [downloads-readable]
        permission_overrides:
          terminal_run: MUTATING
        timeout_seconds: 600
        max_tool_calls: 100
    """)
    t = load_template_file(p)
    assert t.tools == {"terminal_run": {}, "skill_view": {}}
    assert t.mcp_servers == ["zapier"]
    assert t.mcp_action_filter == {"zapier": ["gmail_find_email"]}
    assert t.memory == {"mode": "read-only", "scope": "research/"}
    assert t.shares == ["downloads-readable"]
    assert t.permission_overrides == {"terminal_run": "MUTATING"}
    assert t.timeout_seconds == 600
    assert t.max_tool_calls == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-subagents && python -m pytest tests/test_templates.py -v`
Expected: FAIL — module/dataclass not defined.

- [ ] **Step 3: Implement the dataclass + loader**

```python
# kc-subagents/src/kc_subagents/templates.py
from __future__ import annotations
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ALLOWED_KEYS = {
    "name", "description", "version",
    "model", "model_options",
    "system_prompt",
    "tools",
    "mcp_servers", "mcp_action_filter",
    "memory",
    "shares",
    "permission_overrides",
    "timeout_seconds", "max_tool_calls",
}

@dataclass
class SubagentTemplate:
    name: str
    model: str
    system_prompt: str
    description: str = ""
    version: str = ""
    model_options: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    mcp_action_filter: dict[str, list[str]] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    shares: list[str] = field(default_factory=list)
    permission_overrides: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300
    max_tool_calls: int = 50
    source_path: Path | None = None


class TemplateLoadError(ValueError):
    """Raised when a template YAML is malformed."""


def load_template_file(path: Path) -> SubagentTemplate:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise TemplateLoadError(f"yaml parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise TemplateLoadError(f"{path}: top-level must be a mapping")
    unknown = set(raw.keys()) - _ALLOWED_KEYS
    if unknown:
        raise TemplateLoadError(f"{path}: unknown keys {sorted(unknown)}")
    for required in ("name", "model", "system_prompt"):
        if required not in raw:
            raise TemplateLoadError(f"{path}: missing required key {required!r}")
    return SubagentTemplate(
        name=raw["name"],
        model=raw["model"],
        system_prompt=raw["system_prompt"],
        description=raw.get("description", ""),
        version=raw.get("version", ""),
        model_options=raw.get("model_options") or {},
        tools=raw.get("tools") or {},
        mcp_servers=raw.get("mcp_servers") or [],
        mcp_action_filter=raw.get("mcp_action_filter") or {},
        memory=raw.get("memory") or {},
        shares=raw.get("shares") or [],
        permission_overrides=raw.get("permission_overrides") or {},
        timeout_seconds=int(raw.get("timeout_seconds", 300)),
        max_tool_calls=int(raw.get("max_tool_calls", 50)),
        source_path=path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-subagents && python -m pytest tests/test_templates.py -v`
Expected: PASS for both happy-path tests.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/templates.py kc-subagents/tests/test_templates.py
git commit -m "feat(kc-subagents): SubagentTemplate dataclass + YAML loader (Subagents Task 2)"
```

---

## Task 3: Strict validation in the loader

**Files:**
- Modify: `kc-subagents/src/kc_subagents/templates.py`
- Modify: `kc-subagents/tests/test_templates.py`

- [ ] **Step 1: Add failing validation tests**

Append to `kc-subagents/tests/test_templates.py`:

```python
def test_bad_name_rejected(tmp_path):
    p = write(tmp_path, "weird", """
        name: WEB_Researcher
        model: m
        system_prompt: x
    """)
    with pytest.raises(TemplateLoadError, match="name must be lowercase-kebab"):
        load_template_file(p)

def test_name_mismatches_filename(tmp_path):
    p = write(tmp_path, "research-bot", """
        name: web-researcher
        model: m
        system_prompt: x
    """)
    with pytest.raises(TemplateLoadError, match="filename stem"):
        load_template_file(p)

def test_unknown_memory_mode(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        memory:
          mode: read-write
    """)
    with pytest.raises(TemplateLoadError, match="not yet supported"):
        load_template_file(p)

def test_timeout_clamp(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        timeout_seconds: 99999
    """)
    with pytest.raises(TemplateLoadError, match="timeout_seconds"):
        load_template_file(p)

def test_max_tool_calls_clamp(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        max_tool_calls: 9999
    """)
    with pytest.raises(TemplateLoadError, match="max_tool_calls"):
        load_template_file(p)

def test_memory_scope_escape_rejected(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        memory:
          mode: read-only
          scope: "../outside"
    """)
    with pytest.raises(TemplateLoadError, match="memory.scope"):
        load_template_file(p)
```

Update the existing `TemplateLoadError` import at the top of the test file if needed.

- [ ] **Step 2: Update the import line**

```python
from kc_subagents.templates import SubagentTemplate, load_template_file, TemplateLoadError
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `python -m pytest tests/test_templates.py -v`
Expected: 5 new tests FAIL.

- [ ] **Step 4: Add validation to the loader**

Replace the body of `load_template_file` (in `templates.py`) with validation logic inserted before the dataclass construction:

```python
import re

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MEMORY_MODES = {"none", "read-only"}  # read-write rejected per spec §10.3

def load_template_file(path: Path) -> SubagentTemplate:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise TemplateLoadError(f"yaml parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise TemplateLoadError(f"{path}: top-level must be a mapping")

    unknown = set(raw.keys()) - _ALLOWED_KEYS
    if unknown:
        raise TemplateLoadError(f"{path}: unknown keys {sorted(unknown)}")

    for required in ("name", "model", "system_prompt"):
        if required not in raw:
            raise TemplateLoadError(f"{path}: missing required key {required!r}")

    name = raw["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise TemplateLoadError(
            f"{path}: name must be lowercase-kebab, ≤64 chars (got {name!r})"
        )
    if path.stem != name:
        raise TemplateLoadError(
            f"{path}: name {name!r} does not match filename stem {path.stem!r}"
        )

    mem = raw.get("memory") or {}
    if mem:
        mode = mem.get("mode", "none")
        if mode not in _MEMORY_MODES:
            if mode == "read-write":
                raise TemplateLoadError(
                    f"{path}: memory.read-write is not yet supported; see spec §15"
                )
            raise TemplateLoadError(f"{path}: memory.mode must be one of {_MEMORY_MODES}")
        scope = mem.get("scope")
        if scope is not None:
            if not isinstance(scope, str) or scope.startswith("/") or ".." in Path(scope).parts:
                raise TemplateLoadError(f"{path}: memory.scope must be a relative path under memory_root")

    timeout = int(raw.get("timeout_seconds", 300))
    if not (10 <= timeout <= 1800):
        raise TemplateLoadError(f"{path}: timeout_seconds must be in [10, 1800]")

    max_calls = int(raw.get("max_tool_calls", 50))
    if not (1 <= max_calls <= 500):
        raise TemplateLoadError(f"{path}: max_tool_calls must be in [1, 500]")

    return SubagentTemplate(
        name=name,
        model=raw["model"],
        system_prompt=raw["system_prompt"],
        description=raw.get("description", ""),
        version=raw.get("version", ""),
        model_options=raw.get("model_options") or {},
        tools=raw.get("tools") or {},
        mcp_servers=raw.get("mcp_servers") or [],
        mcp_action_filter=raw.get("mcp_action_filter") or {},
        memory=mem,
        shares=raw.get("shares") or [],
        permission_overrides=raw.get("permission_overrides") or {},
        timeout_seconds=timeout,
        max_tool_calls=max_calls,
        source_path=path,
    )
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `python -m pytest tests/test_templates.py -v`
Expected: ALL pass (original 2 + new 6).

- [ ] **Step 6: Commit**

```bash
git add kc-subagents/src/kc_subagents/templates.py kc-subagents/tests/test_templates.py
git commit -m "feat(kc-subagents): strict template validation at load time (Subagents Task 3)"
```

---

## Task 4: `SubagentIndex` with mtime invalidation

**Files:**
- Modify: `kc-subagents/src/kc_subagents/templates.py`
- Modify: `kc-subagents/tests/test_templates.py`

- [ ] **Step 1: Add failing index tests**

Append:

```python
import threading, time
from kc_subagents.templates import SubagentIndex

def test_index_lists_templates(tmp_path):
    write(tmp_path, "web-researcher", "name: web-researcher\nmodel: m\nsystem_prompt: x")
    write(tmp_path, "coder",          "name: coder\nmodel: m\nsystem_prompt: x")
    idx = SubagentIndex(tmp_path)
    assert sorted(idx.names()) == ["coder", "web-researcher"]
    assert idx.get("coder").name == "coder"

def test_index_unknown_returns_none(tmp_path):
    idx = SubagentIndex(tmp_path)
    assert idx.get("missing") is None

def test_index_degraded_surfaces_error(tmp_path):
    (tmp_path / "bad.yaml").write_text("name: bad\nmodel: m\nsystem_prompt: x\nunknown_key: 1")
    idx = SubagentIndex(tmp_path)
    degraded = idx.degraded()
    assert "bad" in degraded
    assert "unknown keys" in degraded["bad"]

def test_index_reloads_on_mtime_change(tmp_path):
    p = write(tmp_path, "x", "name: x\nmodel: m1\nsystem_prompt: a")
    idx = SubagentIndex(tmp_path)
    assert idx.get("x").model == "m1"
    time.sleep(0.01)
    p.write_text("name: x\nmodel: m2\nsystem_prompt: a")
    # Bump mtime explicitly to defeat coarse-grained filesystem mtimes.
    new_mtime = p.stat().st_mtime + 1
    import os; os.utime(p, (new_mtime, new_mtime))
    assert idx.get("x").model == "m2"

def test_index_thread_safe(tmp_path):
    write(tmp_path, "x", "name: x\nmodel: m\nsystem_prompt: a")
    idx = SubagentIndex(tmp_path)
    errors = []
    def hammer():
        for _ in range(100):
            try:
                idx.get("x")
                idx.names()
            except Exception as e:
                errors.append(e)
    ts = [threading.Thread(target=hammer) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_templates.py -v -k index`
Expected: FAIL — `SubagentIndex` not defined.

- [ ] **Step 3: Implement `SubagentIndex`**

Append to `templates.py`:

```python
import threading
from typing import Iterable

class SubagentIndex:
    """In-memory cache of templates loaded from a directory.

    Mtime-invalidated per file; lock-guarded against concurrent readers.
    Mirrors the SkillIndex pattern from kc-skills.
    """

    def __init__(self, templates_dir: Path) -> None:
        self._dir = Path(templates_dir)
        self._lock = threading.Lock()
        # name -> (template, mtime). Degraded entries store None template + error in _errors.
        self._cache: dict[str, tuple[SubagentTemplate, float]] = {}
        self._errors: dict[str, str] = {}
        self._dir_mtime: float = 0.0
        self._refresh_if_changed()

    def _refresh_if_changed(self) -> None:
        with self._lock:
            if not self._dir.exists():
                self._cache.clear()
                self._errors.clear()
                self._dir_mtime = 0.0
                return
            current_dir_mtime = self._dir.stat().st_mtime
            paths = sorted(self._dir.glob("*.yaml"))
            seen: set[str] = set()
            for p in paths:
                stem = p.stem
                seen.add(stem)
                mtime = p.stat().st_mtime
                cached = self._cache.get(stem)
                if cached and cached[1] == mtime and stem not in self._errors:
                    continue
                try:
                    t = load_template_file(p)
                    self._cache[stem] = (t, mtime)
                    self._errors.pop(stem, None)
                except TemplateLoadError as e:
                    self._cache.pop(stem, None)
                    self._errors[stem] = str(e)
            # Drop entries whose files vanished.
            for stale in set(self._cache.keys()) - seen:
                self._cache.pop(stale, None)
            for stale in set(self._errors.keys()) - seen:
                self._errors.pop(stale, None)
            self._dir_mtime = current_dir_mtime

    def names(self) -> list[str]:
        self._refresh_if_changed()
        with self._lock:
            return sorted(self._cache.keys())

    def get(self, name: str) -> SubagentTemplate | None:
        self._refresh_if_changed()
        with self._lock:
            entry = self._cache.get(name)
            return entry[0] if entry else None

    def degraded(self) -> dict[str, str]:
        self._refresh_if_changed()
        with self._lock:
            return dict(self._errors)

    def reload(self) -> None:
        """Force a full re-scan, e.g. after a dashboard write."""
        with self._lock:
            self._cache.clear()
            self._errors.clear()
            self._dir_mtime = 0.0
        self._refresh_if_changed()
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest tests/test_templates.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/templates.py kc-subagents/tests/test_templates.py
git commit -m "feat(kc-subagents): SubagentIndex with mtime invalidation (Subagents Task 4)"
```

---

## Task 5: Audit schema — `subagent_runs` table + attribution columns

**Files:**
- Modify: the audit-DB schema constant in `kc-sandbox` (search for `CREATE TABLE tool_calls` to locate it).
- Add tests under `kc-sandbox/tests/test_audit_subagents.py`.

- [ ] **Step 1: Locate the audit schema**

Run: `grep -rn "CREATE TABLE tool_calls" kc-sandbox/`
Note the file path and the SCHEMA constant. Hereafter referred to as `<audit_module>`.

- [ ] **Step 2: Write failing migration test**

```python
# kc-sandbox/tests/test_audit_subagents.py
from pathlib import Path
from kc_sandbox.<audit_module> import Storage   # replace <audit_module> with the actual filename

def test_subagent_runs_table_exists(tmp_path: Path):
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init_schema()
    cur = s.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_runs'"
    )
    assert cur.fetchone() is not None

def test_tool_calls_has_attribution_cols(tmp_path):
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init_schema()
    cols = {row[1] for row in s.conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    assert {"parent_agent", "subagent_id", "subagent_template"} <= cols

def test_start_finish_subagent_run(tmp_path):
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init_schema()
    s.start_subagent_run(
        id="ep_abc123",
        parent_conversation_id="conv_1",
        parent_agent="Kona-AI",
        template="web-researcher",
        label="berlin",
        task_preview="weather",
        context_keys=["recent"],
    )
    s.finish_subagent_run(
        id="ep_abc123",
        status="ok",
        duration_ms=1000,
        tool_calls_used=3,
        reply_chars=200,
        error_message=None,
    )
    row = s.conn.execute(
        "SELECT status, duration_ms, tool_calls_used FROM subagent_runs WHERE id=?",
        ("ep_abc123",),
    ).fetchone()
    assert row == ("ok", 1000, 3)

def test_reap_running_on_startup(tmp_path):
    db = tmp_path / "audit.sqlite"
    s = Storage(db_path=db)
    s.init_schema()
    s.start_subagent_run(
        id="ep_zombie", parent_conversation_id="c", parent_agent="Kona-AI",
        template="x", label=None, task_preview=None, context_keys=None,
    )
    # Simulate restart: new Storage instance.
    s2 = Storage(db_path=db)
    s2.init_schema()
    s2.reap_running_subagent_runs()
    row = s2.conn.execute(
        "SELECT status, error_message FROM subagent_runs WHERE id=?", ("ep_zombie",),
    ).fetchone()
    assert row[0] == "interrupted"
    assert "restart" in (row[1] or "").lower()
```

- [ ] **Step 3: Run tests to verify failure**

Run: `python -m pytest kc-sandbox/tests/test_audit_subagents.py -v`
Expected: FAIL — table/methods missing.

- [ ] **Step 4: Add schema + methods**

In the audit module, extend the `SCHEMA` constant (idempotent via `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE … ADD COLUMN` guarded by `PRAGMA table_info`):

```python
SUBAGENT_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS subagent_runs (
  id                     TEXT PRIMARY KEY,
  parent_conversation_id TEXT NOT NULL,
  parent_agent           TEXT NOT NULL,
  template               TEXT NOT NULL,
  label                  TEXT,
  task_preview           TEXT,
  context_keys           TEXT,
  started_ts             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_ts               TIMESTAMP,
  status                 TEXT NOT NULL DEFAULT 'running',
  duration_ms            INTEGER,
  tool_calls_used        INTEGER NOT NULL DEFAULT 0,
  reply_chars            INTEGER,
  error_message          TEXT
);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_parent
  ON subagent_runs(parent_conversation_id, started_ts DESC);
CREATE INDEX IF NOT EXISTS idx_subagent_runs_template
  ON subagent_runs(template, started_ts DESC);
"""

def _ensure_tool_calls_attribution_cols(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    for col in ("parent_agent", "subagent_id", "subagent_template"):
        if col not in existing:
            conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {col} TEXT")
```

Wire `_ensure_tool_calls_attribution_cols(self.conn)` and `self.conn.executescript(SUBAGENT_RUNS_SCHEMA)` into `Storage.init_schema()`.

Add the helper methods on `Storage`:

```python
import json
from datetime import datetime, timezone

def start_subagent_run(self, *, id, parent_conversation_id, parent_agent, template,
                       label, task_preview, context_keys):
    self.conn.execute(
        """INSERT INTO subagent_runs
           (id, parent_conversation_id, parent_agent, template, label, task_preview, context_keys)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (id, parent_conversation_id, parent_agent, template, label,
         (task_preview or "")[:200],
         json.dumps(context_keys) if context_keys else None),
    )
    self.conn.commit()

def finish_subagent_run(self, *, id, status, duration_ms, tool_calls_used,
                        reply_chars, error_message):
    self.conn.execute(
        """UPDATE subagent_runs
           SET ended_ts=CURRENT_TIMESTAMP, status=?, duration_ms=?, tool_calls_used=?,
               reply_chars=?, error_message=?
           WHERE id=?""",
        (status, duration_ms, tool_calls_used, reply_chars, error_message, id),
    )
    self.conn.commit()

def reap_running_subagent_runs(self) -> int:
    cur = self.conn.execute(
        """UPDATE subagent_runs
           SET status='interrupted',
               ended_ts=CURRENT_TIMESTAMP,
               error_message='supervisor restarted mid-run'
           WHERE status='running'"""
    )
    self.conn.commit()
    return cur.rowcount
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest kc-sandbox/tests/test_audit_subagents.py -v`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add kc-sandbox/ -p   # stage just the audit changes
git commit -m "feat(kc-sandbox): subagent_runs table + tool_calls attribution cols (Subagents Task 5)"
```

---

## Task 6: `ApprovalBroker` attribution fields

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/approvals.py`
- Modify: `kc-supervisor/tests/test_approvals.py` (or create if absent)

- [ ] **Step 1: Locate the broker**

Run: `grep -n "class ApprovalBroker" kc-supervisor/src/kc_supervisor/approvals.py`

- [ ] **Step 2: Write a failing test**

Append to `kc-supervisor/tests/test_approvals.py`:

```python
import asyncio, contextvars
from kc_supervisor.approvals import ApprovalBroker, subagent_attribution_var

def test_request_picks_up_attribution_from_contextvar():
    broker = ApprovalBroker()
    captured = {}

    async def consumer():
        async for req in broker.stream():
            captured["parent_agent"] = req.parent_agent
            captured["subagent_id"]  = req.subagent_id
            broker.respond(req.id, decision="allow")
            return

    async def producer():
        subagent_attribution_var.set({"parent_agent": "Kona-AI", "subagent_id": "ep_x"})
        await broker.request(tool="terminal_run", args={}, tier="MUTATING", agent="coder")

    async def main():
        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        await producer()
        await consumer_task

    asyncio.run(main())
    assert captured["parent_agent"] == "Kona-AI"
    assert captured["subagent_id"]  == "ep_x"
```

(Adapt method names — `stream`, `request`, `respond` — to what the existing broker actually exposes.)

- [ ] **Step 3: Run test to verify failure**

Run: `python -m pytest kc-supervisor/tests/test_approvals.py -v -k attribution`
Expected: FAIL — `subagent_attribution_var` not defined.

- [ ] **Step 4: Add the contextvar + field**

In `approvals.py`:

```python
import contextvars
subagent_attribution_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "kc_supervisor_subagent_attribution", default=None,
)
```

Extend the `Request` dataclass with two new optional fields:

```python
@dataclass
class Request:
    # ... existing fields ...
    parent_agent: str | None = None
    subagent_id: str | None = None
```

And in the `request(...)` method, before constructing the `Request`, read the contextvar:

```python
attrib = subagent_attribution_var.get()
parent_agent = attrib.get("parent_agent") if attrib else None
subagent_id  = attrib.get("subagent_id")  if attrib else None
req = Request(..., parent_agent=parent_agent, subagent_id=subagent_id)
```

When serializing the `approval_request` WS frame, include `parent_agent` and `subagent_id` fields.

- [ ] **Step 5: Run test to verify pass**

Run: `python -m pytest kc-supervisor/tests/test_approvals.py -v`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/approvals.py kc-supervisor/tests/test_approvals.py
git commit -m "feat(kc-supervisor): ApprovalBroker subagent attribution via contextvar (Subagents Task 6)"
```

---

## Task 7: `template_to_agent_config` + `EphemeralInstance.spawn` (no run yet)

**Files:**
- Create: `kc-subagents/src/kc_subagents/runner.py`
- Create: `kc-subagents/tests/test_runner.py`

- [ ] **Step 1: Write a failing test for the translation function**

```python
# kc-subagents/tests/test_runner.py
from pathlib import Path
from kc_subagents.templates import SubagentTemplate
from kc_subagents.runner import template_to_agent_config

def test_template_to_agent_config_basic():
    t = SubagentTemplate(
        name="web-researcher", model="claude-opus-4-7",
        system_prompt="research things",
        tools={"web_search": {"budget": 20}, "skill_view": {}},
        timeout_seconds=300, max_tool_calls=30,
        source_path=Path("/tmp/web-researcher.yaml"),
    )
    cfg = template_to_agent_config(t, instance_id="ep_abc123", parent_agent="Kona-AI")
    assert cfg.name == "Kona-AI/ep_abc123/web-researcher"
    assert cfg.model == "claude-opus-4-7"
    assert cfg.system_prompt == "research things"
    # Tool whitelist is a list of names; per-tool config is carried in cfg.tool_config.
    assert set(cfg.tool_whitelist) == {"web_search", "skill_view"}
    assert cfg.tool_config == {"web_search": {"budget": 20}, "skill_view": {}}
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: FAIL — module/function missing.

- [ ] **Step 3: Implement `template_to_agent_config`**

```python
# kc-subagents/src/kc_subagents/runner.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from kc_subagents.templates import SubagentTemplate

@dataclass
class EphemeralAgentConfig:
    """Subset of fields the supervisor's assemble_agent consumes for an ephemeral run.

    Mapped one-to-one to AgentConfig at the supervisor seam (assembly.py reads
    these fields). Keeping a separate dataclass avoids a cross-package import of
    AgentConfig into kc-subagents and lets us add ephemeral-only fields later.
    """
    name: str
    model: str
    system_prompt: str
    tool_whitelist: list[str] = field(default_factory=list)
    tool_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    mcp_action_filter: dict[str, list[str]] = field(default_factory=dict)
    memory_mode: str = "none"
    memory_scope: str | None = None
    shares: list[str] = field(default_factory=list)
    permission_overrides: dict[str, str] = field(default_factory=dict)
    model_options: dict[str, Any] = field(default_factory=dict)

def template_to_agent_config(
    t: SubagentTemplate, *, instance_id: str, parent_agent: str
) -> EphemeralAgentConfig:
    return EphemeralAgentConfig(
        name=f"{parent_agent}/{instance_id}/{t.name}",
        model=t.model,
        system_prompt=t.system_prompt,
        tool_whitelist=list(t.tools.keys()),
        tool_config=dict(t.tools),
        mcp_servers=list(t.mcp_servers),
        mcp_action_filter=dict(t.mcp_action_filter),
        memory_mode=t.memory.get("mode", "none"),
        memory_scope=t.memory.get("scope"),
        shares=list(t.shares),
        permission_overrides=dict(t.permission_overrides),
        model_options=dict(t.model_options),
    )
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/runner.py kc-subagents/tests/test_runner.py
git commit -m "feat(kc-subagents): template_to_agent_config (Subagents Task 7)"
```

---

## Task 8: `EphemeralInstance.run` — happy path with injected agent

**Files:**
- Modify: `kc-subagents/src/kc_subagents/runner.py`
- Modify: `kc-subagents/tests/test_runner.py`

- [ ] **Step 1: Add failing test using a fake agent**

Append to `tests/test_runner.py`:

```python
import asyncio, pytest
from kc_subagents.runner import EphemeralInstance, SubagentRunner, InstanceResult
from kc_subagents.templates import SubagentTemplate
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

class FakeAssembledAgent:
    def __init__(self, reply_text="hello world"):
        self._reply = reply_text
        self.core_agent = MagicMock()
        async def send(message):
            return MagicMock(content=self._reply)
        self.core_agent.send = send
        self.core_agent.history = []

@pytest.mark.asyncio
async def test_instance_run_ok_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))
    fake = FakeAssembledAgent("answer text")
    emitted = []
    inst = EphemeralInstance(
        instance_id="ep_a",
        template=t,
        parent_agent="Kona-AI",
        parent_conversation_id="conv_1",
        task="do thing",
        context=None,
        label="t1",
        effective_timeout=10,
        assembled=fake,
        on_frame=emitted.append,
        audit_start=lambda **kw: None,
        audit_finish=lambda **kw: None,
    )
    result: InstanceResult = await inst.run()
    assert result.status == "ok"
    assert result.reply == "answer text"
    assert any(f["type"] == "subagent_started"  for f in emitted)
    assert any(f["type"] == "subagent_finished" for f in emitted)

@pytest.mark.asyncio
async def test_runner_spawn_and_get_future():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))
    fake = FakeAssembledAgent("done")
    runner = SubagentRunner(
        build_assembled=lambda cfg: fake,
        audit_start=lambda **kw: None,
        audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="go", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    assert handle.startswith("ep_")
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "ok"
    assert result.reply == "done"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: FAIL — `EphemeralInstance`, `SubagentRunner`, `InstanceResult` missing.

- [ ] **Step 3: Implement the runner**

Append to `runner.py`:

```python
import asyncio, secrets, time
from dataclasses import dataclass
from typing import Callable, Optional

@dataclass
class InstanceResult:
    subagent_id: str
    status: str            # ok | error | timeout | stopped
    reply: str | None
    duration_ms: int
    tool_calls_used: int
    error: str | None = None

def _gen_id() -> str:
    return "ep_" + secrets.token_hex(3)  # 6 hex chars

class EphemeralInstance:
    def __init__(
        self, *,
        instance_id: str,
        template: SubagentTemplate,
        parent_agent: str,
        parent_conversation_id: str,
        task: str,
        context: dict | None,
        label: str | None,
        effective_timeout: int,
        assembled,                                 # AssembledAgent or fake
        on_frame: Callable[[dict], None],
        audit_start: Callable[..., None],
        audit_finish: Callable[..., None],
    ):
        self.id = instance_id
        self.template = template
        self.parent_agent = parent_agent
        self.parent_conversation_id = parent_conversation_id
        self.task = task
        self.context = context
        self.label = label
        self.effective_timeout = effective_timeout
        self.assembled = assembled
        self._on_frame = on_frame
        self._audit_start = audit_start
        self._audit_finish = audit_finish
        self._task: asyncio.Task | None = None
        self._result_future: asyncio.Future[InstanceResult] = (
            asyncio.get_event_loop().create_future()
        )
        self.tool_calls_used = 0

    def _emit(self, frame: dict) -> None:
        self._on_frame({**frame, "subagent_id": self.id})

    def _compose_message(self) -> str:
        if not self.context:
            return self.task
        import json as _json
        return f"{self.task}\n\n## Context\n```json\n{_json.dumps(self.context, indent=2)}\n```"

    async def run(self) -> InstanceResult:
        started = time.monotonic()
        self._audit_start(
            id=self.id, parent_conversation_id=self.parent_conversation_id,
            parent_agent=self.parent_agent, template=self.template.name,
            label=self.label, task_preview=self.task,
            context_keys=list(self.context.keys()) if self.context else None,
        )
        self._emit({
            "type": "subagent_started",
            "template": self.template.name,
            "label": self.label,
            "task_preview": self.task[:200],
        })
        reply: str | None = None
        status = "ok"
        error: str | None = None
        try:
            assistant = await asyncio.wait_for(
                self.assembled.core_agent.send(self._compose_message()),
                timeout=self.effective_timeout,
            )
            reply = (getattr(assistant, "content", "") or "").strip()
        except asyncio.TimeoutError:
            status = "timeout"
            error  = f"timed out after {self.effective_timeout}s"
        except asyncio.CancelledError:
            status = "stopped"
            error  = "stopped by user"
            raise_after = True
        except Exception as e:                       # noqa: BLE001
            status = "error"
            error  = str(e)
        else:
            raise_after = False
        duration_ms = int((time.monotonic() - started) * 1000)
        result = InstanceResult(
            subagent_id=self.id, status=status, reply=reply,
            duration_ms=duration_ms, tool_calls_used=self.tool_calls_used, error=error,
        )
        self._emit({
            "type": "subagent_finished",
            "status": status,
            "reply_preview": (reply or "")[:400],
            "duration_ms": duration_ms,
            "tool_calls_used": self.tool_calls_used,
            "error_message": error,
        })
        self._audit_finish(
            id=self.id, status=status, duration_ms=duration_ms,
            tool_calls_used=self.tool_calls_used,
            reply_chars=len(reply) if reply else 0, error_message=error,
        )
        if not self._result_future.done():
            self._result_future.set_result(result)
        if status == "stopped":
            # Propagate cancellation only after we've recorded the terminal state.
            raise asyncio.CancelledError()
        return result

    async def wait(self, ceiling_seconds: int | None = None) -> InstanceResult:
        if ceiling_seconds is None:
            return await self._result_future
        return await asyncio.wait_for(asyncio.shield(self._result_future), timeout=ceiling_seconds)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


class SubagentRunner:
    """Owns the in-flight registry. One per supervisor."""

    PER_CONV_CAP = 4
    GLOBAL_CAP   = 16

    def __init__(
        self, *,
        build_assembled: Callable[[EphemeralAgentConfig], object],
        audit_start: Callable[..., None],
        audit_finish: Callable[..., None],
        on_frame: Callable[[dict], None],
    ):
        self._build_assembled = build_assembled
        self._audit_start = audit_start
        self._audit_finish = audit_finish
        self._on_frame = on_frame
        self._instances: dict[str, EphemeralInstance] = {}

    def _count_in_flight(self, parent_conversation_id: str | None = None) -> int:
        if parent_conversation_id is None:
            return len(self._instances)
        return sum(
            1 for i in self._instances.values()
            if i.parent_conversation_id == parent_conversation_id
        )

    def spawn(
        self, *,
        template: SubagentTemplate,
        task: str,
        context: dict | None,
        label: str | None,
        parent_conversation_id: str,
        parent_agent: str,
        timeout_override: int | None,
    ) -> str:
        if self._count_in_flight(parent_conversation_id) >= self.PER_CONV_CAP:
            raise RuntimeError(
                f"too many in-flight subagents on this conversation "
                f"({self.PER_CONV_CAP}/{self.PER_CONV_CAP}); await some before spawning more"
            )
        if self._count_in_flight() >= self.GLOBAL_CAP:
            raise RuntimeError(
                f"supervisor in-flight subagent cap reached "
                f"({self.GLOBAL_CAP}/{self.GLOBAL_CAP}); retry shortly"
            )
        eff_timeout = template.timeout_seconds
        if timeout_override is not None:
            if timeout_override < 10 or timeout_override > template.timeout_seconds:
                raise RuntimeError(
                    f"timeout_seconds {timeout_override} exceeds template max "
                    f"({template.timeout_seconds})"
                )
            eff_timeout = timeout_override
        instance_id = _gen_id()
        while instance_id in self._instances:
            instance_id = _gen_id()
        cfg = template_to_agent_config(template, instance_id=instance_id, parent_agent=parent_agent)
        assembled = self._build_assembled(cfg)
        inst = EphemeralInstance(
            instance_id=instance_id, template=template,
            parent_agent=parent_agent, parent_conversation_id=parent_conversation_id,
            task=task, context=context, label=label,
            effective_timeout=eff_timeout, assembled=assembled,
            on_frame=self._on_frame,
            audit_start=self._audit_start, audit_finish=self._audit_finish,
        )
        self._instances[instance_id] = inst

        async def _run_and_clean():
            try:
                await inst.run()
            finally:
                self._instances.pop(instance_id, None)

        inst._task = asyncio.create_task(_run_and_clean())
        return instance_id

    async def await_one(self, instance_id: str, *, ceiling_seconds: int | None) -> InstanceResult:
        inst = self._instances.get(instance_id)
        if inst is None:
            return InstanceResult(
                subagent_id=instance_id, status="error", reply=None,
                duration_ms=0, tool_calls_used=0, error="unknown subagent_id",
            )
        return await inst.wait(ceiling_seconds=ceiling_seconds)

    def stop(self, instance_id: str) -> bool:
        inst = self._instances.get(instance_id)
        if not inst:
            return False
        inst.stop()
        return True

    def active(self) -> list[dict]:
        return [{
            "subagent_id": i.id,
            "template": i.template.name,
            "label": i.label,
            "parent_conversation_id": i.parent_conversation_id,
            "started_ts": None,  # filled by audit query if needed
            "tool_calls_used": i.tool_calls_used,
        } for i in self._instances.values()]
```

Add `pytest-asyncio` configuration if not already present — `[tool.pytest.ini_options] asyncio_mode = "auto"` in `kc-subagents/pyproject.toml`.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/runner.py kc-subagents/tests/test_runner.py kc-subagents/pyproject.toml
git commit -m "feat(kc-subagents): EphemeralInstance + SubagentRunner happy path (Subagents Task 8)"
```

---

## Task 9: Terminal states — error, timeout, stopped

**Files:**
- Modify: `kc-subagents/tests/test_runner.py`
- (Implementation already in place; this task locks the contract with tests.)

- [ ] **Step 1: Write failing tests for each non-ok state**

```python
@pytest.mark.asyncio
async def test_instance_run_error_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class BadAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                raise RuntimeError("boom")
            self.core_agent.send = send
            self.core_agent.history = []

    inst = EphemeralInstance(
        instance_id="ep_e", template=t, parent_agent="Kona-AI",
        parent_conversation_id="conv_1", task="x", context=None, label=None,
        effective_timeout=5, assembled=BadAgent(),
        on_frame=lambda f: None,
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
    )
    result = await inst.run()
    assert result.status == "error"
    assert "boom" in (result.error or "")

@pytest.mark.asyncio
async def test_instance_run_timeout_path():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class SlowAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(10)
            self.core_agent.send = send
            self.core_agent.history = []

    inst = EphemeralInstance(
        instance_id="ep_t", template=t, parent_agent="Kona-AI",
        parent_conversation_id="conv_1", task="x", context=None, label=None,
        effective_timeout=1, assembled=SlowAgent(),
        on_frame=lambda f: None,
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
    )
    result = await inst.run()
    assert result.status == "timeout"
    assert "1s" in (result.error or "")

@pytest.mark.asyncio
async def test_runner_stop_yields_stopped_status():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class HangAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(60)
            self.core_agent.send = send
            self.core_agent.history = []

    runner = SubagentRunner(
        build_assembled=lambda cfg: HangAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="x", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    await asyncio.sleep(0.05)
    assert runner.stop(handle) is True
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "stopped"
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: PASS. (Implementation from Task 8 already handles all three; if any FAIL, fix the runner.)

- [ ] **Step 3: Commit**

```bash
git add kc-subagents/tests/test_runner.py
git commit -m "test(kc-subagents): lock error/timeout/stopped terminal contracts (Subagents Task 9)"
```

---

## Task 10: Per-conversation + global cap tests; bad-template + timeout-override errors

**Files:**
- Modify: `kc-subagents/tests/test_runner.py`

- [ ] **Step 1: Add failing tests for caps + overrides**

```python
@pytest.mark.asyncio
async def test_per_conversation_cap(monkeypatch):
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))

    class HangAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                await asyncio.sleep(60)
            self.core_agent.send = send
            self.core_agent.history = []

    runner = SubagentRunner(
        build_assembled=lambda cfg: HangAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handles = []
    for _ in range(runner.PER_CONV_CAP):
        handles.append(runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=None,
        ))
    with pytest.raises(RuntimeError, match="too many in-flight"):
        runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=None,
        )
    # Cleanup so test process exits cleanly.
    for h in handles:
        runner.stop(h)

def test_timeout_override_too_large_rejected():
    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         timeout_seconds=120, source_path=Path("/tmp/x.yaml"))
    runner = SubagentRunner(
        build_assembled=lambda cfg: MagicMock(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    with pytest.raises(RuntimeError, match="exceeds template max"):
        runner.spawn(
            template=t, task="x", context=None, label=None,
            parent_conversation_id="conv_1", parent_agent="Kona-AI",
            timeout_override=999,
        )
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest kc-subagents/tests/test_runner.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add kc-subagents/tests/test_runner.py
git commit -m "test(kc-subagents): per-conversation cap + timeout-override validation (Subagents Task 10)"
```

---

## Task 11: WS trace buffer + reconnect replay

**Files:**
- Create: `kc-subagents/src/kc_subagents/trace.py`
- Create: `kc-subagents/tests/test_trace.py`

- [ ] **Step 1: Write failing tests**

```python
# kc-subagents/tests/test_trace.py
from kc_subagents.trace import TraceBuffer

def test_buffer_replays_in_order():
    buf = TraceBuffer()
    buf.append("conv_1", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("conv_1", {"type": "subagent_tool",    "subagent_id": "ep_a", "tool": "x"})
    buf.append("conv_2", {"type": "subagent_started", "subagent_id": "ep_b"})
    assert [f["type"] for f in buf.snapshot("conv_1")] == [
        "subagent_started", "subagent_tool",
    ]
    assert [f["type"] for f in buf.snapshot("conv_2")] == ["subagent_started"]

def test_buffer_evicts_on_finished_frame():
    buf = TraceBuffer()
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_tool",    "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_finished", "subagent_id": "ep_a", "status": "ok"})
    assert buf.snapshot("c") == []

def test_buffer_keeps_other_instance_frames_after_one_finishes():
    buf = TraceBuffer()
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_a"})
    buf.append("c", {"type": "subagent_started", "subagent_id": "ep_b"})
    buf.append("c", {"type": "subagent_finished", "subagent_id": "ep_a", "status": "ok"})
    snap = buf.snapshot("c")
    assert [f["subagent_id"] for f in snap] == ["ep_b"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest kc-subagents/tests/test_trace.py -v`
Expected: FAIL — `TraceBuffer` not defined.

- [ ] **Step 3: Implement `TraceBuffer`**

```python
# kc-subagents/src/kc_subagents/trace.py
from __future__ import annotations
import threading
from collections import defaultdict

class TraceBuffer:
    """Per-parent-conversation buffer of in-flight subagent frames.

    Frames are appended as instances emit them and evicted (per subagent_id)
    when a 'subagent_finished' frame for that id arrives. snapshot() returns
    the still-buffered frames in append order; used on WS reconnect.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_conv: dict[str, list[dict]] = defaultdict(list)

    def append(self, conversation_id: str, frame: dict) -> None:
        with self._lock:
            self._by_conv[conversation_id].append(frame)
            if frame.get("type") == "subagent_finished":
                sid = frame.get("subagent_id")
                self._by_conv[conversation_id] = [
                    f for f in self._by_conv[conversation_id]
                    if f.get("subagent_id") != sid
                ]
                if not self._by_conv[conversation_id]:
                    self._by_conv.pop(conversation_id, None)

    def snapshot(self, conversation_id: str) -> list[dict]:
        with self._lock:
            return list(self._by_conv.get(conversation_id, []))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest kc-subagents/tests/test_trace.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/trace.py kc-subagents/tests/test_trace.py
git commit -m "feat(kc-subagents): TraceBuffer for WS reconnect replay (Subagents Task 11)"
```

---

## Task 12: `spawn_subagent` + `await_subagents` tools

**Files:**
- Create: `kc-subagents/src/kc_subagents/tools.py`
- Create: `kc-subagents/tests/test_tools.py`

- [ ] **Step 1: Write failing tests for tool surface**

```python
# kc-subagents/tests/test_tools.py
import asyncio, pytest, json
from pathlib import Path
from unittest.mock import MagicMock
from kc_subagents.templates import SubagentTemplate, SubagentIndex
from kc_subagents.runner import SubagentRunner
from kc_subagents.tools import build_subagent_tools

class FakeOkAgent:
    def __init__(self):
        self.core_agent = MagicMock()
        async def send(m): return MagicMock(content="done")
        self.core_agent.send = send
        self.core_agent.history = []

def _index_with(tmp_path, body: str) -> SubagentIndex:
    (tmp_path / "x.yaml").write_text(body)
    return SubagentIndex(tmp_path)

@pytest.mark.asyncio
async def test_spawn_then_await_one(tmp_path):
    idx = _index_with(tmp_path, "name: x\nmodel: m\nsystem_prompt: y\n")
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    spawn = next(t for t in tools if t.name == "spawn_subagent")
    awaiter = next(t for t in tools if t.name == "await_subagents")
    spawn_out = json.loads(await spawn.impl(template="x", task="go"))
    assert spawn_out["status"] == "running"
    handle = spawn_out["subagent_id"]
    await_out = json.loads(await awaiter.impl(subagent_ids=[handle]))
    assert await_out[0]["status"] == "ok"
    assert await_out[0]["reply"]  == "done"

@pytest.mark.asyncio
async def test_spawn_unknown_template_returns_error_string(tmp_path):
    idx = SubagentIndex(tmp_path)
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    spawn = next(t for t in tools if t.name == "spawn_subagent")
    result = await spawn.impl(template="missing", task="x")
    assert "error: unknown template" in result

@pytest.mark.asyncio
async def test_await_unknown_handle_reports_error_row(tmp_path):
    idx = SubagentIndex(tmp_path)
    runner = SubagentRunner(
        build_assembled=lambda cfg: FakeOkAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    tools = build_subagent_tools(index=idx, runner=runner,
                                 current_context=lambda: ("conv_1", "Kona-AI"))
    awaiter = next(t for t in tools if t.name == "await_subagents")
    out = json.loads(await awaiter.impl(subagent_ids=["ep_nope"]))
    assert out[0]["status"] == "error"
    assert "unknown subagent_id" in out[0]["error"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest kc-subagents/tests/test_tools.py -v`
Expected: FAIL — module/function not defined.

- [ ] **Step 3: Implement `tools.py`**

```python
# kc-subagents/src/kc_subagents/tools.py
from __future__ import annotations
import asyncio, json
from typing import Callable
from kc_core.tools import Tool                       # adjust import path if different
from kc_subagents.templates import SubagentIndex
from kc_subagents.runner import SubagentRunner

CurrentContext = Callable[[], tuple[str, str]]       # () -> (conversation_id, parent_agent)

def build_subagent_tools(
    *,
    index: SubagentIndex,
    runner: SubagentRunner,
    current_context: CurrentContext,
) -> list[Tool]:

    async def spawn_impl(
        template: str, task: str,
        context: dict | None = None,
        label: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        t = index.get(template)
        if t is None:
            degraded = index.degraded()
            if template in degraded:
                return f"error: template {template!r} is degraded: {degraded[template]}"
            return f"error: unknown template {template!r}"
        try:
            cid, parent_agent = current_context()
        except Exception:
            return "error: no current conversation context"
        try:
            handle = runner.spawn(
                template=t, task=task, context=context, label=label,
                parent_conversation_id=cid, parent_agent=parent_agent,
                timeout_override=timeout_seconds,
            )
        except RuntimeError as e:
            return f"error: {e}"
        return json.dumps({
            "subagent_id": handle, "status": "running",
            "template": template, "label": label,
        })

    async def await_impl(
        subagent_ids: list[str], timeout_seconds: int | None = None,
    ) -> str:
        ceiling = None
        if timeout_seconds is not None:
            ceiling = max(10, min(int(timeout_seconds), 1800))
        results = await asyncio.gather(
            *[runner.await_one(h, ceiling_seconds=ceiling) for h in subagent_ids],
            return_exceptions=False,
        )
        out = []
        for r in results:
            row = {
                "subagent_id":     r.subagent_id,
                "status":          r.status,
                "duration_ms":     r.duration_ms,
                "tool_calls_used": r.tool_calls_used,
            }
            if r.status == "ok":
                row["reply"] = r.reply or ""
            else:
                row["error"] = r.error or ""
            out.append(row)
        return json.dumps(out)

    spawn_tool = Tool(
        name="spawn_subagent",
        description=(
            "Spawn an ephemeral subagent from a registered template to perform a "
            "single mission. Returns a handle JSON; pair with await_subagents to "
            "collect the result. Subagent runs in fresh context."
        ),
        parameters={
            "type": "object",
            "properties": {
                "template":        {"type": "string"},
                "task":            {"type": "string"},
                "context":         {"type": "object"},
                "label":           {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["template", "task"],
        },
        impl=spawn_impl,
    )
    await_tool = Tool(
        name="await_subagents",
        description=(
            "Join one or more subagent handles previously returned by "
            "spawn_subagent. Returns a JSON array preserving input order."
        ),
        parameters={
            "type": "object",
            "properties": {
                "subagent_ids":    {"type": "array", "items": {"type": "string"},
                                    "minItems": 1, "maxItems": 8},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["subagent_ids"],
        },
        impl=await_impl,
    )
    return [spawn_tool, await_tool]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest kc-subagents/tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-subagents/src/kc_subagents/tools.py kc-subagents/tests/test_tools.py
git commit -m "feat(kc-subagents): spawn_subagent + await_subagents tools (Subagents Task 12)"
```

---

## Task 13: Supervisor `assemble_agent` accepts new deps + registers tools on Kona only

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py`
- Modify: `kc-supervisor/src/kc_supervisor/agents.py` (thread kwargs through)
- Modify: `kc-supervisor/tests/test_assembly.py`

- [ ] **Step 1: Write failing tests for the gating**

Append to `kc-supervisor/tests/test_assembly.py`:

```python
def test_kona_gets_subagent_tools_when_enabled(tmp_path, monkeypatch):
    cfg = make_agent_config(name="Kona-AI", model="m", system_prompt="x")
    fake_index = MagicMock()
    fake_runner = MagicMock()
    fake_index.names.return_value = ["web-researcher"]
    agent = assemble_agent(
        cfg=cfg, shares=MagicMock(), audit_storage=MagicMock(),
        broker=MagicMock(), ollama_url="x", default_model="m",
        undo_db_path=tmp_path/"undo.db", resolve_agent=lambda n: (None, "unknown"),
        subagent_index=fake_index, subagent_runner=fake_runner,
        # ... pass other required kwargs ...
    )
    tool_names = {t.name for t in agent.tools}
    assert "spawn_subagent"   in tool_names
    assert "await_subagents"  in tool_names

def test_non_kona_does_not_get_subagent_tools(tmp_path):
    cfg = make_agent_config(name="Research-Agent", model="m", system_prompt="x")
    agent = assemble_agent(
        cfg=cfg, shares=MagicMock(), audit_storage=MagicMock(),
        broker=MagicMock(), ollama_url="x", default_model="m",
        undo_db_path=tmp_path/"undo.db", resolve_agent=lambda n: (None, "unknown"),
        subagent_index=MagicMock(), subagent_runner=MagicMock(),
        # ... pass other required kwargs ...
    )
    tool_names = {t.name for t in agent.tools}
    assert "spawn_subagent"  not in tool_names
    assert "await_subagents" not in tool_names

def test_ephemeral_instance_does_not_get_subagent_tools(tmp_path):
    cfg = make_agent_config(name="Kona-AI/ep_abc/web-researcher",
                            model="m", system_prompt="x")
    agent = assemble_agent(
        cfg=cfg, shares=MagicMock(), audit_storage=MagicMock(),
        broker=MagicMock(), ollama_url="x", default_model="m",
        undo_db_path=tmp_path/"undo.db", resolve_agent=lambda n: (None, "unknown"),
        subagent_index=MagicMock(), subagent_runner=MagicMock(),
        # ... pass other required kwargs ...
    )
    tool_names = {t.name for t in agent.tools}
    assert "spawn_subagent"     not in tool_names
    assert "await_subagents"    not in tool_names
    assert "delegate_to_agent"  not in tool_names
```

(`make_agent_config` is a helper used elsewhere in that test file; if absent, build the AgentConfig inline matching the existing test pattern.)

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest kc-supervisor/tests/test_assembly.py -v -k subagent`
Expected: FAIL — kwargs not accepted.

- [ ] **Step 3: Wire `assemble_agent`**

In `assembly.py`:

```python
from kc_subagents.tools import build_subagent_tools

def assemble_agent(
    cfg,
    *,
    # ... existing args ...
    subagent_index=None,
    subagent_runner=None,
    # ...
):
    # ... existing body ...
    is_kona      = cfg.name in ("kona", "Kona-AI")
    is_ephemeral = "/ep_" in cfg.name  # synthetic ephemeral name pattern
    if is_kona and not is_ephemeral and subagent_index is not None and subagent_runner is not None:
        tools.extend(build_subagent_tools(
            index=subagent_index,
            runner=subagent_runner,
            current_context=lambda: (
                kc_supervisor.scheduling.context.get_current_conversation_id(),
                cfg.name,
            ),
        ))
    if is_ephemeral:
        # Strip delegate_to_agent if it was registered earlier — ephemeral instances
        # cannot delegate or spawn.
        tools = [t for t in tools if t.name not in ("delegate_to_agent",)]
```

(Adjust the `current_context` resolver to match how the existing scheduling/todos tools read the active conversation_id — the contextvar lives in `kc_supervisor.scheduling.context` per `agents.py` precedent.)

In `agents.py` (`AgentRegistry.__init__`), add `subagent_index` and `subagent_runner` kwargs, threaded through to `assemble_agent` in `load_all`.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest kc-supervisor/tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/assembly.py kc-supervisor/src/kc_supervisor/agents.py kc-supervisor/tests/test_assembly.py
git commit -m "feat(kc-supervisor): register subagent tools on Kona only (Subagents Task 13)"
```

---

## Task 14: `EphemeralRunner.build_assembled` wiring + attribution contextvar

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py` (build the runner with a real `build_assembled` closure that calls `assemble_agent`)
- Modify: `kc-subagents/src/kc_subagents/runner.py` (set the attribution contextvar around `core_agent.send`)
- Modify: `kc-subagents/tests/test_runner.py`

- [ ] **Step 1: Write failing test that attribution contextvar leaks into the agent's send**

```python
@pytest.mark.asyncio
async def test_run_sets_subagent_attribution_contextvar():
    import contextvars
    captured = {}

    class WatcherAgent:
        def __init__(self):
            self.core_agent = MagicMock()
            async def send(message):
                # Read the contextvar that the runner is expected to set.
                from kc_supervisor.approvals import subagent_attribution_var
                captured["attrib"] = subagent_attribution_var.get()
                return MagicMock(content="ok")
            self.core_agent.send = send
            self.core_agent.history = []

    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         source_path=Path("/tmp/x.yaml"))
    runner = SubagentRunner(
        build_assembled=lambda cfg: WatcherAgent(),
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="x", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "ok"
    assert captured["attrib"] == {"parent_agent": "Kona-AI", "subagent_id": handle}
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest kc-subagents/tests/test_runner.py::test_run_sets_subagent_attribution_contextvar -v`
Expected: FAIL — contextvar not set.

- [ ] **Step 3: Set the contextvar in `EphemeralInstance.run`**

Modify `runner.py` to import and set the contextvar around the `send` call:

```python
try:
    from kc_supervisor.approvals import subagent_attribution_var
except Exception:                                       # pragma: no cover
    subagent_attribution_var = None

# ... inside run(), replacing the existing try-block that calls send():
token = None
if subagent_attribution_var is not None:
    token = subagent_attribution_var.set({
        "parent_agent": self.parent_agent,
        "subagent_id":  self.id,
    })
try:
    assistant = await asyncio.wait_for(
        self.assembled.core_agent.send(self._compose_message()),
        timeout=self.effective_timeout,
    )
    reply = (getattr(assistant, "content", "") or "").strip()
except asyncio.TimeoutError:
    status, error = "timeout", f"timed out after {self.effective_timeout}s"
# ... rest of the existing except/else block unchanged ...
finally:
    if token is not None and subagent_attribution_var is not None:
        subagent_attribution_var.reset(token)
```

- [ ] **Step 4: Wrap tool impls with the `tool_calls_used` counter + `max_tool_calls` cap**

Add to `runner.py`:

```python
from kc_core.tools import Tool

def _wrap_tools_with_counter(tools: list, instance: "EphemeralInstance") -> list:
    """Return a new list of tools whose impls bump instance.tool_calls_used and
    fail-fast once the template's max_tool_calls cap is reached.

    The synthetic 'cap reached' error string is returned as the tool result so
    the parent agent's loop stays alive and produces a final assistant turn.
    """
    wrapped: list[Tool] = []
    for t in tools:
        original_impl = t.impl
        async def _counted_impl(_t=t, _orig=original_impl, **kwargs):
            if instance.tool_calls_used >= instance.template.max_tool_calls:
                return (
                    f"error: max_tool_calls cap reached "
                    f"({instance.template.max_tool_calls})"
                )
            instance.tool_calls_used += 1
            return await _orig(**kwargs)
        wrapped.append(Tool(
            name=t.name, description=t.description,
            parameters=t.parameters, impl=_counted_impl,
        ))
    return wrapped
```

In `SubagentRunner.spawn`, immediately after `assembled = self._build_assembled(cfg)`:

```python
assembled.tools = _wrap_tools_with_counter(assembled.tools, inst)
```

(If `AssembledAgent` exposes tools under a different attribute, adapt the assignment. The supervisor's existing assembly puts them on `core_agent.tools` per the patterns in `assembly.py`; verify with `grep -n "core_agent" kc-supervisor/src/kc_supervisor/assembly.py`.)

Add a failing test for the cap, then run it:

```python
@pytest.mark.asyncio
async def test_max_tool_calls_cap_short_circuits(monkeypatch):
    from kc_core.tools import Tool
    calls = []
    async def fake_tool_impl(**kw):
        calls.append(kw); return "ok"
    fake_tool = Tool(name="fake", description="", parameters={"type":"object"}, impl=fake_tool_impl)

    class AgentWithTool:
        def __init__(self):
            self.tools = [fake_tool]
            self.core_agent = MagicMock()
            async def send(message):
                # Pretend the model called the tool 5 times via the supervisor's tool-call loop.
                for _ in range(5):
                    result = await self.tools[0].impl()
                return MagicMock(content="done")
            self.core_agent.send = send
            self.core_agent.history = []

    t = SubagentTemplate(name="x", model="m", system_prompt="y",
                         max_tool_calls=3, source_path=Path("/tmp/x.yaml"))
    agent = AgentWithTool()
    runner = SubagentRunner(
        build_assembled=lambda cfg: agent,
        audit_start=lambda **kw: None, audit_finish=lambda **kw: None,
        on_frame=lambda f: None,
    )
    handle = runner.spawn(
        template=t, task="x", context=None, label=None,
        parent_conversation_id="conv_1", parent_agent="Kona-AI",
        timeout_override=None,
    )
    result = await runner.await_one(handle, ceiling_seconds=5)
    assert result.status == "ok"
    assert result.tool_calls_used == 3
    assert len(calls) == 3   # 4th and 5th call short-circuited
```

Run: `python -m pytest kc-subagents/tests/test_runner.py::test_max_tool_calls_cap_short_circuits -v`
Expected: PASS.

- [ ] **Step 5: Wire audit attribution into tool_calls inserts**

The supervisor's audit pipeline writes a `tool_calls` row each time a tool fires. To populate the new `parent_agent` / `subagent_id` / `subagent_template` columns, the audit insert call site must read `subagent_attribution_var` at insert time.

Locate the insert: `grep -n "INSERT INTO tool_calls" kc-sandbox/ kc-supervisor/ -r`

Modify the insert helper (call it `record_tool_call` or similar) to read the contextvar and pass the three fields:

```python
from kc_supervisor.approvals import subagent_attribution_var

def record_tool_call(self, *, tool, args, result, tier, agent, ...):
    attrib = subagent_attribution_var.get() if 'subagent_attribution_var' in globals() else None
    parent_agent      = (attrib or {}).get("parent_agent")
    subagent_id       = (attrib or {}).get("subagent_id")
    subagent_template = None
    if attrib and parent_agent and "/" in agent:
        # Synthetic ephemeral name: "<parent>/<instance_id>/<template>"
        subagent_template = agent.rsplit("/", 1)[-1]
    self.conn.execute(
        "INSERT INTO tool_calls (tool, args, result, tier, agent, parent_agent, subagent_id, subagent_template, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (tool, json.dumps(args), result, tier, agent,
         parent_agent, subagent_id, subagent_template),
    )
```

Add a test that exercises the contextvar → audit row path:

```python
# kc-sandbox/tests/test_audit_subagents.py — append
def test_tool_call_row_carries_attribution(tmp_path):
    from kc_supervisor.approvals import subagent_attribution_var
    s = Storage(db_path=tmp_path / "audit.sqlite")
    s.init_schema()
    token = subagent_attribution_var.set({"parent_agent": "Kona-AI", "subagent_id": "ep_abc"})
    try:
        s.record_tool_call(
            tool="terminal_run", args={"argv": ["ls"]}, result="ok",
            tier="MUTATING", agent="Kona-AI/ep_abc/coder",
        )
    finally:
        subagent_attribution_var.reset(token)
    row = s.conn.execute(
        "SELECT parent_agent, subagent_id, subagent_template FROM tool_calls"
    ).fetchone()
    assert row == ("Kona-AI", "ep_abc", "coder")
```

Run: `python -m pytest kc-sandbox/tests/test_audit_subagents.py -v`
Expected: PASS.

- [ ] **Step 6: Wire `build_assembled` in `main.py`**

(This will not be exercised until Task 17 enables the flag, but the closure should be defined now.)

```python
# kc-supervisor/src/kc_supervisor/main.py — inside startup, after registry is built
from kc_subagents.templates import SubagentIndex
from kc_subagents.runner import SubagentRunner
from kc_subagents.trace import TraceBuffer

trace_buffer = TraceBuffer()

def _on_subagent_frame(frame: dict) -> None:
    cid = frame.get("parent_conversation_id") or frame.get("conversation_id")
    # Frames carry conversation context via closure when emitted; for now we tag in EphemeralInstance.
    # (Threaded fully in Task 15.)
    trace_buffer.append(cid, frame) if cid else None
    ws_routes.broadcast_to_conversation(cid, frame) if cid else None

def _build_ephemeral_assembled(cfg):
    # Re-enter assemble_agent with the ephemeral cfg shape.
    return assembly.assemble_ephemeral(cfg=cfg, registry=agent_registry)

subagent_runner = SubagentRunner(
    build_assembled=_build_ephemeral_assembled,
    audit_start=audit_storage.start_subagent_run,
    audit_finish=audit_storage.finish_subagent_run,
    on_frame=_on_subagent_frame,
)
```

Add a thin `assemble_ephemeral(cfg, registry)` helper in `assembly.py` that maps `EphemeralAgentConfig` to the supervisor's existing `AgentConfig` shape and calls the same `assemble_agent` machinery (excluding the spawn/delegate tools — ephemeral instances don't get them).

- [ ] **Step 7: Run all tests**

Run: `python -m pytest kc-subagents/ kc-supervisor/tests kc-sandbox/tests -v -k "subagent or assembly or audit"`
Expected: ALL pass.

- [ ] **Step 8: Commit**

```bash
git add kc-subagents/src/kc_subagents/runner.py kc-subagents/tests/test_runner.py kc-supervisor/src/kc_supervisor/main.py kc-supervisor/src/kc_supervisor/assembly.py kc-sandbox/
git commit -m "feat(kc-supervisor): SubagentRunner + tool-call counter + audit attribution (Subagents Task 14)"
```

---

## Task 15: WS frame routing (parent conv) + `subagent_stop` handler

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/ws_routes.py`
- Modify: `kc-subagents/src/kc_subagents/runner.py` (have `_on_frame` callback carry `parent_conversation_id` so ws_routes can route)
- Modify: `kc-supervisor/tests/test_ws_subagent.py` (new file)

- [ ] **Step 1: Update `EphemeralInstance._emit` to tag conversation_id**

In `runner.py`, change `_emit` to:

```python
def _emit(self, frame: dict) -> None:
    self._on_frame({
        **frame,
        "subagent_id": self.id,
        "parent_conversation_id": self.parent_conversation_id,
    })
```

- [ ] **Step 2: Write failing test for the WS stop frame handler**

```python
# kc-supervisor/tests/test_ws_subagent.py
import asyncio, pytest, json
from unittest.mock import MagicMock, AsyncMock
from kc_supervisor.ws_routes import _handle_inbound_frame  # adjust import path

@pytest.mark.asyncio
async def test_subagent_stop_frame_routes_to_runner():
    runner = MagicMock()
    runner.stop.return_value = True
    deps = MagicMock(subagent_runner=runner)
    ws  = AsyncMock()
    await _handle_inbound_frame(
        deps=deps, ws=ws, conversation_id="conv_1",
        frame={"type": "subagent_stop", "subagent_id": "ep_abc"},
    )
    runner.stop.assert_called_once_with("ep_abc")
```

(`_handle_inbound_frame` is a helper that should be extracted from the existing ws handler if it's currently inline — extract it if needed to make the handler testable. If extracting is too invasive, write an integration-style test by connecting a TestClient WebSocket and sending the frame.)

- [ ] **Step 3: Run test to verify failure**

Run: `python -m pytest kc-supervisor/tests/test_ws_subagent.py -v`
Expected: FAIL.

- [ ] **Step 4: Add the handler branch + reconnect replay**

In `ws_routes.py`, find the inbound-frame dispatch (the same place that handles `clarify_response`) and add:

```python
if frame.get("type") == "subagent_stop":
    sid = frame.get("subagent_id")
    if sid and deps.subagent_runner:
        deps.subagent_runner.stop(sid)
    return
```

On WS connect, after sending the existing reconnect catch-up (clarify, etc.), replay buffered subagent frames:

```python
if deps.subagent_trace_buffer:
    for buffered in deps.subagent_trace_buffer.snapshot(conversation_id):
        await ws.send_json(buffered)
```

Add `subagent_trace_buffer` to the `Deps` dataclass.

- [ ] **Step 5: Run test to verify pass**

Run: `python -m pytest kc-supervisor/tests/test_ws_subagent.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/ws_routes.py kc-subagents/src/kc_subagents/runner.py kc-supervisor/tests/test_ws_subagent.py
git commit -m "feat(kc-supervisor): WS subagent_stop handler + trace replay (Subagents Task 15)"
```

---

## Task 16: HTTP routes — `/subagent-templates` CRUD + `/subagents/active` + stop

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Create: `kc-supervisor/tests/test_http_subagents.py`

- [ ] **Step 1: Write failing tests for the routes**

```python
# kc-supervisor/tests/test_http_subagents.py
import json, yaml
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from kc_supervisor.main import build_app  # adjust to whatever factory exists

@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    return d

@pytest.fixture
def client(templates_dir, monkeypatch):
    monkeypatch.setenv("KC_SUBAGENTS_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KC_SUBAGENTS_ENABLED", "true")
    app = build_app()
    return TestClient(app), templates_dir

def test_list_templates_empty(client):
    c, _ = client
    r = c.get("/subagent-templates")
    assert r.status_code == 200
    assert r.json() == []

def test_create_template_writes_yaml(client):
    c, dir_ = client
    body = {
        "yaml": "name: web-researcher\nmodel: m\nsystem_prompt: research\n"
    }
    r = c.post("/subagent-templates", json=body)
    assert r.status_code == 201
    assert (dir_ / "web-researcher.yaml").exists()
    r2 = c.get("/subagent-templates")
    assert any(t["name"] == "web-researcher" for t in r2.json())

def test_delete_template_removes_file(client):
    c, dir_ = client
    (dir_ / "coder.yaml").write_text("name: coder\nmodel: m\nsystem_prompt: x\n")
    r = c.delete("/subagent-templates/coder")
    assert r.status_code == 204
    assert not (dir_ / "coder.yaml").exists()

def test_active_endpoint_returns_in_flight(client):
    c, _ = client
    r = c.get("/subagents/active")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest kc-supervisor/tests/test_http_subagents.py -v`
Expected: FAIL — routes not defined.

- [ ] **Step 3: Add the routes**

In `http_routes.py`:

```python
from pathlib import Path
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
import yaml

router = APIRouter()

class TemplateBody(BaseModel):
    yaml: str

@router.get("/subagent-templates")
def list_templates(deps = Depends(get_deps)):
    if not deps.subagent_index:
        return []
    rows = []
    degraded = deps.subagent_index.degraded()
    for name in deps.subagent_index.names():
        t = deps.subagent_index.get(name)
        rows.append({
            "name":         t.name,
            "description":  t.description,
            "model":        t.model,
            "tool_count":   len(t.tools),
            "mcp_count":    len(t.mcp_servers),
            "status":       "ok",
            "last_error":   None,
        })
    for bad_name, err in degraded.items():
        rows.append({
            "name": bad_name, "description": "", "model": "?",
            "tool_count": 0, "mcp_count": 0,
            "status": "degraded", "last_error": err,
        })
    return rows

@router.get("/subagent-templates/{name}")
def get_template(name: str, deps = Depends(get_deps)):
    if not deps.subagent_index:
        raise HTTPException(503, "subagents disabled")
    p = deps.subagent_templates_dir / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, "not found")
    return {"name": name, "yaml": p.read_text()}

@router.post("/subagent-templates", status_code=201)
def create_template(body: TemplateBody, deps = Depends(get_deps)):
    if not deps.subagent_index:
        raise HTTPException(503, "subagents disabled")
    try:
        parsed = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as e:
        raise HTTPException(422, f"bad yaml: {e}")
    name = parsed.get("name")
    if not name:
        raise HTTPException(422, "name required")
    p = deps.subagent_templates_dir / f"{name}.yaml"
    if p.exists():
        raise HTTPException(409, f"template {name!r} already exists")
    p.write_text(body.yaml)
    deps.subagent_index.reload()
    return {"name": name}

@router.patch("/subagent-templates/{name}")
def update_template(name: str, body: TemplateBody, deps = Depends(get_deps)):
    if not deps.subagent_index:
        raise HTTPException(503, "subagents disabled")
    p = deps.subagent_templates_dir / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, "not found")
    p.write_text(body.yaml)
    deps.subagent_index.reload()
    return {"name": name}

@router.delete("/subagent-templates/{name}", status_code=204)
def delete_template(name: str, deps = Depends(get_deps)):
    if not deps.subagent_index:
        raise HTTPException(503, "subagents disabled")
    p = deps.subagent_templates_dir / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, "not found")
    p.unlink()
    deps.subagent_index.reload()
    return Response(status_code=204)

@router.get("/subagents/active")
def active(deps = Depends(get_deps)):
    if not deps.subagent_runner:
        return []
    return deps.subagent_runner.active()

@router.post("/subagents/{sid}/stop")
def stop_subagent(sid: str, deps = Depends(get_deps)):
    if not deps.subagent_runner:
        raise HTTPException(503, "subagents disabled")
    ok = deps.subagent_runner.stop(sid)
    return {"stopped": bool(ok)}
```

Mount the router in the FastAPI app.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest kc-supervisor/tests/test_http_subagents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_http_subagents.py
git commit -m "feat(kc-supervisor): subagent-templates + active + stop HTTP routes (Subagents Task 16)"
```

---

## Task 17: `main.py` startup wiring + `KC_SUBAGENTS_ENABLED` flag + seed templates installer

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py`
- Create: `kc-subagents/src/kc_subagents/seeds/__init__.py`
- Create: `kc-subagents/src/kc_subagents/seeds/web-researcher.yaml`
- Create: `kc-subagents/src/kc_subagents/seeds/coder.yaml`
- Create: `kc-subagents/src/kc_subagents/seeds/email-drafter.yaml`
- Create: `kc-subagents/src/kc_subagents/seeds/scheduler.yaml`
- Create: `kc-subagents/src/kc_subagents/seeds/install.py`
- Modify: `kc-subagents/tests/test_seeds.py`

- [ ] **Step 1: Write the four seed YAMLs verbatim from spec §11**

Use the exact YAML bodies in spec §11.1 / 11.2 / 11.3 / 11.4. Each file's stem must match its `name:` field.

- [ ] **Step 2: Write the installer**

```python
# kc-subagents/src/kc_subagents/seeds/install.py
from pathlib import Path
import shutil

SEED_DIR = Path(__file__).parent

def install_seeds_if_empty(target_dir: Path) -> list[str]:
    """If target_dir is empty (or missing), copy seed YAMLs into it.

    Returns the list of seed names installed. Existing user files are never
    overwritten — if any *.yaml file is already present, no seeds are installed.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    if any(target_dir.glob("*.yaml")):
        return []
    installed: list[str] = []
    for src in sorted(SEED_DIR.glob("*.yaml")):
        shutil.copy(src, target_dir / src.name)
        installed.append(src.stem)
    return installed
```

- [ ] **Step 3: Write failing test for the installer**

```python
# kc-subagents/tests/test_seeds.py
from pathlib import Path
from kc_subagents.seeds.install import install_seeds_if_empty

def test_installs_all_four_into_empty_dir(tmp_path: Path):
    installed = install_seeds_if_empty(tmp_path)
    assert sorted(installed) == ["coder", "email-drafter", "scheduler", "web-researcher"]
    assert (tmp_path / "web-researcher.yaml").exists()

def test_does_not_overwrite_existing(tmp_path: Path):
    (tmp_path / "user.yaml").write_text("name: user\nmodel: m\nsystem_prompt: x\n")
    installed = install_seeds_if_empty(tmp_path)
    assert installed == []
    assert (tmp_path / "user.yaml").exists()
    assert not (tmp_path / "web-researcher.yaml").exists()
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest kc-subagents/tests/test_seeds.py -v`
Expected: PASS.

- [ ] **Step 5: Wire startup in `main.py`**

```python
import os
from kc_subagents.templates import SubagentIndex
from kc_subagents.runner import SubagentRunner
from kc_subagents.trace import TraceBuffer
from kc_subagents.seeds.install import install_seeds_if_empty

KC_SUBAGENTS_ENABLED = os.environ.get("KC_SUBAGENTS_ENABLED", "false").lower() == "true"

if KC_SUBAGENTS_ENABLED:
    templates_dir = Path(os.environ.get(
        "KC_SUBAGENTS_TEMPLATES_DIR",
        str(Path.home() / "KonaClaw" / "subagent-templates"),
    ))
    install_seeds_if_empty(templates_dir)
    subagent_index  = SubagentIndex(templates_dir)
    subagent_trace  = TraceBuffer()
    subagent_runner = SubagentRunner(
        build_assembled=_build_ephemeral_assembled,
        audit_start=audit_storage.start_subagent_run,
        audit_finish=audit_storage.finish_subagent_run,
        on_frame=_on_subagent_frame,  # appends to subagent_trace and broadcasts
    )
    # Reap any rows left in 'running' from a prior interrupted supervisor process.
    reaped = audit_storage.reap_running_subagent_runs()
    if reaped:
        logger.info("reaped %d in-flight subagent_runs row(s) on startup", reaped)
else:
    subagent_index  = None
    subagent_trace  = None
    subagent_runner = None

# Thread through Deps + AgentRegistry — every consumer must accept None.

deps.subagent_index          = subagent_index
deps.subagent_runner         = subagent_runner
deps.subagent_trace_buffer   = subagent_trace
deps.subagent_templates_dir  = templates_dir if KC_SUBAGENTS_ENABLED else None
```

- [ ] **Step 6: Run the full Python test suite**

Run: `python -m pytest kc-subagents/ kc-supervisor/tests/ kc-sandbox/tests/ -v`
Expected: ALL pass; nothing in the existing supervisor suite regresses.

- [ ] **Step 7: Commit**

```bash
git add kc-subagents/src/kc_subagents/seeds kc-subagents/tests/test_seeds.py kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-supervisor): KC_SUBAGENTS_ENABLED startup wiring + seed templates installer (Subagents Task 17)"
```

---

## Task 18: Dashboard — tab 09 templates list + editor modal

**Files:**
- Modify: `kc-dashboard/src/components/Sidebar.tsx` (add "Subagents" entry)
- Create: `kc-dashboard/src/api/subagents.ts`
- Create: `kc-dashboard/src/pages/SubagentsTab.tsx`
- Create: `kc-dashboard/src/components/SubagentTemplateCard.tsx`
- Create: `kc-dashboard/src/components/SubagentTemplateEditor.tsx`
- Create: `kc-dashboard/tests/api/subagents.test.ts`
- Create: `kc-dashboard/tests/components/SubagentTemplateEditor.test.tsx`

- [ ] **Step 1: Write failing test for the API wrapper**

```ts
// kc-dashboard/tests/api/subagents.test.ts
import { describe, it, expect, vi } from "vitest";
import { listTemplates, createTemplate, deleteTemplate } from "../../src/api/subagents";

describe("subagents API", () => {
  it("listTemplates GETs and parses rows", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ name: "web-researcher", model: "m", tool_count: 2, mcp_count: 0, status: "ok", description: "" }],
    });
    // @ts-ignore
    global.fetch = fetchMock;
    const rows = await listTemplates();
    expect(fetchMock).toHaveBeenCalledWith("/subagent-templates");
    expect(rows[0].name).toBe("web-researcher");
  });
});
```

- [ ] **Step 2: Implement the API wrapper**

```ts
// kc-dashboard/src/api/subagents.ts
export type TemplateRow = {
  name: string; description: string; model: string;
  tool_count: number; mcp_count: number;
  status: "ok" | "degraded"; last_error: string | null;
};

export async function listTemplates(): Promise<TemplateRow[]> {
  const r = await fetch("/subagent-templates");
  if (!r.ok) throw new Error(`listTemplates ${r.status}`);
  return r.json();
}
export async function getTemplate(name: string): Promise<{ name: string; yaml: string }> {
  const r = await fetch(`/subagent-templates/${encodeURIComponent(name)}`);
  if (!r.ok) throw new Error(`getTemplate ${r.status}`);
  return r.json();
}
export async function createTemplate(yamlBody: string): Promise<{ name: string }> {
  const r = await fetch("/subagent-templates", {
    method: "POST", headers: {"content-type": "application/json"},
    body: JSON.stringify({ yaml: yamlBody }),
  });
  if (!r.ok) throw new Error(`createTemplate ${r.status}: ${await r.text()}`);
  return r.json();
}
export async function updateTemplate(name: string, yamlBody: string): Promise<{ name: string }> {
  const r = await fetch(`/subagent-templates/${encodeURIComponent(name)}`, {
    method: "PATCH", headers: {"content-type": "application/json"},
    body: JSON.stringify({ yaml: yamlBody }),
  });
  if (!r.ok) throw new Error(`updateTemplate ${r.status}: ${await r.text()}`);
  return r.json();
}
export async function deleteTemplate(name: string): Promise<void> {
  const r = await fetch(`/subagent-templates/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`deleteTemplate ${r.status}`);
}
```

- [ ] **Step 3: Write the editor modal**

```tsx
// kc-dashboard/src/components/SubagentTemplateEditor.tsx
import React, { useState } from "react";
import { createTemplate, updateTemplate } from "../api/subagents";

type Props = {
  mode: "create" | "edit";
  initialYaml?: string;
  initialName?: string;
  onClose: () => void;
  onSaved: () => void;
};

export function SubagentTemplateEditor({ mode, initialYaml, initialName, onClose, onSaved }: Props) {
  const [yamlText, setYamlText] = useState(initialYaml ?? DEFAULT_TEMPLATE_YAML);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true); setError(null);
    try {
      if (mode === "create") await createTemplate(yamlText);
      else await updateTemplate(initialName!, yamlText);
      onSaved();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div role="dialog" aria-label="Subagent template editor" className="modal">
      <textarea
        aria-label="template yaml"
        value={yamlText}
        onChange={(e) => setYamlText(e.target.value)}
        rows={24}
        style={{width: "100%", fontFamily: "monospace"}}
      />
      {error && <div role="alert" className="error">{error}</div>}
      <div className="actions">
        <button onClick={onClose} disabled={saving}>Cancel</button>
        <button onClick={handleSave} disabled={saving}>Save</button>
      </div>
    </div>
  );
}

const DEFAULT_TEMPLATE_YAML = `name: my-subagent
description: One-line description.
model: claude-opus-4-7
system_prompt: |
  You are a focused subagent. Describe its mission here.
tools:
  skill_view: {}
timeout_seconds: 300
max_tool_calls: 50
`;
```

- [ ] **Step 4: Write component test**

```tsx
// kc-dashboard/tests/components/SubagentTemplateEditor.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SubagentTemplateEditor } from "../../src/components/SubagentTemplateEditor";

vi.mock("../../src/api/subagents", () => ({
  createTemplate: vi.fn().mockResolvedValue({ name: "x" }),
  updateTemplate: vi.fn(),
}));

describe("SubagentTemplateEditor", () => {
  it("saves a new template via createTemplate", async () => {
    const onSaved = vi.fn();
    render(<SubagentTemplateEditor mode="create" onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});
```

- [ ] **Step 5: Build the templates list page**

```tsx
// kc-dashboard/src/pages/SubagentsTab.tsx
import React, { useEffect, useState } from "react";
import { listTemplates, deleteTemplate, getTemplate, TemplateRow } from "../api/subagents";
import { SubagentTemplateEditor } from "../components/SubagentTemplateEditor";

export function SubagentsTab() {
  const [rows, setRows] = useState<TemplateRow[]>([]);
  const [editor, setEditor] = useState<{mode: "create" | "edit", name?: string, yaml?: string} | null>(null);

  async function refresh() { setRows(await listTemplates()); }
  useEffect(() => { refresh(); }, []);

  async function startEdit(name: string) {
    const { yaml } = await getTemplate(name);
    setEditor({mode: "edit", name, yaml});
  }
  async function handleDelete(name: string) {
    if (!confirm(`Delete template ${name}?`)) return;
    await deleteTemplate(name);
    refresh();
  }

  return (
    <div>
      <header><h2>Subagents</h2><button onClick={() => setEditor({mode: "create"})}>+ New template</button></header>
      <div className="grid">
        {rows.map(r => (
          <div key={r.name} className={`card ${r.status === "degraded" ? "degraded" : ""}`}>
            <h3>{r.name}</h3>
            <div className="meta">model: {r.model} · tools: {r.tool_count} · mcp: {r.mcp_count}</div>
            <p>{r.description}</p>
            {r.status === "degraded" && <div className="error">{r.last_error}</div>}
            <button onClick={() => startEdit(r.name)}>Edit</button>
            <button onClick={() => handleDelete(r.name)}>Delete</button>
          </div>
        ))}
      </div>
      {editor && (
        <SubagentTemplateEditor
          mode={editor.mode} initialName={editor.name} initialYaml={editor.yaml}
          onClose={() => setEditor(null)}
          onSaved={() => { setEditor(null); refresh(); }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 6: Mount in sidebar/router**

Add a `"Subagents"` entry in `Sidebar.tsx` that routes to `SubagentsTab`.

- [ ] **Step 7: Run dashboard tests**

Run: `cd kc-dashboard && npm test`
Expected: ALL pass.

- [ ] **Step 8: Commit**

```bash
git add kc-dashboard/src/api/subagents.ts kc-dashboard/src/pages/SubagentsTab.tsx kc-dashboard/src/components/SubagentTemplateEditor.tsx kc-dashboard/src/components/Sidebar.tsx kc-dashboard/tests/api/subagents.test.ts kc-dashboard/tests/components/SubagentTemplateEditor.test.tsx
git commit -m "feat(kc-dashboard): tab 09 — templates list + editor modal (Subagents Task 18)"
```

---

## Task 19: Dashboard — active runs panel + inline trace block + approval badge

**Files:**
- Create: `kc-dashboard/src/components/SubagentActiveRunsPanel.tsx`
- Create: `kc-dashboard/src/components/SubagentTraceBlock.tsx`
- Modify: `kc-dashboard/src/components/ChatTranscript.tsx`
- Modify: `kc-dashboard/src/components/ApprovalCard.tsx`
- Modify: `kc-dashboard/src/lib/ws.ts` (add the four new frame types to `ChatEvent` union)
- Create: `kc-dashboard/tests/components/SubagentTraceBlock.test.tsx`

- [ ] **Step 1: Type the new frame shapes**

In `kc-dashboard/src/lib/ws.ts`, extend the `ChatEvent` union:

```ts
export type SubagentStarted  = { type: "subagent_started";  subagent_id: string; template: string; label?: string | null; task_preview: string; ts?: string };
export type SubagentTool     = { type: "subagent_tool";     subagent_id: string; tool: string; args_preview?: string; result_preview?: string; tier: string; ts?: string };
export type SubagentApproval = { type: "subagent_approval"; subagent_id: string; approval_id: string; tool: string; args_preview?: string; attributed_to: string; ts?: string };
export type SubagentFinished = { type: "subagent_finished"; subagent_id: string; status: "ok" | "error" | "timeout" | "stopped" | "interrupted"; reply_preview: string; duration_ms: number; tool_calls_used: number; error_message?: string | null; ts?: string };
export type ChatEvent = /* existing variants */ | SubagentStarted | SubagentTool | SubagentApproval | SubagentFinished;
```

- [ ] **Step 2: Write failing test for the trace block**

```tsx
// kc-dashboard/tests/components/SubagentTraceBlock.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SubagentTraceBlock } from "../../src/components/SubagentTraceBlock";

describe("SubagentTraceBlock", () => {
  it("shows running header + tool rows + finished reply", () => {
    render(
      <SubagentTraceBlock
        startedFrame={{ type: "subagent_started", subagent_id: "ep_a", template: "web-researcher", label: "berlin", task_preview: "weather" }}
        toolFrames={[{ type: "subagent_tool", subagent_id: "ep_a", tool: "web_search", tier: "SAFE" }]}
        approvalFrames={[]}
        finishedFrame={{ type: "subagent_finished", subagent_id: "ep_a", status: "ok", reply_preview: "...sunny...", duration_ms: 1200, tool_calls_used: 1 }}
        onStop={() => {}}
      />
    );
    expect(screen.getByText(/web-researcher/)).toBeInTheDocument();
    expect(screen.getByText(/web_search/)).toBeInTheDocument();
    expect(screen.getByText(/sunny/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Implement `SubagentTraceBlock`**

```tsx
// kc-dashboard/src/components/SubagentTraceBlock.tsx
import React, { useState } from "react";
import { SubagentStarted, SubagentTool, SubagentApproval, SubagentFinished } from "../lib/ws";

type Props = {
  startedFrame: SubagentStarted;
  toolFrames: SubagentTool[];
  approvalFrames: SubagentApproval[];
  finishedFrame: SubagentFinished | null;
  onStop: () => void;
};

const ICON: Record<string,string> = {
  ok: "✓", error: "⚠", timeout: "⏱", stopped: "⏹", interrupted: "⚡",
};

export function SubagentTraceBlock({ startedFrame, toolFrames, approvalFrames, finishedFrame, onStop }: Props) {
  const [expanded, setExpanded] = useState(true);
  const isDone = !!finishedFrame;
  const status  = finishedFrame?.status;
  const headerLabel = `subagent: ${startedFrame.template}${startedFrame.label ? ` · ${startedFrame.label}` : ""}`;

  return (
    <section className="subagent-trace" aria-label={`Subagent trace ${startedFrame.subagent_id}`}>
      <header onClick={() => setExpanded(e => !e)}>
        <span className="caret">{expanded ? "▾" : "▸"}</span>
        <span>{headerLabel}</span>
        <span className="status">
          {isDone
            ? `${ICON[status!] ?? ""} ${status} · ${toolFrames.length} tools · ${(finishedFrame!.duration_ms/1000).toFixed(1)}s`
            : `running`}
        </span>
        {!isDone && <button onClick={(e) => { e.stopPropagation(); onStop(); }}>⏹ Stop</button>}
      </header>
      {expanded && (
        <div className="body">
          {toolFrames.map((f, i) => (
            <div key={i} className="tool-row">
              <code>{f.tool}</code>
              {f.args_preview && <span className="args">{f.args_preview}</span>}
              {f.result_preview && <span className="result">→ {f.result_preview}</span>}
            </div>
          ))}
          {approvalFrames.map((f, i) => (
            <div key={`a${i}`} className="approval-pointer">
              Approval requested for <code>{f.tool}</code> ({f.approval_id})
            </div>
          ))}
          {finishedFrame && (
            <div className={`reply ${status}`}>
              {finishedFrame.status === "ok"
                ? finishedFrame.reply_preview
                : (finishedFrame.error_message || finishedFrame.status)}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Wire `ChatTranscript` to group and render trace frames**

In `ChatTranscript.tsx`, maintain a `Map<subagent_id, { started, tools, approvals, finished }>` as you walk the message stream; render a `<SubagentTraceBlock>` at the position of the `subagent_started` frame. Wire the Stop button to:

```ts
ws.send(JSON.stringify({ type: "subagent_stop", subagent_id }));
```

- [ ] **Step 5: Add "via subagent" badge to `ApprovalCard`**

In `ApprovalCard.tsx`, if `subagent_id` is present on the request payload:

```tsx
{request.subagent_id && (
  <span className="badge subagent-badge" title={`Requested by subagent ${request.subagent_id} (child of ${request.parent_agent})`}>
    via subagent
  </span>
)}
```

- [ ] **Step 6: Active runs panel**

```tsx
// kc-dashboard/src/components/SubagentActiveRunsPanel.tsx
import React, { useEffect, useState } from "react";

type ActiveRun = { subagent_id: string; template: string; label: string | null; parent_conversation_id: string; tool_calls_used: number };

export function SubagentActiveRunsPanel() {
  const [rows, setRows] = useState<ActiveRun[]>([]);
  useEffect(() => {
    const t = setInterval(async () => {
      const r = await fetch("/subagents/active"); setRows(await r.json());
    }, 1500);
    return () => clearInterval(t);
  }, []);
  async function stop(sid: string) { await fetch(`/subagents/${sid}/stop`, { method: "POST" }); }
  return (
    <div className="active-runs">
      {rows.map(r => (
        <div key={r.subagent_id} className="row">
          <code>{r.subagent_id}</code> · {r.template} · {r.label ?? ""} · {r.tool_calls_used} tools
          <button onClick={() => stop(r.subagent_id)}>Stop</button>
        </div>
      ))}
    </div>
  );
}
```

Mount it on the Subagents tab page next to the templates grid.

- [ ] **Step 7: Run dashboard tests**

Run: `cd kc-dashboard && npm test`
Expected: ALL pass.

- [ ] **Step 8: Commit**

```bash
git add kc-dashboard/src/components/SubagentTraceBlock.tsx kc-dashboard/src/components/SubagentActiveRunsPanel.tsx kc-dashboard/src/components/ChatTranscript.tsx kc-dashboard/src/components/ApprovalCard.tsx kc-dashboard/src/lib/ws.ts kc-dashboard/tests/components/SubagentTraceBlock.test.tsx
git commit -m "feat(kc-dashboard): inline trace block + active runs panel + approval badge (Subagents Task 19)"
```

---

## Task 20: SMOKE doc

**Files:**
- Create: `docs/superpowers/specs/2026-05-11-subagents-SMOKE.md`

- [ ] **Step 1: Write SMOKE doc with the 9 gates from spec §14.4**

```markdown
# Subagents SMOKE Gates

Run these manually after merging to main with `KC_SUBAGENTS_ENABLED=true`. Mark each PASS/FAIL with the date + commit SHA.

## Gate 1 — Authoring round-trip
- Open Subagents tab; click "+ New template".
- Save a minimal template (name: `gate1-test`, model: `claude-opus-4-7`, system_prompt: "say hi").
- Verify `~/KonaClaw/subagent-templates/gate1-test.yaml` exists.
- Verify Kona's tool schema now includes `gate1-test` as a known template (query: "what subagent templates do you have available?").

## Gate 2 — Inline trace rendering
- Ask Kona "spawn the `web-researcher` subagent to find the current weather in Berlin."
- Verify a `▾ subagent: web-researcher · ...` block renders in the chat transcript.
- Verify tool calls (`web_search`, `web_fetch`) appear as rows inside the block.
- Verify final reply appears as a child bubble inside the block on completion.

## Gate 3 — Attributed approval
- Ask Kona "spawn the `coder` subagent to list files in `/tmp`."
- When the `terminal_run` approval card appears, verify it shows the "via subagent" badge.
- Verify the card's title reads `"coder (ep_..., child of Kona-AI)"`.
- Approve; verify the call runs and the trace block updates.

## Gate 4 — Parallel spawn + await
- Ask Kona "spawn three `web-researcher` instances in parallel and tell me the weather in Berlin, Tokyo, and NYC."
- Verify three trace blocks render side by side and all complete.
- Verify Kona's final reply contains all three answers.
- Query `~/KonaClaw/data/konaclaw.db` (or wherever audit lives): `SELECT subagent_id, template, status, duration_ms FROM subagent_runs ORDER BY started_ts DESC LIMIT 3;` — three rows, all `status=ok`.

## Gate 5 — Stop button
- Ask Kona to spawn a long-running task; immediately click the Stop button on the trace block.
- Verify the block transitions to `⏹ stopped` within ~2s.
- Verify the corresponding `subagent_runs` row reflects `status=stopped`.

## Gate 6 — Timeout
- Author a template with `timeout_seconds: 10` and a system prompt that loops on tool calls.
- Spawn it; verify the block transitions to `⏱ timeout` after ~10s.
- Verify the row reflects `status=timeout`.

## Gate 7 — max_tool_calls cap
- Author a template with `max_tool_calls: 2` and a system prompt that wants 10 calls.
- Spawn it; verify the run finalizes after 2 tool calls with a non-empty reply that mentions the cap.

## Gate 8 — Seed templates end-to-end
For each of the four seed templates, run a representative prompt:
- `web-researcher`: "what's the weather in Berlin today?"
- `coder`: "in `/tmp/foo`, create a small bash script that prints the date." (approves attributed terminal_run)
- `email-drafter`: "draft a reply to my most recent email from mom." (requires Zapier Gmail enabled)
- `scheduler`: "schedule a 30-minute walk with mom tomorrow afternoon." (clarify exercised)

## Gate 9 — Restart resilience
- Spawn a long-running subagent; immediately `Ctrl-C` the supervisor.
- Restart; query `subagent_runs` for that id — `status=interrupted`, `error_message` mentions restart.
- Verify no zombie state: the dashboard's active runs panel is empty after restart.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-11-subagents-SMOKE.md
git commit -m "docs(smoke): subagents — 9 manual gates (Subagents Task 20)"
```

---

## Final integration check

After all 20 tasks land, run from the repo root:

```bash
python -m pytest kc-subagents/ kc-supervisor/tests/ kc-sandbox/tests/ -v
cd kc-dashboard && npm test
```

Both must be green. Then verify the gate-list at `docs/superpowers/specs/2026-05-11-subagents-SMOKE.md` is ready for manual run.

Flip `KC_SUBAGENTS_ENABLED=true` in `~/.konaclaw.env`, restart the supervisor, and step through SMOKE.
