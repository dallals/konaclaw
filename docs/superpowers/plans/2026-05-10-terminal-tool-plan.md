# Terminal Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `terminal_run` agent tool that executes shell commands on the host with multi-root cwd validation, tier-by-command approval gating, KonaClaw-secret env stripping, and sync capture with timeout. Phase A of the post-Skills tools rollout.

**Architecture:** New top-level `kc-terminal` package mirroring `kc-skills`. Lazy-imported by `kc_supervisor.assembly` and gated by `KC_TERMINAL_ENABLED`. Classifier outputs a 3-tier label (`SAFE` / `MUTATING` / `DESTRUCTIVE`) recorded in audit JSON; supervisor maps both `MUTATING` and `DESTRUCTIVE` to the engine's `Tier.DESTRUCTIVE` so they both invoke `ApprovalBroker.request_approval`. A new `tier_resolvers` field on `PermissionEngine` makes per-call dynamic tier resolution possible without changing static-tier semantics for other tools.

**Tech Stack:** Python 3.11+, `hatchling` build, `pytest` + `pytest-asyncio`, `subprocess.run` via `asyncio.to_thread`, existing `kc_sandbox.permissions` and `kc_supervisor.approvals` machinery.

**Spec:** `docs/superpowers/specs/2026-05-10-terminal-tool-design.md` (commit `ebd3bf0`).

---

## File Structure

**New files (kc-terminal package):**
- `kc-terminal/pyproject.toml` — package metadata, deps on `kc-core` and (test) `pytest-asyncio`.
- `kc-terminal/src/kc_terminal/__init__.py` — re-exports `build_terminal_tool`, `TerminalConfig`.
- `kc-terminal/src/kc_terminal/config.py` — `TerminalConfig` dataclass, defaults, `from_env()` factory.
- `kc-terminal/src/kc_terminal/classifier.py` — `classify_argv(argv) -> RawTier`, `classify_command(cmd) -> RawTier`.
- `kc-terminal/src/kc_terminal/paths.py` — `validate_cwd(cwd_str, roots) -> Path` + exception types.
- `kc-terminal/src/kc_terminal/env.py` — `build_child_env(parent, secret_prefixes) -> dict[str,str]`.
- `kc-terminal/src/kc_terminal/runner.py` — `run(...)` sync subprocess + truncation + timeout.
- `kc-terminal/src/kc_terminal/tools.py` — `build_terminal_tool(cfg, approval_broker)` factory returning a `kc_core.tools.Tool`.
- `kc-terminal/tests/__init__.py`
- `kc-terminal/tests/test_classifier.py`
- `kc-terminal/tests/test_paths.py`
- `kc-terminal/tests/test_env.py`
- `kc-terminal/tests/test_runner.py`
- `kc-terminal/tests/test_tool_integration.py`

**Modified files (cross-package):**
- `kc-sandbox/src/kc_sandbox/permissions.py` — add `tier_resolvers: dict[str, Callable[[dict], Tier]]` field on `PermissionEngine`, consulted before `tier_map` lookup.
- `kc-sandbox/tests/test_permissions.py` — add tests for `tier_resolvers`.
- `kc-supervisor/src/kc_supervisor/assembly.py` — lazy-import `kc_terminal`, build config, register `terminal_run` tool, install resolver on `PermissionEngine` when `KC_TERMINAL_ENABLED=true`.
- `kc-supervisor/tests/test_assembly.py` — add registration test (present iff enabled).

**New doc:**
- `docs/superpowers/specs/2026-05-10-terminal-tool-SMOKE.md` — manual smoke checklist for post-merge verification.

---

## Task 1: Bootstrap kc-terminal package

**Files:**
- Create: `kc-terminal/pyproject.toml`
- Create: `kc-terminal/src/kc_terminal/__init__.py`
- Create: `kc-terminal/tests/__init__.py`
- Create: `kc-terminal/.gitignore`

- [ ] **Step 1: Create `kc-terminal/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-terminal"
version = "0.1.0"
description = "KonaClaw terminal_run tool (Phase A of tools rollout)"
requires-python = ">=3.11"
dependencies = ["kc-core", "kc-sandbox"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.hatch.build.targets.wheel]
packages = ["src/kc_terminal"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"
asyncio_mode = "auto"
```

- [ ] **Step 2: Create empty `kc-terminal/src/kc_terminal/__init__.py`**

```python
"""KonaClaw terminal_run tool."""
```

- [ ] **Step 3: Create empty `kc-terminal/tests/__init__.py`**

(zero-byte file)

- [ ] **Step 4: Create `kc-terminal/.gitignore`**

```
__pycache__/
.pytest_cache/
.venv/
*.egg-info/
dist/
build/
```

- [ ] **Step 5: Verify package layout exists**

Run: `find kc-terminal -type f | sort`

Expected output:
```
kc-terminal/.gitignore
kc-terminal/pyproject.toml
kc-terminal/src/kc_terminal/__init__.py
kc-terminal/tests/__init__.py
```

- [ ] **Step 6: Install editable into kc-supervisor venv so later tasks can run tests**

Run from repo root:
```bash
cd kc-supervisor && uv pip install -e ../kc-terminal --extra dev
```

Expected: `Successfully installed kc-terminal-0.1.0`.

- [ ] **Step 7: Commit**

```bash
git add kc-terminal/
git commit -m "feat(kc-terminal): bootstrap package scaffolding"
```

---

## Task 2: classifier — argv mode (basic command sets)

**Files:**
- Create: `kc-terminal/src/kc_terminal/classifier.py`
- Test: `kc-terminal/tests/test_classifier.py`

- [ ] **Step 1: Write failing tests for `classify_argv` basic command sets**

Create `kc-terminal/tests/test_classifier.py`:

```python
import pytest
from kc_terminal.classifier import (
    classify_argv,
    classify_command,
    RawTier,
    BadArgvError,
)


@pytest.mark.parametrize("argv,expected", [
    (["ls"], RawTier.SAFE),
    (["ls", "-la"], RawTier.SAFE),
    (["cat", "/etc/hosts"], RawTier.SAFE),
    (["grep", "foo", "bar.txt"], RawTier.SAFE),
    (["pwd"], RawTier.SAFE),
    (["echo", "hi"], RawTier.SAFE),
    (["env"], RawTier.SAFE),
])
def test_argv_safe_commands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["rm", "-rf", "x"], RawTier.DESTRUCTIVE),
    (["sudo", "ls"], RawTier.DESTRUCTIVE),
    (["curl", "https://x"], RawTier.DESTRUCTIVE),
    (["wget", "https://x"], RawTier.DESTRUCTIVE),
    (["ssh", "host"], RawTier.DESTRUCTIVE),
    (["chmod", "+x", "f"], RawTier.DESTRUCTIVE),
    (["mv", "a", "b"], RawTier.DESTRUCTIVE),
    (["cp", "a", "b"], RawTier.DESTRUCTIVE),
])
def test_argv_destructive_commands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["python", "-c", "print(1)"], RawTier.MUTATING),
    (["python3", "-c", "print(1)"], RawTier.MUTATING),
    (["node", "-e", "1"], RawTier.MUTATING),
    (["npm", "install"], RawTier.MUTATING),
    (["pip", "install", "x"], RawTier.MUTATING),
    (["pytest"], RawTier.MUTATING),
    (["make", "build"], RawTier.MUTATING),
    (["docker", "ps"], RawTier.MUTATING),
    (["some-unknown-tool"], RawTier.MUTATING),
])
def test_argv_default_mutating(argv, expected):
    assert classify_argv(argv) == expected


def test_argv_normalizes_basename():
    assert classify_argv(["/usr/bin/ls"]) == RawTier.SAFE
    assert classify_argv(["/usr/local/bin/rm", "x"]) == RawTier.DESTRUCTIVE


def test_argv_empty_raises():
    with pytest.raises(BadArgvError):
        classify_argv([])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_classifier.py -v`

Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement `classify_argv` basics**

Create `kc-terminal/src/kc_terminal/classifier.py`:

