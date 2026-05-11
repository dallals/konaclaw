# Terminal Tool — Design Spec

**Date:** 2026-05-10
**Phase:** Tools Rollout — Phase A
**Status:** Design (pre-plan)

## Summary

Add a general-purpose terminal/bash tool (`terminal_run`) to KonaClaw. The tool lets agents execute shell commands on the host, gated by per-call approval whose strictness scales with the command's risk tier. Closest existing analog: `kc-skills`' `skill_run_script`, which this tool generalizes (unbounded cwd, dynamic tier classification).

Phase A of the post-Skills tools rollout. Phases B (web fetch + search) and C (todo + clarify) follow as separate spec → plan → execute cycles.

## Goals

1. Unlock `github-pr-workflow`'s open punt and similar skills that need `gh` / `git` / arbitrary CLI invocation.
2. Provide a path-validated, tier-classified, audited primitive for shell execution.
3. Mirror the structural pattern of `kc-skills` (own subpackage, own tests, clean boundary).
4. Avoid scope balloon: synchronous capture only, no streaming, no background processes, no interactive commands.

## Non-Goals

- Docker / VM / cloud sandboxing (`sandbox-exec`, modal, SSH). Local subprocess only.
- Stateful shell sessions (no persistent cwd or env across calls).
- Streaming output, polling, background processes, TTY support, stdin.
- Replacing `skill_run_script`. Skills still use the skill-scoped runner.
- Allowing the agent to add env vars per call. Env is server-controlled.

## Architecture

### Package layout

New top-level package, parallel to `kc-skills`:

```
kc-terminal/
  pyproject.toml
  src/kc_terminal/
    __init__.py
    config.py        # TerminalConfig dataclass
    classifier.py    # classify_argv / classify_command -> Tier
    paths.py         # validate_cwd(cwd, roots) -> Path | raises
    env.py           # build_child_env(parent, secret_prefixes) -> dict
    runner.py        # run(...) -> dict  (the subprocess invocation)
    tools.py         # build_terminal_tool(cfg) -> Tool  (public factory)
  tests/
    test_classifier.py
    test_paths.py
    test_env.py
    test_runner.py
    test_tool_integration.py
```

### Wiring

- `kc-supervisor/pyproject.toml` adds `kc-terminal` as a dependency.
- `kc_supervisor/assembly.py` imports `build_terminal_tool`, constructs a `TerminalConfig` from `KC_TERMINAL_*` env vars (or a YAML pointed to by `KC_TERMINAL_CONFIG`), registers the returned `Tool`.
- A new `KC_TERMINAL_ENABLED` env var (default `false` for safety; flipped to `true` once smoke gates pass) controls registration.

### Dynamic tier resolution

The existing approval/audit machinery resolves tier through a static `tier_map` in `assembly.py`. The terminal tool's tier depends on its arguments, not a static label, so we extend the mechanism:

- `Tool` gains an optional field: `tier_resolver: Callable[[dict], Tier] | None = None`.
- `make_audit_aware_callback` (in `kc_supervisor/audit_tools.py`) checks `tier_resolver` first; if present, calls it with the parsed args to get the per-call tier; falls back to `tier_map[tool.name]` otherwise.
- This is a small, localized change. No behavior change for tools that don't set `tier_resolver`.
- Static `tier_map` entry for `terminal_run` is `Tier.MUTATING` (the conservative default if `tier_resolver` is ever bypassed).

## Tool Surface

Single tool, single call shape.

### Parameters

```jsonc
{
  "argv":     ["string", ...],   // optional; mutually exclusive with command
  "command":  "string",           // optional; mutually exclusive with argv
  "cwd":      "string",           // REQUIRED, absolute path inside allowlisted root
  "timeout_seconds": 60,          // optional; default 60, max from config (default 600)
  "description":     "string"     // optional; short human label for the approval UI
}
```

### Validation rules

- Exactly one of `argv` / `command` must be present.
- `argv` must be a non-empty list of strings.
- `command` must be a non-empty string.
- `cwd` must be absolute, must exist as a directory, must resolve (after symlink resolution) inside at least one allowlisted root.
- `timeout_seconds` clamped to `[1, config.max_timeout_seconds]`.

### Return shape (success)