```python
from __future__ import annotations
from enum import Enum
from pathlib import PurePath


class RawTier(str, Enum):
    SAFE = "SAFE"
    MUTATING = "MUTATING"
    DESTRUCTIVE = "DESTRUCTIVE"


class BadArgvError(ValueError):
    pass


SAFE_COMMANDS = frozenset({
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "file",
    "stat", "tree", "echo", "which", "whereis", "type", "env", "printenv",
    "date", "uname", "hostname", "uptime", "ps", "id", "true", "false",
})

DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp",
    "sudo", "su", "doas",
    "kill", "killall", "pkill",
    "curl", "wget",
    "ssh", "scp", "rsync",
    "dd", "mkfs", "fdisk",
    "chmod", "chown", "chgrp",
    "shutdown", "reboot", "halt",
})


def _basename(arg0: str) -> str:
    return PurePath(arg0).name


def classify_argv(argv: list[str]) -> RawTier:
    if not argv:
        raise BadArgvError("empty argv")
    cmd = _basename(argv[0])
    if cmd in DESTRUCTIVE_COMMANDS:
        return RawTier.DESTRUCTIVE
    if cmd in SAFE_COMMANDS:
        return RawTier.SAFE
    return RawTier.MUTATING


def classify_command(command: str) -> RawTier:
    raise NotImplementedError  # filled in Task 4
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_classifier.py -v -k "not classify_command"`

Expected: all `test_argv_*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/classifier.py kc-terminal/tests/test_classifier.py
git commit -m "feat(kc-terminal): classify_argv with SAFE/DESTRUCTIVE/MUTATING command sets"
```

---

## Task 3: classifier — git and gh subrules

**Files:**
- Modify: `kc-terminal/src/kc_terminal/classifier.py`
- Modify: `kc-terminal/tests/test_classifier.py`

- [ ] **Step 1: Append failing tests for git/gh subrules**

Append to `kc-terminal/tests/test_classifier.py`:

```python
@pytest.mark.parametrize("argv,expected", [
    (["git", "status"], RawTier.SAFE),
    (["git", "log", "--oneline"], RawTier.SAFE),
    (["git", "diff"], RawTier.SAFE),
    (["git", "show", "HEAD"], RawTier.SAFE),
    (["git", "blame", "f"], RawTier.SAFE),
    (["git", "branch"], RawTier.SAFE),
    (["git", "remote", "-v"], RawTier.SAFE),
    (["git", "rev-parse", "HEAD"], RawTier.SAFE),
    (["git", "describe"], RawTier.SAFE),
    (["git", "fetch"], RawTier.SAFE),
    (["git", "ls-files"], RawTier.SAFE),
])
def test_argv_git_safe_subcommands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["git", "push"], RawTier.DESTRUCTIVE),
    (["git", "push", "origin", "main"], RawTier.DESTRUCTIVE),
    (["git", "filter-repo", "--invert-paths"], RawTier.DESTRUCTIVE),
    (["git", "reset", "--hard", "HEAD"], RawTier.DESTRUCTIVE),
    (["git", "clean", "-fd"], RawTier.DESTRUCTIVE),
    (["git", "branch", "-D", "x"], RawTier.DESTRUCTIVE),
    (["git", "tag", "-d", "v1"], RawTier.DESTRUCTIVE),
])
def test_argv_git_destructive_subcommands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["git", "commit", "-m", "x"], RawTier.MUTATING),
    (["git", "checkout", "main"], RawTier.MUTATING),
    (["git", "merge", "x"], RawTier.MUTATING),
    (["git", "pull"], RawTier.MUTATING),
    (["git", "add", "."], RawTier.MUTATING),
    (["git", "stash"], RawTier.MUTATING),
])
def test_argv_git_default_mutating(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "repo", "view"], RawTier.SAFE),
    (["gh", "repo", "list"], RawTier.SAFE),
    (["gh", "pr", "view", "123"], RawTier.SAFE),
    (["gh", "pr", "list"], RawTier.SAFE),
    (["gh", "pr", "status"], RawTier.SAFE),
    (["gh", "pr", "diff", "123"], RawTier.SAFE),
    (["gh", "issue", "view", "1"], RawTier.SAFE),
    (["gh", "issue", "list"], RawTier.SAFE),
    (["gh", "issue", "status"], RawTier.SAFE),
    (["gh", "run", "view", "42"], RawTier.SAFE),
    (["gh", "run", "list"], RawTier.SAFE),
])
def test_argv_gh_safe_pairs(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "pr", "merge", "123"], RawTier.DESTRUCTIVE),
    (["gh", "pr", "close", "123"], RawTier.DESTRUCTIVE),
    (["gh", "repo", "delete", "x"], RawTier.DESTRUCTIVE),
    (["gh", "secret", "delete", "FOO"], RawTier.DESTRUCTIVE),
    (["gh", "release", "delete", "v1"], RawTier.DESTRUCTIVE),
])
def test_argv_gh_destructive_pairs(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "pr", "create"], RawTier.MUTATING),
    (["gh", "issue", "create"], RawTier.MUTATING),
    (["gh", "api", "/repos/x/y"], RawTier.MUTATING),
    (["gh", "auth", "status"], RawTier.MUTATING),
])
def test_argv_gh_default_mutating(argv, expected):
    assert classify_argv(argv) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_classifier.py -v -k "git_safe or git_dest or git_default or gh_safe or gh_dest or gh_default"`