```jsonc
{
  "mode":             "argv" | "command",
  "exit_code":        0,           // int; -1 on timeout
  "stdout":           "...",        // head+tail truncated if needed
  "stdout_truncated": false,
  "stderr":           "...",
  "stderr_truncated": false,
  "duration_ms":      123,
  "timed_out":        false,
  "cwd":              "/abs/path",  // echoed back, post-validation
  "tier":             "SAFE"        // "SAFE" | "MUTATING" | "DESTRUCTIVE"
}
```

### Return shape (error)

Errors are JSON, never raised to the agent:

```jsonc
{ "error": "cwd_outside_roots",          "cwd": "..." }
{ "error": "cwd_not_a_directory",         "cwd": "..." }
{ "error": "cwd_does_not_exist",          "cwd": "..." }
{ "error": "cwd_not_absolute",            "cwd": "..." }
{ "error": "must_provide_argv_or_command" }
{ "error": "both_argv_and_command_provided" }
{ "error": "empty_argv" }
{ "error": "executable_not_found",        "argv0": "..." }
{ "error": "approval_denied",             "reason": "..." }
```

### Env handling

Env is server-controlled. The agent cannot pass env. Child env is built by `build_child_env(os.environ, cfg.secret_prefixes)` — see Env Policy below.

## Tier Classification

Two paths into `Tier`: argv mode (precise) and shell mode (parsed conservatively).

### `classify_argv(argv) -> Tier`

Look at `Path(argv[0]).name` (basename, so `/usr/bin/git` normalizes to `git`). Match against three sets in `TerminalConfig`:

```python
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "file",
    "stat", "tree", "echo", "which", "whereis", "type", "env", "printenv",
    "date", "uname", "hostname", "uptime", "ps", "id", "true", "false",
}

DESTRUCTIVE_COMMANDS = {
    "rm", "rmdir", "mv", "cp",
    "sudo", "su", "doas",
    "kill", "killall", "pkill",
    "curl", "wget",
    "ssh", "scp", "rsync",
    "dd", "mkfs", "fdisk",
    "chmod", "chown", "chgrp",
    "shutdown", "reboot", "halt",
}
# Everything else (git, gh, npm, pip, uv, pytest, make, docker, python, node, ...) -> MUTATING
```

**Notes on the boundary:**
- `python`, `node` are deliberately NOT in SAFE — they execute arbitrary code (`python -c "import os; os.system('rm -rf ~')"` would otherwise tier SAFE). Default MUTATING.
- `mv`, `cp` classified DESTRUCTIVE because they can silently overwrite. Strict but defensible.

### Sub-rule for git / gh

If `argv[0]` basename is `git` or `gh`, inspect `argv[1]`:

```python
GIT_SAFE = {"status", "log", "diff", "show", "blame", "branch", "remote",
            "rev-parse", "describe", "fetch", "ls-files"}
GIT_DESTRUCTIVE = {"push", "filter-repo"}
# `git reset --hard`, `git clean -fd`, `git branch -D`, `git tag -d` matched by
# scanning argv[2:] for the destructive flag pattern.
# All other git subcommands -> MUTATING.

GH_SAFE_PAIRS = {
    ("repo", "view"),  ("repo", "list"),
    ("pr",   "view"),  ("pr",   "list"),   ("pr", "status"), ("pr", "diff"),
    ("issue","view"),  ("issue","list"),   ("issue", "status"),
    ("run",  "view"),  ("run",  "list"),
}
GH_DESTRUCTIVE_PAIRS = {
    ("pr", "merge"), ("pr", "close"),
    ("repo", "delete"),
    ("secret", "delete"), ("release", "delete"),
}
# Implementation: for gh, look at (argv[1], argv[2]) pairs.
# `gh api ...` is always MUTATING (we don't try to parse -X to detect GET vs POST).
# Pair in GH_SAFE_PAIRS -> SAFE.
# Pair in GH_DESTRUCTIVE_PAIRS -> DESTRUCTIVE.
# Default for gh -> MUTATING.
```

### `classify_command(command_string) -> Tier`

Shell mode is conservative. Parse with `shlex.split(command_string)`. Walk all tokens (the parser does not respect `|`/`&&`/`;` as token delimiters by default — we re-split on those too, treating each segment as a sub-command and using the strictest tier across segments).