Expected: failures (current `classify_argv` returns MUTATING for `git` since it's not in either set).

- [ ] **Step 3: Implement git/gh subrules**

Replace `classify_argv` and add helpers in `kc-terminal/src/kc_terminal/classifier.py`:

```python
GIT_SAFE_SUBCMDS = frozenset({
    "status", "log", "diff", "show", "blame", "branch", "remote",
    "rev-parse", "describe", "fetch", "ls-files",
})

GIT_DESTRUCTIVE_SUBCMDS = frozenset({
    "push", "filter-repo",
})

GH_SAFE_PAIRS = frozenset({
    ("repo", "view"),  ("repo", "list"),
    ("pr",   "view"),  ("pr",   "list"),  ("pr", "status"), ("pr", "diff"),
    ("issue","view"),  ("issue","list"),  ("issue", "status"),
    ("run",  "view"),  ("run",  "list"),
})

GH_DESTRUCTIVE_PAIRS = frozenset({
    ("pr", "merge"), ("pr", "close"),
    ("repo", "delete"),
    ("secret", "delete"), ("release", "delete"),
})


def _classify_git(argv: list[str]) -> RawTier:
    # argv[0] is "git" (already basenamed by caller).
    if len(argv) < 2:
        return RawTier.MUTATING
    sub = argv[1]
    if sub in GIT_DESTRUCTIVE_SUBCMDS:
        return RawTier.DESTRUCTIVE
    if sub in GIT_SAFE_SUBCMDS:
        # `git branch -D x` is destructive even though `branch` is in SAFE.
        if sub == "branch" and any(a == "-D" for a in argv[2:]):
            return RawTier.DESTRUCTIVE
        return RawTier.SAFE
    # `git reset --hard ...` -> destructive
    if sub == "reset" and any(a == "--hard" for a in argv[2:]):
        return RawTier.DESTRUCTIVE
    # `git clean -fd` / `-f` -> destructive
    if sub == "clean" and any(a.startswith("-") and "f" in a for a in argv[2:]):
        return RawTier.DESTRUCTIVE
    # `git tag -d x` -> destructive
    if sub == "tag" and any(a == "-d" for a in argv[2:]):
        return RawTier.DESTRUCTIVE
    return RawTier.MUTATING


def _classify_gh(argv: list[str]) -> RawTier:
    if len(argv) < 3:
        return RawTier.MUTATING
    pair = (argv[1], argv[2])
    if pair in GH_DESTRUCTIVE_PAIRS:
        return RawTier.DESTRUCTIVE
    if pair in GH_SAFE_PAIRS:
        return RawTier.SAFE
    return RawTier.MUTATING


def classify_argv(argv: list[str]) -> RawTier:
    if not argv:
        raise BadArgvError("empty argv")
    cmd = _basename(argv[0])
    if cmd == "git":
        return _classify_git([cmd, *argv[1:]])
    if cmd == "gh":
        return _classify_gh([cmd, *argv[1:]])
    if cmd in DESTRUCTIVE_COMMANDS:
        return RawTier.DESTRUCTIVE
    if cmd in SAFE_COMMANDS:
        return RawTier.SAFE
    return RawTier.MUTATING
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_classifier.py -v -k "not classify_command"`

Expected: all argv tests pass (both basic and git/gh).

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/classifier.py kc-terminal/tests/test_classifier.py
git commit -m "feat(kc-terminal): git/gh subcommand-aware tier classification"
```

---

## Task 4: classifier — shell command mode

**Files:**
- Modify: `kc-terminal/src/kc_terminal/classifier.py`
- Modify: `kc-terminal/tests/test_classifier.py`

- [ ] **Step 1: Append failing tests for `classify_command`**

Append to `kc-terminal/tests/test_classifier.py`:

```python
@pytest.mark.parametrize("cmd,expected", [
    # Pipes/conjunctions: walk every segment; strictest wins.
    ("ls | grep foo",                       RawTier.MUTATING),
    ("ls -la && pwd",                       RawTier.MUTATING),
    ("echo hi ; echo bye",                  RawTier.MUTATING),
    # Shell is never SAFE, even with safe-only argv.
    ("ls",                                  RawTier.MUTATING),
    ("git status",                          RawTier.MUTATING),
    # Destructive tokens.
    ("rm -rf x",                            RawTier.DESTRUCTIVE),
    ("ls | xargs rm",                       RawTier.DESTRUCTIVE),
    ("echo hi; rm -rf ~",                   RawTier.DESTRUCTIVE),
    ("curl https://x",                      RawTier.DESTRUCTIVE),
    ("ls > out.txt",                        RawTier.DESTRUCTIVE),
    ("ls >> out.txt",                       RawTier.DESTRUCTIVE),
    ("git push origin main",                RawTier.DESTRUCTIVE),
    ("echo hi | tee file.txt",              RawTier.DESTRUCTIVE),
    ("echo hi | tee /dev/null",             RawTier.MUTATING),  # /dev/null is allowed
    ("sudo ls",                             RawTier.DESTRUCTIVE),
])
def test_classify_command_table(cmd, expected):
    assert classify_command(cmd) == expected


def test_classify_command_empty_raises():
    with pytest.raises(BadArgvError):
        classify_command("")
    with pytest.raises(BadArgvError):
        classify_command("   ")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_classifier.py::test_classify_command_table -v`

Expected: failures (current `classify_command` raises `NotImplementedError`).

- [ ] **Step 3: Implement `classify_command`**

Replace the `classify_command` stub in `kc-terminal/src/kc_terminal/classifier.py`:

```python
import shlex

# Tokens that, on their own, mean "redirecting output to a real file" - DESTRUCTIVE.
_REDIRECT_TOKENS = frozenset({">", ">>", ">|"})

# Shell separators we split sub-commands on (so we can tier each segment).
_SEGMENT_SEPS = frozenset({"|", "||", "&&", ";", "&"})


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _SEGMENT_SEPS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _segment_tier(segment: list[str]) -> RawTier:
    if not segment:
        return RawTier.MUTATING
    # Redirect operators inside a segment -> DESTRUCTIVE (unless to /dev/null).
    for i, tok in enumerate(segment):
        if tok in _REDIRECT_TOKENS:
            target = segment[i + 1] if i + 1 < len(segment) else ""
            if target != "/dev/null":
                return RawTier.DESTRUCTIVE
    # `tee` writing to a non-/dev/null path -> DESTRUCTIVE.
    if "tee" in segment:
        tee_idx = segment.index("tee")
        # Find first non-flag arg after tee.
        target = next(
            (a for a in segment[tee_idx + 1 :] if not a.startswith("-")),
            None,
        )
        if target is not None and target != "/dev/null":
            return RawTier.DESTRUCTIVE
    # Any DESTRUCTIVE command token in the segment.
    for tok in segment:
        cmd = _basename(tok)
        if cmd in DESTRUCTIVE_COMMANDS:
            return RawTier.DESTRUCTIVE
    # Adjacent `git push` -> DESTRUCTIVE.
    for i in range(len(segment) - 1):
        if _basename(segment[i]) == "git" and segment[i + 1] == "push":
            return RawTier.DESTRUCTIVE
    # Shell mode is never SAFE.
    return RawTier.MUTATING


def classify_command(command: str) -> RawTier:
    if not command or not command.strip():
        raise BadArgvError("empty command")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as e:
        # Unterminated quote etc. Be conservative.
        raise BadArgvError(f"unparseable command: {e}") from e
    segments = _split_segments(tokens)
    tier = RawTier.MUTATING
    for seg in segments:
        seg_tier = _segment_tier(seg)
        if seg_tier == RawTier.DESTRUCTIVE:
            return RawTier.DESTRUCTIVE
        if seg_tier == RawTier.MUTATING:
            tier = RawTier.MUTATING
    return tier
```

Note: `shlex.split` does NOT treat `|`, `&&`, `;`, `>` as token delimiters — they come back as their own tokens. The `_split_segments` walk is what gives us per-segment analysis.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_classifier.py -v`

Expected: all classifier tests pass (argv + command + empty cases).

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/classifier.py kc-terminal/tests/test_classifier.py
git commit -m "feat(kc-terminal): shell-command tier classifier with segment walk"
```

---

## Task 5: paths — validate_cwd

**Files:**
- Create: `kc-terminal/src/kc_terminal/paths.py`
- Test: `kc-terminal/tests/test_paths.py`

- [ ] **Step 1: Write failing tests for `validate_cwd`**

Create `kc-terminal/tests/test_paths.py`:

```python
import pytest
from pathlib import Path
from kc_terminal.paths import (
    validate_cwd,
    CwdNotAbsolute,
    CwdDoesNotExist,
    CwdNotADirectory,
    CwdOutsideRoots,
)


@pytest.fixture
def workdir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "subdir"
    inside.mkdir()
    a_file = inside / "f.txt"
    a_file.write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    return {"root": root, "inside": inside, "file": a_file, "outside": outside, "tmp": tmp_path}


def test_inside_root_is_accepted(workdir):
    out = validate_cwd(str(workdir["inside"]), [workdir["root"]])
    assert out == workdir["inside"].resolve()


def test_root_itself_is_accepted(workdir):
    out = validate_cwd(str(workdir["root"]), [workdir["root"]])
    assert out == workdir["root"].resolve()


def test_relative_path_rejected(workdir):
    with pytest.raises(CwdNotAbsolute):
        validate_cwd("subdir", [workdir["root"]])


def test_nonexistent_path_rejected(workdir):
    missing = workdir["root"] / "no-such-dir"
    with pytest.raises(CwdDoesNotExist):
        validate_cwd(str(missing), [workdir["root"]])


def test_file_not_a_directory(workdir):
    with pytest.raises(CwdNotADirectory):
        validate_cwd(str(workdir["file"]), [workdir["root"]])


def test_outside_roots_rejected(workdir):
    with pytest.raises(CwdOutsideRoots):
        validate_cwd(str(workdir["outside"]), [workdir["root"]])


def test_symlink_to_outside_rejected(workdir):
    link = workdir["root"] / "escape"
    link.symlink_to(workdir["outside"])
    with pytest.raises(CwdOutsideRoots):
        validate_cwd(str(link), [workdir["root"]])


def test_symlink_to_inside_accepted(workdir):
    link = workdir["outside"] / "shortcut"
    link.symlink_to(workdir["inside"])
    out = validate_cwd(str(link), [workdir["root"]])
    assert out == workdir["inside"].resolve()


def test_multi_root_second_match(workdir, tmp_path):
    other = tmp_path / "other-root"
    other.mkdir()
    sub = other / "x"
    sub.mkdir()
    out = validate_cwd(str(sub), [workdir["root"], other])
    assert out == sub.resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_paths.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `validate_cwd`**

Create `kc-terminal/src/kc_terminal/paths.py`:

```python
from __future__ import annotations
from pathlib import Path


class CwdNotAbsolute(ValueError):
    pass


class CwdDoesNotExist(ValueError):
    pass


class CwdNotADirectory(ValueError):
    pass


class CwdOutsideRoots(ValueError):
    pass


def validate_cwd(cwd_str: str, roots: list[Path]) -> Path:
    p = Path(cwd_str)
    if not p.is_absolute():
        raise CwdNotAbsolute(cwd_str)
    try:
        p_resolved = p.resolve(strict=True)
    except FileNotFoundError as e:
        raise CwdDoesNotExist(cwd_str) from e
    if not p_resolved.is_dir():
        raise CwdNotADirectory(str(p_resolved))
    for root in roots:
        try:
            root_resolved = root.resolve(strict=True)
        except FileNotFoundError:
            continue  # missing root is just skipped
        if p_resolved == root_resolved or root_resolved in p_resolved.parents:
            return p_resolved
    raise CwdOutsideRoots(str(p_resolved))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_paths.py -v`

Expected: 9 passing.

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/paths.py kc-terminal/tests/test_paths.py
git commit -m "feat(kc-terminal): validate_cwd with symlink-following root check"
```

---

## Task 6: env — build_child_env

**Files:**
- Create: `kc-terminal/src/kc_terminal/env.py`
- Test: `kc-terminal/tests/test_env.py`

- [ ] **Step 1: Write failing tests for `build_child_env`**

Create `kc-terminal/tests/test_env.py`:

```python
from kc_terminal.env import build_child_env, DEFAULT_SECRET_PREFIXES


def test_strips_default_secret_prefixes():
    parent = {
        "ANTHROPIC_API_KEY": "secret",
        "SUPABASE_KEY": "secret",
        "KC_SKILL_DIR": "/tmp",
        "OPENAI_API_KEY": "secret",
        "TELEGRAM_BOT_TOKEN": "secret",
        "ZAPIER_NLA_KEY": "secret",
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
    }
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert "ANTHROPIC_API_KEY" not in out
    assert "SUPABASE_KEY" not in out
    assert "KC_SKILL_DIR" not in out
    assert "OPENAI_API_KEY" not in out
    assert "TELEGRAM_BOT_TOKEN" not in out
    assert "ZAPIER_NLA_KEY" not in out
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/Users/x"


def test_preserves_safe_vars():
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
        "USER": "x",
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "xterm",
        "TMPDIR": "/tmp",
        "SSH_AUTH_SOCK": "/x.sock",
        "AWS_ACCESS_KEY_ID": "AKIA...",
    }
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert out == parent


def test_preserves_github_token_by_name():
    parent = {"GITHUB_TOKEN": "ghp_xxx", "PATH": "/usr/bin"}
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert out["GITHUB_TOKEN"] == "ghp_xxx"


def test_empty_parent_yields_empty():
    assert build_child_env({}, DEFAULT_SECRET_PREFIXES) == {}


def test_case_sensitivity_documented():
    # Prefix match is exact (case-sensitive). Lower/mixed-case keys are preserved.
    parent = {"anthropic_lower": "x", "Anthropic_Mixed": "x", "ANTHROPIC_REAL": "secret"}
    out = build_child_env(parent, DEFAULT_SECRET_PREFIXES)
    assert "anthropic_lower" in out
    assert "Anthropic_Mixed" in out
    assert "ANTHROPIC_REAL" not in out


def test_custom_prefix_list():
    parent = {"MYAPP_KEY": "secret", "PATH": "/usr/bin"}
    out = build_child_env(parent, ("MYAPP_",))
    assert "MYAPP_KEY" not in out
    assert out["PATH"] == "/usr/bin"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_env.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `build_child_env`**

Create `kc-terminal/src/kc_terminal/env.py`:

```python
from __future__ import annotations
from typing import Iterable


DEFAULT_SECRET_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_", "OPENAI_", "DEEPSEEK_", "GROQ_",
    "SUPABASE_",  "KONA_",   "KC_",
    "GOOGLE_OAUTH_", "GCAL_", "GMAIL_",
    "TELEGRAM_BOT_TOKEN", "ZAPIER_",
    "STRIPE_",    "TWILIO_", "SENDGRID_",
)


def build_child_env(
    parent: dict[str, str],
    secret_prefixes: Iterable[str],
) -> dict[str, str]:
    prefixes = tuple(secret_prefixes)
    return {k: v for k, v in parent.items() if not any(k.startswith(p) for p in prefixes)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_env.py -v`

Expected: 6 passing.

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/env.py kc-terminal/tests/test_env.py
git commit -m "feat(kc-terminal): build_child_env with prefix-based secret stripping"
```

---

## Task 7: config — TerminalConfig

**Files:**
- Create: `kc-terminal/src/kc_terminal/config.py`
- Modify: `kc-terminal/tests/__init__.py` (no change needed; add a new test file)
- Test: `kc-terminal/tests/test_config.py`

- [ ] **Step 1: Write failing tests for `TerminalConfig`**

Create `kc-terminal/tests/test_config.py`:

```python
from pathlib import Path
from kc_terminal.config import TerminalConfig


def test_defaults():
    cfg = TerminalConfig.with_defaults()
    assert cfg.max_timeout_seconds == 600
    assert cfg.default_timeout_seconds == 60
    assert cfg.output_cap_bytes == 128 * 1024
    assert any("KonaClaw" in str(r) for r in cfg.roots)
    assert "ANTHROPIC_" in cfg.secret_prefixes


def test_from_env_overrides(monkeypatch, tmp_path):
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir(); r2.mkdir()
    monkeypatch.setenv("KC_TERMINAL_ROOTS", f"{r1}:{r2}")
    monkeypatch.setenv("KC_TERMINAL_DEFAULT_TIMEOUT", "30")
    monkeypatch.setenv("KC_TERMINAL_MAX_TIMEOUT", "120")
    monkeypatch.setenv("KC_TERMINAL_OUTPUT_CAP_BYTES", "2048")
    cfg = TerminalConfig.from_env()
    assert cfg.roots == [r1, r2]
    assert cfg.default_timeout_seconds == 30
    assert cfg.max_timeout_seconds == 120
    assert cfg.output_cap_bytes == 2048


def test_from_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.delenv("KC_TERMINAL_ROOTS", raising=False)
    monkeypatch.delenv("KC_TERMINAL_DEFAULT_TIMEOUT", raising=False)
    cfg = TerminalConfig.from_env()
    assert cfg.default_timeout_seconds == 60
    assert cfg.max_timeout_seconds == 600


def test_clamp_timeout():
    cfg = TerminalConfig.with_defaults()
    assert cfg.clamp_timeout(None) == cfg.default_timeout_seconds
    assert cfg.clamp_timeout(0) == 1
    assert cfg.clamp_timeout(-5) == 1
    assert cfg.clamp_timeout(10_000) == cfg.max_timeout_seconds
    assert cfg.clamp_timeout(45) == 45
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_config.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `TerminalConfig`**

Create `kc-terminal/src/kc_terminal/config.py`:

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from kc_terminal.env import DEFAULT_SECRET_PREFIXES


def _default_roots() -> list[Path]:
    home = Path.home()
    return [
        home / "KonaClaw",
        home / "Desktop" / "claudeCode" / "SammyClaw",
    ]