- If any token equals one of `DESTRUCTIVE_COMMANDS` → **DESTRUCTIVE**.
- If any token is a redirect operator (`>`, `>>`, `>|`) → **DESTRUCTIVE**.
- If `tee` appears with output to a non-`/dev/null` path → **DESTRUCTIVE**.
- If adjacent tokens `git push` appear → **DESTRUCTIVE**.
- Otherwise → **MUTATING**. Shell mode is **never** SAFE.

### Configurability

All three sets and the git/gh subrules are fields on `TerminalConfig`, populated from a YAML at `KC_TERMINAL_CONFIG` (optional) or defaults baked into `kc_terminal/config.py`. No restart required to change at runtime if config is re-loaded — but for v1, config is loaded once at supervisor startup.

### Audit

The resolved tier is recorded in the audit row alongside `argv` / `command`, `cwd`, and `exit_code`. The audit row schema doesn't need to change — tier goes into the existing `args_json` field as part of the returned result captured by the audit wrapper.

## Path Policy

### Roots

`TerminalConfig.roots: list[Path]` — absolute, resolved.

Loaded at startup from `KC_TERMINAL_ROOTS` (colon-separated, like `PATH`) or YAML. Defaults if unset:

```python
[
    Path.home() / "KonaClaw",
    Path.home() / "Desktop" / "claudeCode" / "SammyClaw",
]
```

### `validate_cwd(cwd_str, roots) -> Path`

1. `p = Path(cwd_str)`. If not absolute → raise `CwdNotAbsolute` → `cwd_not_absolute` error.
2. `p_resolved = p.resolve(strict=True)`. `FileNotFoundError` → raise `CwdDoesNotExist` → `cwd_does_not_exist` error.
3. If `not p_resolved.is_dir()` → raise `CwdNotADirectory` → `cwd_not_a_directory` error.
4. For each root in `roots`: `root_resolved = root.resolve(strict=True)`; accept if `p_resolved == root_resolved` or `root_resolved in p_resolved.parents`.
5. If no root matched → raise `CwdOutsideRoots` → `cwd_outside_roots` error.

Symlink-following is deliberate: a symlink target outside roots must be rejected, preventing `ln -s / ~/escape` tricks.

## Env Policy

### `build_child_env(parent, secret_prefixes) -> dict[str, str]`

```python
SECRET_PREFIXES = (
    "ANTHROPIC_", "OPENAI_", "DEEPSEEK_", "GROQ_",
    "SUPABASE_",  "KONA_",   "KC_",
    "GOOGLE_OAUTH_", "GCAL_", "GMAIL_",
    "TELEGRAM_BOT_TOKEN", "ZAPIER_",
    "STRIPE_",    "TWILIO_", "SENDGRID_",
)
```

Rule: for each key in `parent_env`, drop if `key.startswith(prefix)` for any prefix in `secret_prefixes`. Keep everything else.

**Preserved by name (despite being a secret):** `GITHUB_TOKEN`. `gh` is a headline use case; preserving the token avoids forcing the agent through `gh auth login`. (If this is revisited later, adding `GITHUB_TOKEN` to the strip list is a one-line config change.)

**Stripped (by `KC_` prefix):** all `KC_*` runtime config (`KC_SKILL_DIR`, `KC_TERMINAL_*`, etc.). Child doesn't inherit these.

**Preserved by default:** `PATH`, `HOME`, `USER`, `SHELL`, `LANG`, `TERM`, `TMPDIR`, ssh-agent vars (`SSH_AUTH_SOCK`, `SSH_AGENT_PID`), `AWS_*`, etc.

**No allowlist override.** If a command legitimately needs a stripped var, that's a sign it should be a skill (which has its own env policy via `skill_run_script`), not a terminal call.

**Case sensitivity:** prefix match is exact (case-sensitive). `Anthropic_Key` (mixed case) is preserved. Documented.

## Execution

`runner.run(...)` is the single function that invokes `subprocess.run`. Signature:

```python
def run(
    *,
    argv: list[str] | None,
    command: str | None,
    cwd: Path,                  # already validated
    env: dict[str, str],        # already stripped
    timeout_seconds: int,       # already clamped
    output_cap_bytes: int,      # from config; default 128 KB
) -> dict:
    ...
```

### Subprocess call

- **argv mode:** `subprocess.run(argv, shell=False, ...)`
- **command mode:** `subprocess.run(command, shell=True, executable="/bin/bash", ...)`