@dataclass(frozen=True)
class TerminalConfig:
    roots: list[Path]
    secret_prefixes: tuple[str, ...]
    default_timeout_seconds: int
    max_timeout_seconds: int
    output_cap_bytes: int

    @classmethod
    def with_defaults(cls) -> "TerminalConfig":
        return cls(
            roots=_default_roots(),
            secret_prefixes=DEFAULT_SECRET_PREFIXES,
            default_timeout_seconds=60,
            max_timeout_seconds=600,
            output_cap_bytes=128 * 1024,
        )

    @classmethod
    def from_env(cls) -> "TerminalConfig":
        base = cls.with_defaults()
        roots_raw = os.environ.get("KC_TERMINAL_ROOTS")
        roots = [Path(p) for p in roots_raw.split(":") if p] if roots_raw else base.roots
        default_to = int(os.environ.get("KC_TERMINAL_DEFAULT_TIMEOUT", base.default_timeout_seconds))
        max_to = int(os.environ.get("KC_TERMINAL_MAX_TIMEOUT", base.max_timeout_seconds))
        cap = int(os.environ.get("KC_TERMINAL_OUTPUT_CAP_BYTES", base.output_cap_bytes))
        return cls(
            roots=roots,
            secret_prefixes=base.secret_prefixes,
            default_timeout_seconds=default_to,
            max_timeout_seconds=max_to,
            output_cap_bytes=cap,
        )

    def clamp_timeout(self, requested: int | None) -> int:
        if requested is None:
            return self.default_timeout_seconds
        if requested < 1:
            return 1
        if requested > self.max_timeout_seconds:
            return self.max_timeout_seconds
        return requested
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_config.py -v`

Expected: 4 passing.

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/config.py kc-terminal/tests/test_config.py
git commit -m "feat(kc-terminal): TerminalConfig with env-driven overrides"
```

---

## Task 8: runner — basic execution + output capture

**Files:**
- Create: `kc-terminal/src/kc_terminal/runner.py`
- Test: `kc-terminal/tests/test_runner.py`

- [ ] **Step 1: Write failing tests for basic execution**

Create `kc-terminal/tests/test_runner.py`:

```python
import pytest
from pathlib import Path
from kc_terminal.runner import run


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def test_argv_echo_captures_stdout(workdir):
    result = run(
        argv=["echo", "hello"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""
    assert result["timed_out"] is False
    assert result["mode"] == "argv"
    assert result["stdout_truncated"] is False
    assert result["stderr_truncated"] is False
    assert result["duration_ms"] >= 0


def test_argv_false_nonzero_exit(workdir):
    result = run(
        argv=["false"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 1
    assert result["timed_out"] is False


def test_argv_executable_not_found(workdir):
    result = run(
        argv=["this-command-does-not-exist-12345"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result.get("error") == "executable_not_found"
    assert "this-command-does-not-exist-12345" in result["argv0"]


def test_shell_pipe(workdir):
    result = run(
        argv=None,
        command="echo hi | wc -c",
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["mode"] == "command"
    # `echo hi` -> "hi\n" -> 3 bytes
    assert result["stdout"].strip() == "3"


def test_cwd_applied(workdir):
    result = run(
        argv=["pwd"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    # macOS may add /private prefix; compare resolved paths.
    assert Path(result["stdout"].strip()).resolve() == workdir.resolve()


def test_env_applied_and_secrets_stripped(workdir):
    # Parent env has a secret-prefixed var; child env (built by caller) excludes it.
    # The runner itself only forwards what it's given.
    result = run(
        argv=["sh", "-c", "echo PATH=$PATH; echo SECRET=${KC_TEST_SECRET:-UNSET}"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},  # no KC_TEST_SECRET
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert "PATH=/usr/bin:/bin" in result["stdout"]
    assert "SECRET=UNSET" in result["stdout"]


def test_stdin_is_devnull(workdir):
    # `cat` with no args reads stdin; with DEVNULL it should exit immediately with empty output.
    result = run(
        argv=["cat"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == ""
    assert result["timed_out"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_runner.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement basic `run`**

Create `kc-terminal/src/kc_terminal/runner.py`:

```python
from __future__ import annotations
import subprocess
import time
from pathlib import Path