Both pass:
- `capture_output=True`
- `text=True`
- `cwd=str(cwd)`
- `env=env`
- `timeout=timeout_seconds`
- `stdin=subprocess.DEVNULL` (hard guarantee against interactive hangs)

### Async wrapper

`subprocess.run` is sync and blocking; the tool's `impl` is async (because `request_approval` is async). The runner is invoked via `await asyncio.to_thread(runner.run, ...)` so a slow child doesn't block the supervisor's event loop.

### Timeout handling

`subprocess.TimeoutExpired` is caught:

```python
return {
    "exit_code": -1,
    "timed_out": True,
    "stdout": _head_tail(e.stdout or "", cap)[0],
    "stdout_truncated": _head_tail(e.stdout or "", cap)[1],
    "stderr": _head_tail(e.stderr or "", cap)[0],
    "stderr_truncated": _head_tail(e.stderr or "", cap)[1],
    "duration_ms": elapsed_ms,
    ...
}
```

`subprocess.run` kills the child automatically when timeout fires.

### Output truncation — `_head_tail(text, cap_bytes) -> (str, bool)`

Head-and-tail truncation, not head-only. The end of stderr is typically where the actual error lives.

- If `len(text.encode("utf-8")) <= cap_bytes`: return `(text, False)`.
- Else: keep the first `cap_bytes // 2` bytes + literal marker `\n\n...[TRUNCATED N bytes]...\n\n` + last `cap_bytes // 2` bytes. Marker includes the dropped byte count.

### Tool `impl` flow

```
1. Parse + validate args (return error JSON on bad input).
2. validate_cwd(cwd, cfg.roots).
3. tier = classify_argv(argv) if argv else classify_command(command).
4. If tier != SAFE:
     allowed, reason = await broker.request_approval(
         agent, "terminal_run",
         {"argv|command": ..., "cwd": ..., "tier": tier.name, "description": ...}
     )
     If not allowed: return {"error": "approval_denied", "reason": reason}.
5. env = build_child_env(os.environ, cfg.secret_prefixes).
6. result = await asyncio.to_thread(runner.run, ...).
7. Return result | {"tier": tier.name}.
```

Step 4's approval call is what `tier_resolver` (Section: Dynamic tier resolution) feeds into. `AuditingToolRegistry`'s wrapper sees the per-call tier and gates accordingly. SAFE skips approval entirely.

## Testing

`kc-terminal/tests/` runs alongside existing pytest harnesses.

### `test_classifier.py` (~40 cases, table-driven)

- argv → tier: SAFE commands (`ls`, `cat`, `grep`); `git`/`gh` subcommand matrix; DESTRUCTIVE set (`rm`, `curl`, `sudo`); `python`/`node` → MUTATING; unknown command → MUTATING.
- argv normalization: `/usr/bin/git status` classifies same as `git status`.
- command → tier: pure pipe (`ls | grep foo`) → MUTATING; `rm` token → DESTRUCTIVE; `>` redirect → DESTRUCTIVE; adjacent `git push` → DESTRUCTIVE; bare `ls` in shell mode → MUTATING (never SAFE).
- Edge: empty argv → raises `ValueError`.

### `test_paths.py` (~15 cases)

- Absolute path inside root → accepted.
- Relative path → `CwdNotAbsolute`.
- Path outside roots → `CwdOutsideRoots`.
- Symlink → outside root: rejected.
- Symlink → inside root: accepted.
- Non-existent path → `CwdDoesNotExist`.
- File (not dir) → `CwdNotADirectory`.
- `cwd == root` exactly → accepted.
- Multi-root: cwd inside second root → accepted.

### `test_env.py` (~10 cases)

- `ANTHROPIC_API_KEY`, `SUPABASE_KEY`, `KC_SKILL_DIR` → stripped.
- `PATH`, `HOME`, `USER`, `SHELL`, `LANG`, `GITHUB_TOKEN`, `AWS_ACCESS_KEY_ID` → preserved.
- Empty parent env → empty child.
- Case sensitivity: `Anthropic_Key` preserved.
- Custom prefix list via config → respected.

### `test_runner.py` (~12 cases, hits real `subprocess`)