def _head_tail(text: str, cap_bytes: int) -> tuple[str, bool]:
    """Return (possibly-truncated, was_truncated). Keeps head + tail with a marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= cap_bytes:
        return text, False
    half = cap_bytes // 2
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    dropped = len(encoded) - 2 * half
    marker = f"\n\n...[TRUNCATED {dropped} bytes]...\n\n"
    return head + marker + tail, True


def run(
    *,
    argv: list[str] | None,
    command: str | None,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    output_cap_bytes: int,
) -> dict:
    mode = "argv" if argv is not None else "command"
    start_ns = time.time_ns()
    try:
        if argv is not None:
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                env=env,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        else:
            completed = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
                cwd=str(cwd),
                env=env,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
    except FileNotFoundError as e:
        return {
            "error": "executable_not_found",
            "argv0": (argv[0] if argv else (command or "").split()[0] if command else ""),
            "detail": str(e),
        }
    except subprocess.TimeoutExpired as e:
        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        out, out_tr = _head_tail((e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace"), output_cap_bytes)
        err, err_tr = _head_tail((e.stderr or "") if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace"), output_cap_bytes)
        return {
            "mode": mode,
            "exit_code": -1,
            "stdout": out,
            "stdout_truncated": out_tr,
            "stderr": err,
            "stderr_truncated": err_tr,
            "duration_ms": duration_ms,
            "timed_out": True,
        }
    duration_ms = (time.time_ns() - start_ns) // 1_000_000
    stdout, stdout_tr = _head_tail(completed.stdout or "", output_cap_bytes)
    stderr, stderr_tr = _head_tail(completed.stderr or "", output_cap_bytes)
    return {
        "mode": mode,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_tr,
        "stderr": stderr,
        "stderr_truncated": stderr_tr,
        "duration_ms": duration_ms,
        "timed_out": False,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_runner.py -v`

Expected: 7 passing.

- [ ] **Step 5: Commit**

```bash
git add kc-terminal/src/kc_terminal/runner.py kc-terminal/tests/test_runner.py
git commit -m "feat(kc-terminal): subprocess runner with argv/shell modes, DEVNULL stdin"
```

---

## Task 9: runner — timeout + truncation tests

**Files:**
- Modify: `kc-terminal/tests/test_runner.py`

- [ ] **Step 1: Append failing tests for timeout and truncation behavior**

Append to `kc-terminal/tests/test_runner.py`:

```python
def test_timeout_kills_process(workdir):
    result = run(
        argv=["sleep", "5"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=1,
        output_cap_bytes=1024,
    )
    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["duration_ms"] >= 900  # ~1s, allow scheduler jitter
    assert result["duration_ms"] < 3000


def test_stdout_truncation_head_and_tail(workdir):
    # Emit 8000 bytes of stdout; cap at 1024.
    result = run(
        argv=None,
        command="python3 -c \"import sys; sys.stdout.write('A'*4000); sys.stdout.write('B'*4000)\"",
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["stdout_truncated"] is True
    # Truncated output should contain the marker and both head + tail markers (A and B).
    assert "[TRUNCATED" in result["stdout"]
    assert "A" in result["stdout"]
    assert "B" in result["stdout"]
    # Truncated length should be approximately cap_bytes + marker.
    assert len(result["stdout"].encode("utf-8")) < 1024 + 200


def test_short_output_not_truncated(workdir):
    result = run(
        argv=["echo", "small"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["stdout_truncated"] is False
    assert "[TRUNCATED" not in result["stdout"]
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_runner.py -v`

Expected: 10 passing (no implementation change needed — runner already handles these).

- [ ] **Step 3: Commit**

```bash
git add kc-terminal/tests/test_runner.py
git commit -m "test(kc-terminal): runner timeout + head-tail truncation coverage"
```

---

## Task 10: PermissionEngine — add tier_resolvers

**Files:**
- Modify: `kc-sandbox/src/kc_sandbox/permissions.py`
- Modify: `kc-sandbox/tests/test_permissions.py`

- [ ] **Step 1: Write failing tests for `tier_resolvers`**

Append to `kc-sandbox/tests/test_permissions.py`:

```python
import pytest
from kc_sandbox.permissions import PermissionEngine, Tier, AlwaysAllow, AlwaysDeny


def test_resolver_overrides_tier_map():
    # Tool not in tier_map, but resolver returns SAFE -> allowed without callback.
    calls = []
    def cb(agent, tool, args):
        calls.append(("cb", agent, tool))
        return (True, None)
    engine = PermissionEngine(
        tier_map={},  # tool unknown — would default to DESTRUCTIVE
        agent_overrides={},
        approval_callback=cb,
        tier_resolvers={"terminal_run": lambda args: Tier.SAFE},
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={"argv": ["ls"]})
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    assert calls == []  # no callback invoked


def test_resolver_returns_destructive_invokes_callback():
    seen = []
    def cb(agent, tool, args):
        seen.append(args)
        return (True, None)
    engine = PermissionEngine(
        tier_map={},
        agent_overrides={},
        approval_callback=cb,
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={"argv": ["rm", "x"]})
    assert d.allowed is True
    assert d.tier == Tier.DESTRUCTIVE
    assert seen == [{"argv": ["rm", "x"]}]


def test_resolver_takes_precedence_over_tier_map():
    engine = PermissionEngine(
        tier_map={"terminal_run": Tier.SAFE},  # static says SAFE
        agent_overrides={},
        approval_callback=AlwaysDeny(reason="nope"),
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},  # dynamic says DESTRUCTIVE
    )
    d = engine.check(agent="a", tool="terminal_run", arguments={})
    assert d.allowed is False
    assert d.tier == Tier.DESTRUCTIVE
    assert d.reason == "nope"


@pytest.mark.asyncio
async def test_resolver_works_in_async_path():
    engine = PermissionEngine(
        tier_map={},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={"terminal_run": lambda args: Tier.DESTRUCTIVE},
    )
    d = await engine.check_async(agent="a", tool="terminal_run", arguments={})
    assert d.allowed is True
    assert d.tier == Tier.DESTRUCTIVE


def test_no_resolver_falls_back_to_tier_map():
    engine = PermissionEngine(
        tier_map={"file.read": Tier.SAFE},
        agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={},
    )
    d = engine.check(agent="a", tool="file.read", arguments={})
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    assert d.source == "tier"
```

The first test of the suite already exists; the new ones are appended at the end. If `pytest-asyncio` isn't already configured in the kc-sandbox test setup, add `asyncio_mode = "auto"` to `kc-sandbox/pyproject.toml`'s `[tool.pytest.ini_options]` first (mirror what kc-terminal does in Task 1).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-sandbox && pytest tests/test_permissions.py -v -k "resolver"`

Expected: TypeError ("__init__ got an unexpected keyword argument 'tier_resolvers'").

- [ ] **Step 3: Add `tier_resolvers` field and consult it in both `check` and `check_async`**

Modify `kc-sandbox/src/kc_sandbox/permissions.py`. Update the `PermissionEngine` class signature and both check methods:

```python
class PermissionEngine:
    def __init__(
        self,
        tier_map: dict[str, Tier],
        agent_overrides: dict[str, dict[str, Tier]],
        approval_callback: ApprovalCallback,
        tier_resolvers: dict[str, "Callable[[dict[str, Any]], Tier]"] | None = None,
    ) -> None:
        self.tier_map = dict(tier_map)
        self.agent_overrides = {a: dict(o) for a, o in agent_overrides.items()}
        self.approval_callback = approval_callback
        self.tier_resolvers = dict(tier_resolvers or {})

    def _resolve_tier(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[Tier, str]:
        override = self.agent_overrides.get(agent, {}).get(tool)
        if override is not None:
            return override, "override"
        resolver = self.tier_resolvers.get(tool)
        if resolver is not None:
            return resolver(arguments), "resolver"
        return self.tier_map.get(tool, Tier.DESTRUCTIVE), "tier"
```

Then rewrite `check` and `check_async` to call `_resolve_tier`. Replace lines 51-71 (sync `check`) and 85-106 (`check_async`) with:

```python
    def check(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        tier, source = self._resolve_tier(agent, tool, arguments)
        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)
        allowed, reason = self.approval_callback(agent, tool, arguments)
        callback_source = (
            "override+callback" if source == "override"
            else "resolver+callback" if source == "resolver"
            else "callback"
        )
        return Decision(allowed=allowed, tier=tier, source=callback_source, reason=reason)

    async def check_async(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        tier, source = self._resolve_tier(agent, tool, arguments)
        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)
        result = self.approval_callback(agent, tool, arguments)
        if inspect.iscoroutine(result):
            result = await result
        allowed, reason = result
        callback_source = (
            "override+callback" if source == "override"
            else "resolver+callback" if source == "resolver"
            else "callback"
        )
        return Decision(allowed=allowed, tier=tier, source=callback_source, reason=reason)
```

Add `Callable` to the existing `typing` import at the top of the file if it's not there.

- [ ] **Step 4: Run tests to verify all permissions tests pass**

Run: `cd kc-sandbox && pytest tests/test_permissions.py -v`

Expected: all existing tests still pass + new resolver tests pass.

- [ ] **Step 5: Commit**

```bash
git add kc-sandbox/src/kc_sandbox/permissions.py kc-sandbox/tests/test_permissions.py kc-sandbox/pyproject.toml
git commit -m "feat(kc-sandbox): tier_resolvers field on PermissionEngine for dynamic per-call tier"
```

---

## Task 11: tools — build_terminal_tool factory + resolver

**Files:**
- Create: `kc-terminal/src/kc_terminal/tools.py`
- Modify: `kc-terminal/src/kc_terminal/__init__.py`
- Test: `kc-terminal/tests/test_tool_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create `kc-terminal/tests/test_tool_integration.py`:

```python
import json
import os
import pytest
from pathlib import Path

from kc_core.tools import ToolRegistry
from kc_sandbox.permissions import PermissionEngine, Tier, AlwaysAllow, AlwaysDeny

from kc_terminal.config import TerminalConfig
from kc_terminal.tools import build_terminal_tool, terminal_tier_resolver


@pytest.fixture
def cfg(tmp_path):
    return TerminalConfig(
        roots=[tmp_path],
        secret_prefixes=("KC_TEST_",),
        default_timeout_seconds=10,
        max_timeout_seconds=30,
        output_cap_bytes=4096,
    )


def _invoke(tool, **kwargs) -> dict:
    """Run async impl synchronously for tests."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(tool.impl(**kwargs))


@pytest.mark.asyncio
async def test_safe_call_skips_approval(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    engine = PermissionEngine(
        tier_map={}, agent_overrides={},
        approval_callback=AlwaysDeny(reason="should not be called"),
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(agent="a", tool="terminal_run", arguments={"argv": ["ls"], "cwd": str(tmp_path)})
    assert d.allowed is True
    assert d.tier == Tier.SAFE
    result_json = await tool.impl(argv=["ls"], cwd=str(tmp_path))
    result = json.loads(result_json)
    assert result["exit_code"] == 0
    assert result["tier"] == "SAFE"


@pytest.mark.asyncio
async def test_destructive_call_routes_through_engine(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    engine = PermissionEngine(
        tier_map={}, agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(
        agent="a",
        tool="terminal_run",
        arguments={"argv": ["rm", "nothing-here"], "cwd": str(tmp_path)},
    )
    assert d.tier == Tier.DESTRUCTIVE
    assert d.allowed is True


@pytest.mark.asyncio
async def test_mutating_tier_also_routes_to_destructive(cfg, tmp_path):
    """`git commit` classifies as MUTATING in raw tier but the resolver collapses
    MUTATING -> DESTRUCTIVE so it prompts under the existing engine."""
    engine = PermissionEngine(
        tier_map={}, agent_overrides={},
        approval_callback=AlwaysAllow(),
        tier_resolvers={"terminal_run": terminal_tier_resolver},
    )
    d = await engine.check_async(
        agent="a",
        tool="terminal_run",
        arguments={"argv": ["git", "commit", "-m", "x"], "cwd": str(tmp_path)},
    )
    assert d.tier == Tier.DESTRUCTIVE  # collapsed from raw MUTATING


@pytest.mark.asyncio
async def test_returns_raw_tier_in_result(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], cwd=str(tmp_path)))
    assert result["tier"] == "SAFE"
    result = json.loads(await tool.impl(argv=["echo", "x"], cwd=str(tmp_path)))
    assert result["tier"] == "SAFE"


@pytest.mark.asyncio
async def test_bad_cwd_returns_error(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], cwd="/etc"))
    assert result["error"] == "cwd_outside_roots"