- argv `echo` → stdout captured, exit_code 0.
- argv `false` → exit_code 1.
- argv non-existent command → `{"error": "executable_not_found", "argv0": ...}`.
- Timeout: `sleep 5` with `timeout_seconds=1` → `timed_out=True`, `exit_code=-1`.
- stdin DEVNULL: `cat` with no args exits immediately on EOF.
- Truncation: 200KB of stdout with 1KB cap → `stdout_truncated=True`, head + marker + tail present.
- Shell mode pipe: `bash -c "echo hi | wc -c"` → captured correctly.
- cwd applied: `pwd` returns the requested cwd.
- env applied: child sees passed env, doesn't see stripped vars (set `KC_TEST_SECRET=x` in parent, expect absent in child).

### `test_tool_integration.py` (~8 cases)

- Build tool with a fake `ApprovalBroker` that auto-allows.
- SAFE call (`ls`) → no approval invoked, returns `tier: "SAFE"`.
- MUTATING call (`git status`) → approval invoked once, then executes.
- DESTRUCTIVE call (`rm something`) → approval invoked; denial → `approval_denied` error, no subprocess spawned.
- Bad cwd → `cwd_outside_roots` error, no approval, no subprocess.
- Both argv and command → `both_argv_and_command_provided` error.
- Audit hook: spy on `AuditingToolRegistry`, verify each call writes a row with the resolved tier.

### Assembly integration

One test added to `kc-supervisor/tests/test_assembly.py`:
- `terminal_run` is in registered tools when `KC_TERMINAL_ENABLED=true`.
- Absent when unset / `false`.

### No external network in tests

All real-process tests use stock macOS utilities: `echo`, `ls`, `pwd`, `false`, `cat`, `sleep`, `bash`.

## Manual Smoke Gates (post-merge)

Run after merging to `main`, not in CI:

1. **SAFE no-prompt:** `terminal_run argv=["git","status"] cwd=~/Desktop/claudeCode/SammyClaw` — no approval prompt, returns success.
2. **MUTATING prompt:** `terminal_run argv=["git","commit","-m","test"]` — prompts; approve; succeeds (or fails cleanly with nothing-to-commit).
3. **DESTRUCTIVE prompt:** `terminal_run argv=["curl","https://example.com"]` — prompts; approve; returns example.com HTML.
4. **Env stripping:** `terminal_run command="env | grep -iE '(anthropic|supabase|kona|kc_)'"` — MUTATING, prompts, approve; verify output is empty (no KonaClaw secrets leaked).
5. **Path rejection:** `terminal_run argv=["ls"] cwd="/etc"` — returns `cwd_outside_roots` error, no prompt.
6. **Timeout:** `terminal_run argv=["sleep","5"] timeout_seconds=1` — returns `timed_out=true`, `exit_code=-1`.

## Rollout

1. Ship `kc-terminal` with `KC_TERMINAL_ENABLED=false` as the default — the tool is built and tested but not exposed to agents until smoke passes.
2. After all six smoke gates pass on Sammy's machine, flip `KC_TERMINAL_ENABLED=true` in the supervisor's runtime config.
3. Update `github-pr-workflow` skill to remove its punt and call `terminal_run` instead.
4. Document the tool in `kc-terminal/README.md` with the parameter shape, tier semantics, and the strip-list.

## Decisions Locked

| Decision | Choice |
|---|---|
| Sandbox boundary | Local subprocess, pinned to allowlisted roots |
| Approval policy | Tier by command (SAFE/MUTATING/DESTRUCTIVE) |
| Working dir | Multi-root, stateless |
| Env | Strip KonaClaw secrets by prefix list, keep `GITHUB_TOKEN` |
| Output / interactivity | Sync capture, hard timeout, `stdin=DEVNULL`, head+tail truncate |
| Shell choice | Both — `argv` (preferred) or `command` (escalated tier) |
| Package shape | New `kc-terminal` subpackage |

## Out of Scope (future work)

- **Phase B:** web fetch + search tools (Firecrawl/Exa/Tavily backend decision).
- **Phase C:** todo + clarify tools.
- Streaming / polling / background processes (`terminal_start` / `terminal_poll` / `terminal_kill`). Reconsider only if sync model becomes a bottleneck for real workloads.
- Docker / sandbox-exec isolation. Reconsider if the trust model changes (e.g. KonaClaw runs on shared infra).
- Stateful shell sessions (persistent cwd/env).