@pytest.mark.asyncio
async def test_both_argv_and_command_rejected(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], command="ls", cwd=str(tmp_path)))
    assert result["error"] == "both_argv_and_command_provided"


@pytest.mark.asyncio
async def test_neither_argv_nor_command_rejected(cfg, tmp_path):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(cwd=str(tmp_path)))
    assert result["error"] == "must_provide_argv_or_command"


@pytest.mark.asyncio
async def test_relative_cwd_rejected(cfg):
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(argv=["ls"], cwd="relative/path"))
    assert result["error"] == "cwd_not_absolute"


@pytest.mark.asyncio
async def test_tool_registers_in_registry(cfg):
    tool = build_terminal_tool(cfg)
    reg = ToolRegistry()
    reg.register(tool)
    assert "terminal_run" in reg.names()


@pytest.mark.asyncio
async def test_env_is_stripped(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("KC_TEST_SECRET", "should-be-stripped")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    tool = build_terminal_tool(cfg)
    result = json.loads(await tool.impl(
        argv=["sh", "-c", "echo SECRET=${KC_TEST_SECRET:-UNSET}"],
        cwd=str(tmp_path),
    ))
    assert result["exit_code"] == 0
    assert "SECRET=UNSET" in result["stdout"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-terminal && pytest tests/test_tool_integration.py -v`

Expected: ImportError for `kc_terminal.tools`.

- [ ] **Step 3: Implement `build_terminal_tool` and `terminal_tier_resolver`**

Create `kc-terminal/src/kc_terminal/tools.py`:

```python
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from kc_core.tools import Tool
from kc_sandbox.permissions import Tier

from kc_terminal.classifier import (
    classify_argv,
    classify_command,
    RawTier,
    BadArgvError,
)
from kc_terminal.config import TerminalConfig
from kc_terminal.env import build_child_env
from kc_terminal.paths import (
    validate_cwd,
    CwdNotAbsolute,
    CwdDoesNotExist,
    CwdNotADirectory,
    CwdOutsideRoots,
)
from kc_terminal.runner import run as runner_run


_PARAMS = {
    "type": "object",
    "properties": {
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Argv list. Mutually exclusive with `command`. Preferred form.",
        },
        "command": {
            "type": "string",
            "description": (
                "Shell command string interpreted by /bin/bash. "
                "Mutually exclusive with `argv`. Always tiers MUTATING or DESTRUCTIVE."
            ),
        },
        "cwd": {
            "type": "string",
            "description": "Absolute path inside an allowlisted root. REQUIRED.",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Optional. Clamped to [1, max_timeout]. Default 60.",
        },
        "description": {
            "type": "string",
            "description": "Optional. Short human label shown in the approval prompt.",
        },
    },
    "required": ["cwd"],
}


_DESCRIPTION = (
    "Run a shell command on the host. Pass exactly one of `argv` (list, preferred) "
    "or `command` (shell string). `cwd` must be an absolute path inside an "
    "allowlisted root. No stdin, no TTY — interactive commands will hang and "
    "be killed at timeout. Returns JSON: {mode, exit_code, stdout, stdout_truncated, "
    "stderr, stderr_truncated, duration_ms, timed_out, cwd, tier}. On error: "
    "{error: <code>, ...}."
)


def _raw_tier_for(args: dict[str, Any]) -> RawTier:
    if args.get("argv") is not None:
        return classify_argv(list(args["argv"]))
    if args.get("command") is not None:
        return classify_command(str(args["command"]))
    raise BadArgvError("no argv or command")


def terminal_tier_resolver(args: dict[str, Any]) -> Tier:
    """Map the 3-state RawTier to the engine's 2-state policy:
    RawTier.SAFE        -> Tier.SAFE        (auto-allow, no prompt)
    RawTier.MUTATING    -> Tier.DESTRUCTIVE (prompts)
    RawTier.DESTRUCTIVE -> Tier.DESTRUCTIVE (prompts)
    """
    try:
        raw = _raw_tier_for(args)
    except BadArgvError:
        # Bad args -> let the tool impl return the structured error.
        # Conservative: tier as DESTRUCTIVE so it prompts before exec
        # (though the impl will short-circuit on validation).
        return Tier.DESTRUCTIVE
    if raw == RawTier.SAFE:
        return Tier.SAFE
    return Tier.DESTRUCTIVE


def build_terminal_tool(cfg: TerminalConfig) -> Tool:
    async def impl(
        argv: list[str] | None = None,
        command: str | None = None,
        cwd: str | None = None,
        timeout_seconds: int | None = None,
        description: str | None = None,
    ) -> str:
        # Validate mutual exclusion.
        if argv is None and command is None:
            return json.dumps({"error": "must_provide_argv_or_command"})
        if argv is not None and command is not None:
            return json.dumps({"error": "both_argv_and_command_provided"})
        if argv is not None and len(argv) == 0:
            return json.dumps({"error": "empty_argv"})
        if cwd is None:
            return json.dumps({"error": "cwd_required"})
        # Validate cwd.
        try:
            cwd_path = validate_cwd(cwd, cfg.roots)
        except CwdNotAbsolute:
            return json.dumps({"error": "cwd_not_absolute", "cwd": cwd})
        except CwdDoesNotExist:
            return json.dumps({"error": "cwd_does_not_exist", "cwd": cwd})
        except CwdNotADirectory:
            return json.dumps({"error": "cwd_not_a_directory", "cwd": cwd})
        except CwdOutsideRoots:
            return json.dumps({"error": "cwd_outside_roots", "cwd": cwd})
        # Classify (RawTier — kept in result for audit clarity).
        try:
            raw_tier = _raw_tier_for({"argv": argv, "command": command})
        except BadArgvError as e:
            return json.dumps({"error": "bad_args", "detail": str(e)})
        # Build env + clamp timeout.
        child_env = build_child_env(dict(os.environ), cfg.secret_prefixes)
        clamped = cfg.clamp_timeout(timeout_seconds)
        # Execute (sync subprocess, off the event loop).
        result = await asyncio.to_thread(
            runner_run,
            argv=argv,
            command=command,
            cwd=cwd_path,
            env=child_env,
            timeout_seconds=clamped,
            output_cap_bytes=cfg.output_cap_bytes,
        )
        # Annotate with cwd echo + raw tier.
        if "error" not in result:
            result["cwd"] = str(cwd_path)
            result["tier"] = raw_tier.value
        return json.dumps(result)

    return Tool(
        name="terminal_run",
        description=_DESCRIPTION,
        parameters=_PARAMS,
        impl=impl,
    )
```

- [ ] **Step 4: Update `__init__.py` to export the factory and resolver**

Replace `kc-terminal/src/kc_terminal/__init__.py`:

```python
"""KonaClaw terminal_run tool."""
from kc_terminal.tools import build_terminal_tool, terminal_tier_resolver
from kc_terminal.config import TerminalConfig

__all__ = ["build_terminal_tool", "terminal_tier_resolver", "TerminalConfig"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd kc-terminal && pytest tests/test_tool_integration.py -v`

Expected: 10 passing.

- [ ] **Step 6: Run the full kc-terminal test suite to confirm nothing regressed**

Run: `cd kc-terminal && pytest -v`

Expected: all classifier, paths, env, config, runner, and integration tests pass.

- [ ] **Step 7: Commit**

```bash
git add kc-terminal/src/kc_terminal/tools.py kc-terminal/src/kc_terminal/__init__.py kc-terminal/tests/test_tool_integration.py
git commit -m "feat(kc-terminal): build_terminal_tool factory + tier resolver"
```

---

## Task 12: Wire terminal_run into kc_supervisor.assembly

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/assembly.py`
- Modify: `kc-supervisor/tests/test_assembly.py`

- [ ] **Step 1: Write failing assembly registration test**

Append to `kc-supervisor/tests/test_assembly.py` (find existing imports and helper fixtures, then add):

```python
def test_terminal_tool_absent_when_disabled(monkeypatch):
    monkeypatch.delenv("KC_TERMINAL_ENABLED", raising=False)
    # Build whatever minimal assembly setup the existing tests use; assert
    # "terminal_run" is NOT in the registered tool names.
    # ... (reuse existing test scaffolding to build an AssembledAgent) ...
    agent = _build_test_agent()  # helper used elsewhere in this file
    assert "terminal_run" not in agent.registry.names()


def test_terminal_tool_present_when_enabled(monkeypatch):
    monkeypatch.setenv("KC_TERMINAL_ENABLED", "true")
    agent = _build_test_agent()
    assert "terminal_run" in agent.registry.names()


def test_terminal_tier_resolver_registered_when_enabled(monkeypatch):
    monkeypatch.setenv("KC_TERMINAL_ENABLED", "true")
    agent = _build_test_agent()
    # tier_resolvers is the new field on PermissionEngine
    assert "terminal_run" in agent.permission_engine.tier_resolvers
```

If `_build_test_agent` or its equivalent isn't already present in `test_assembly.py`, this task should first introduce a small helper that returns an assembled agent given the minimum config used by existing tests. Read the file before writing the new tests and reuse the pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kc-supervisor && pytest tests/test_assembly.py -v -k "terminal"`

Expected: failures (`terminal_run` not registered, or `tier_resolvers` field missing on assembled engine — caught here if not propagated).

- [ ] **Step 3: Wire kc-terminal into `assembly.py`**

In `kc-supervisor/src/kc_supervisor/assembly.py`, immediately after the `if skill_index is not None:` block (around line 220), add:

```python
    # Terminal tool — gated by KC_TERMINAL_ENABLED (default disabled).
    # Lazy-imports kc_terminal so kc-supervisor doesn't hard-depend on the package.
    terminal_tier_resolvers: dict[str, Any] = {}
    if os.environ.get("KC_TERMINAL_ENABLED", "").lower() in ("1", "true", "yes"):
        from kc_terminal import build_terminal_tool, terminal_tier_resolver, TerminalConfig
        terminal_cfg = TerminalConfig.from_env()
        terminal_tool = build_terminal_tool(terminal_cfg)
        registry.register(terminal_tool)
        # Static fallback tier in case the resolver is ever bypassed.
        tier_map[terminal_tool.name] = Tier.DESTRUCTIVE
        terminal_tier_resolvers[terminal_tool.name] = terminal_tier_resolver
```

Then find where `PermissionEngine(...)` is constructed (around line 273) and add the new kwarg. Locate:

```python
    engine = PermissionEngine(
        tier_map=tier_map,
        agent_overrides=agent_overrides,
        approval_callback=...,
    )
```

Add `tier_resolvers=terminal_tier_resolvers` (initialized as `{}` if the block above didn't run — make sure it's defined above this line in all paths). The variable was declared in the new block; if the block didn't run, ensure a `terminal_tier_resolvers: dict = {}` default is declared higher in the function, before the gated block.

Also ensure `import os` exists at the top of the file (it likely already does for other env reads).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kc-supervisor && pytest tests/test_assembly.py -v`

Expected: all existing tests still pass + new terminal tests pass.

- [ ] **Step 5: Run the full kc-supervisor test suite to catch regressions**

Run: `cd kc-supervisor && pytest -v`

Expected: full pass (350+ tests per memory).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/assembly.py kc-supervisor/tests/test_assembly.py
git commit -m "feat(kc-supervisor): register terminal_run tool behind KC_TERMINAL_ENABLED"
```

---

## Task 13: SMOKE checklist doc

**Files:**
- Create: `docs/superpowers/specs/2026-05-10-terminal-tool-SMOKE.md`

- [ ] **Step 1: Write the SMOKE checklist**

Create `docs/superpowers/specs/2026-05-10-terminal-tool-SMOKE.md`:

```markdown
# Terminal Tool — SMOKE Checklist

**Spec:** `2026-05-10-terminal-tool-design.md`
**Plan:** `2026-05-10-terminal-tool-plan.md`

Run on macOS, by Sammy, after merging to `main`. Six gates:

## Setup

1. In the kc-supervisor runtime env, set: `KC_TERMINAL_ENABLED=true`.
2. Restart the supervisor: `pkill -f kc_supervisor && cd kc-supervisor && uv run kc-supervisor`.
3. Confirm via Dashboard or logs that `terminal_run` appears in the tool registry.

## Gates

- [ ] **Gate 1 — SAFE, no prompt.**
  Ask Kona via Telegram/iMessage/Dashboard: "Run `git status` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: no approval prompt; tool returns repo status; audit row shows `tier="SAFE"`.

- [ ] **Gate 2 — MUTATING, prompts.**
  Ask Kona: "Run `git commit -m \"smoke test\"` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: Dashboard shows approval prompt. Approve. Tool returns either commit success or `nothing to commit`. Audit shows `tier="MUTATING"`.

- [ ] **Gate 3 — DESTRUCTIVE, prompts.**
  Ask Kona: "Run `curl https://example.com`."
  - Expected: Dashboard prompts. Approve. Tool returns example.com HTML. Audit shows `tier="DESTRUCTIVE"`.

- [ ] **Gate 4 — Env stripping.**
  Ask Kona: "Run shell command `env | grep -iE '(anthropic|supabase|kona|kc_)'`."
  - Expected: prompts (shell mode is at least MUTATING → DESTRUCTIVE at engine), approve, output is EMPTY. No KonaClaw secrets in child env.

- [ ] **Gate 5 — Path rejection.**
  Ask Kona: "Run `ls` in `/etc`."
  - Expected: NO prompt. Tool returns `{"error": "cwd_outside_roots", "cwd": "/etc"}`.

- [ ] **Gate 6 — Timeout.**
  Ask Kona: "Run `sleep 5` with timeout_seconds 1."
  - Expected: prompts (sleep is MUTATING). Approve. Returns `{timed_out: true, exit_code: -1, duration_ms: ~1000}`.

## Rollback

If any gate fails: set `KC_TERMINAL_ENABLED=false`, restart supervisor, file an issue with the audit row and command details.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-10-terminal-tool-SMOKE.md
git commit -m "docs: SMOKE gates for terminal_run tool"
```

---

## Self-Review Notes

After writing this plan I checked it against the spec:

- **Spec coverage:** every section of the spec is implemented by a task. Architecture (Task 1), Tool surface (Task 11), Tier classification (Tasks 2-4), Path policy (Task 5), Env policy (Task 6), Execution (Tasks 8-9), Testing (every task includes tests), Wiring (Task 12), SMOKE (Task 13). The dynamic-tier-resolver mechanism (Task 10) is what makes the spec's "MUTATING prompts" land on the existing engine without changing semantics for other tools.
- **One spec/engine reconciliation point:** the spec said "MUTATING prompts." The existing `PermissionEngine` only prompts on DESTRUCTIVE. The plan resolves this by adding `tier_resolvers` (Task 10) and having the terminal resolver map `RawTier.MUTATING → Tier.DESTRUCTIVE` at the boundary, while keeping the richer 3-state label in the tool's return JSON for audit clarity. This was the explicit choice approved before writing this plan ("Option A").
- **No placeholders.** Every test body and implementation block is concrete.
- **Type/name consistency:** `RawTier` (kc-terminal's 3-state enum) vs `Tier` (kc-sandbox's existing enum) are kept distinct. `terminal_tier_resolver` is referenced consistently. `build_terminal_tool` signature stable across tasks.
- **One known soft spot:** Task 12 step 3 references `_build_test_agent()` as if it exists. Read the existing `kc-supervisor/tests/test_assembly.py` first and reuse whatever helper or fixture pattern is already in use there. If none exists, the step should introduce a minimal one before adding the three terminal tests.
