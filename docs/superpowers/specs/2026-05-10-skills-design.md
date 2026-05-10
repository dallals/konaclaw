# KonaClaw Skills — design

**Status:** Design — approved by Sammy 2026-05-10. Ready for implementation plan.

**Inspiration:** Mirrors the [Hermes agent](https://github.com/NousResearch/hermes-agent) skills system, which itself extends Anthropic's Claude Skills (progressive disclosure, agentskills.io frontmatter conventions). Same shape so external skills can be lifted later if hub sync ever lands.

## Goal

Add a skills system that lets agents (Kona-AI, alice, bob, future agents) load task-focused capability modules on demand. Skills are markdown files with YAML frontmatter, optionally bundling supporting files (references, templates, assets) and executable scripts. The agent discovers skills via a `skills_list` tool and loads bodies via `skill_view`. Users can also invoke skills explicitly with `/<skill-name>` in chat.

## Scope

### In scope (v1)
- `~/KonaClaw/skills/` directory with `<category>/<skill-name>/SKILL.md` layout (or flat `<skill-name>/SKILL.md`).
- New `kc-skills` package: frontmatter parsing, in-memory `SkillIndex` with mtime invalidation.
- Three agent tools: `skills_list`, `skill_view`, `skill_run_script` (path-validated, approval-gated).
- Supervisor-side `/<skill-name>` slash command parsing for dashboard chat, Telegram, and iMessage.
- Dashboard `Skills` tab — read-only list with search + category filters + expand-on-click body preview.
- Platform gating via `platforms: [...]` frontmatter.

### Explicitly out of scope (deferred)
- Hub sync from external catalogs (Anthropic, Claude marketplace, LobeHub, OpenAI). v2.
- General-purpose `bash(cmd)` tool. Separate spec.
- Per-agent skill allowlists (skills are global in v1).
- Filesystem watcher / WS-driven dashboard updates (polling is sufficient).
- Skill editing from the dashboard (read-only — author edits files on disk).
- Inline shell expansion (`` !`cmd` ``) and template substitution (`${KC_SKILL_DIR}`).
- Skill-declared config vars (`metadata.hermes.config`).
- `requires_tools` / `fallback_for_*` conditional activation.
- `prerequisites` env-var / command checks.

## Architecture

Three layers, strictly additive at the public API.

### `~/KonaClaw/skills/` (new — user home)

Mirrors the existing `agents/` / `memory/` / `data/` convention. Layout:

```
~/KonaClaw/skills/
├── github/                              # category folder
│   ├── DESCRIPTION.md                   # optional category overview (not yet read by index)
│   └── github-auth/                     # leaf skill
│       ├── SKILL.md                     # required
│       ├── references/                  # optional
│       │   └── token-scopes.md
│       ├── templates/                   # optional
│       │   └── pat-creation.txt
│       ├── assets/                      # optional (images, etc.)
│       └── scripts/                     # optional (skill_run_script targets)
│           └── verify-auth.sh
└── productivity/
    └── stretch-reminders/
        └── SKILL.md
```

Categories are folders. A flat layout (`<skill-name>/SKILL.md` directly under `skills/`) is also valid. The `category` on each indexed skill is the parent dir name or `null` for flat. The supervisor creates the directory at boot if missing.

### `kc-skills/` (new — Python package at monorepo root)

Self-contained, mirrors the layout of `kc-memory/` and `kc-sandbox/`. No imports from kc-supervisor.

```
kc-skills/
├── pyproject.toml
├── src/kc_skills/
│   ├── __init__.py             # public exports
│   ├── frontmatter.py          # YAML parsing + platform check
│   ├── skill_index.py          # SkillIndex class (in-memory + mtime invalidation)
│   └── tools.py                # build_skill_tools(index, broker, current_agent_provider)
└── tests/
    ├── test_frontmatter.py
    ├── test_skill_index.py
    └── test_tools.py
```

### kc-supervisor wiring

- `service.py` `Deps` gains `skill_index: Optional[SkillIndex] = None`.
- `main.py` constructs `SkillIndex(home / "skills")` at boot and assigns to `deps.skill_index`.
- `assembly.py` calls `kc_skills.tools.build_skill_tools(...)` and registers the three skill tools on every `AssembledAgent`'s `ToolRegistry`. Global by design (Q2 decision).
- `skill_slash.py` (new) — pure helper: `resolve_slash_command(text, *, skill_index) -> Optional[Tuple[str, str]]`.
- `ws_routes.py` `/ws/chat/{conversation_id}` — invokes the helper before the agent turn, uses the loaded body for `send_stream` while persisting the user's original text.
- `inbound.py` `InboundRouter.handle_text` — same helper invocation for Telegram + iMessage messages.
- `http_routes.py` — `GET /skills`, `GET /skills/{name}`, `GET /skills/{name}/files/{path}`. Read-only.

### kc-dashboard wiring

- New nav tab `08 — SKILLS` and route `/skills` in `App.tsx` / `main.tsx`.
- `src/views/Skills.tsx` — read-only list view (~150 lines).
- `src/api/skills.ts` — typed client for the three GET endpoints.
- 30s polling refetch via React Query. No WS.

## Skill format

### Directory + file conventions

- Skill name: `^[a-z][a-z0-9-]{0,63}$`. Mirrors directory name. Used as slash command (`/<name>`).
- One required file: `SKILL.md`.
- Four optional sibling directories: `references/`, `templates/`, `assets/`, `scripts/`.

### SKILL.md frontmatter — required fields

```yaml
---
name: github-auth
description: |
  GitHub auth setup: HTTPS PATs, SSH keys, gh CLI login. Use when the user is
  trying to push, pull, open PRs, or run gh commands and credentials aren't
  already configured.
---
```

- `name` — ≤64 chars, must match the regex above. Indexer rejects on mismatch.
- `description` — ≤1024 chars. The *only* field besides `name` that `skills_list` returns; it's how the agent decides whether to load the skill. Missing description: skill listed but description shown as `(missing)`.

### SKILL.md frontmatter — optional fields

```yaml
version: 1.0.0                          # informational; not enforced
platforms: [macos, linux]               # OS gate — skill hidden on non-matching machines
tags: [github, git, auth]               # for dashboard filtering
related_skills: [github-pr-workflow]    # cross-references shown in the dashboard detail panel
```

`platforms` accepts `macos` / `linux` / `windows`. Maps to `sys.platform.startswith(...)` checks. Absent or empty = available everywhere.

### Validation rules at index time

- Bad YAML, missing `---` boundaries, bad encoding → skill excluded, error logged with file path.
- Missing or invalid `name` → excluded, logged.
- Missing `description` → included, description shown as `(missing)`.
- `platforms` set and current OS not matching → excluded from `list()` (still on disk; not indexed).
- Duplicate `name` across categories → first-discovered wins (alphabetical traversal: category, then skill name). Second logged.

## `SkillIndex`

Single class in `kc-skills/src/kc_skills/skill_index.py`. ≤200 lines.

### Public API

```python
@dataclass(frozen=True)
class SkillSummary:
    name: str
    category: Optional[str]
    description: str
    version: Optional[str]
    platforms: Optional[list[str]]
    tags: list[str]
    related_skills: list[str]
    skill_dir: Path

@dataclass(frozen=True)
class Skill:
    summary: SkillSummary
    body: str
    supporting_files: dict[str, list[str]]
    # e.g. {"references": ["token-scopes.md"], "templates": [...], "scripts": [...]}


class SkillIndex:
    def __init__(self, root: Path) -> None: ...

    def list(self) -> list[SkillSummary]:
        """Platform-filtered, deterministically ordered (category alpha, then name alpha).
        Cheap on hot path: stat-checks each SKILL.md, re-parses only changed files."""

    def get(self, name: str) -> Optional[Skill]: ...

    def read_supporting_file(self, name: str, file_path: str) -> Optional[str]:
        """Returns file contents, or None if the file doesn't exist.
        Raises PathOutsideSkillDir if the resolved path escapes the skill's
        own directory (symlinks pointing out, `..`, absolute paths outside).
        Callers map None → file_not_found and the exception → path_outside_skill_dir."""

    def script_path(self, name: str, script_name: str) -> Optional[Path]:
        """Returns absolute path to scripts/<script_name>, or None if missing.
        Raises PathOutsideSkillDir on path-escape, same as read_supporting_file."""


class PathOutsideSkillDir(Exception):
    """Raised when a caller-supplied file_path resolves outside the skill's dir."""
```

### Mtime invalidation strategy

Internal state:
- `_by_name: dict[str, _Entry]` where `_Entry = (mtime_ns, summary, body)`.
- `_seen_paths: set[Path]` of SKILL.md paths discovered so far.

On every `list()` / `get()` / `script_path()` call, holding `threading.Lock`:

1. Two-level scan with `os.scandir(root)` collecting `<root>/<category>/<skill>/SKILL.md` and `<root>/<skill>/SKILL.md` paths. Each `stat().st_mtime_ns` is one syscall.
2. Cache hit (mtime unchanged) → reuse existing `_Entry`.
3. Cache miss / new path → re-parse frontmatter + read body, update entry.
4. Path no longer present → drop entry.

This makes slash command matching cheap: the WS handler calls `index.get(name)` per chat message; mtime checks dominate over YAML parsing.

### Failure modes

- Bad YAML in one SKILL.md → that skill excluded, others unaffected.
- `~/KonaClaw/skills/` missing → supervisor creates it. Empty index, no errors.
- Permission denied on the dir → log error, set `deps.skill_index = None`. Routes/tools return 503.

### Concurrency

All public methods serialize through one `threading.Lock`. mtime invalidation is idempotent — concurrent calls during a re-parse see the older entry, then the newer one on the next call. No data races.

## Tools

Three tools registered on every `AssembledAgent.registry` (global, per Q2). They read from `Deps.skill_index`.

### `skills_list(category: str | None = None) -> str`

Token-cheap. Returns only `name`, `category`, `description`.

```json
{
  "skills": [
    {"name": "github-auth", "category": "github", "description": "GitHub auth setup..."},
    {"name": "stretch-reminders", "category": "productivity", "description": "..."}
  ],
  "categories": ["github", "productivity"],
  "count": 2,
  "hint": "Use skill_view(name) to load a skill's full instructions."
}
```

- `category=None` → all skills. `category="github"` → filtered to that folder.
- Empty result includes `"message": "No skills found in ~/KonaClaw/skills/"`.
- Description shown as `(missing)` if SKILL.md lacks one.
- Already platform-filtered by `SkillIndex.list()`.

### `skill_view(name: str, file_path: str | None = None) -> str`

Two modes.

**Body + supporting files manifest** (`file_path=None`):

```json
{
  "name": "github-auth",
  "category": "github",
  "skill_dir": "/Users/sammy/KonaClaw/skills/github/github-auth",
  "frontmatter": {
    "version": "1.0.0",
    "tags": ["github", "git"],
    "platforms": null,
    "related_skills": []
  },
  "content": "# GitHub Authentication Setup\n\n...",
  "supporting_files": {
    "references": ["token-scopes.md"],
    "templates": ["pat-creation.txt"],
    "assets": [],
    "scripts": ["verify-auth.sh"]
  },
  "hint": "Load a supporting file with skill_view(name, file_path=\"references/token-scopes.md\")."
}
```

**Supporting file read** (`file_path="references/token-scopes.md"`):

```json
{
  "name": "github-auth",
  "file_path": "references/token-scopes.md",
  "content": "# Token Scopes\n\n..."
}
```

Errors (uniform JSON shape):

- Unknown skill → `{"error": "skill_not_found", "name": "<name>"}`
- File not found → `{"error": "file_not_found", "file_path": "<path>"}`
- Path escapes skill dir (`..`, symlinks, absolute paths outside the dir) → `{"error": "path_outside_skill_dir"}`

### `skill_run_script(name: str, script: str, args: list[str] = []) -> str`

The narrow execution tool from Q4. Runs `~/KonaClaw/skills/<...>/<name>/scripts/<script>` with the given args.

**Successful response:**
```json
{
  "name": "github-auth",
  "script": "verify-auth.sh",
  "exit_code": 0,
  "stdout": "github.com/<user>: authenticated\n",
  "stderr": "",
  "duration_ms": 142
}
```

**Execution semantics:**
- **Path validation**: `index.script_path(name, script)` resolves to an absolute path; rejects anything escaping the skill's `scripts/` dir (no `..`, no symlinks, no other directories).
- **Approval gate**: every call routes through `ApprovalBroker.request_approval(agent=<agent>, tool="skill_run_script", arguments={"name": ..., "script": ..., "args": [...]})`. User approves/denies in the dashboard's Permissions tab. No "approve once for the session" — every call prompts.
- **Subprocess**: `subprocess.run([abs_path, *args], capture_output=True, text=True, timeout=120, cwd=<skill_dir>)`. Shebang determines interpreter; we don't shell-out.
- **Output cap**: stdout / stderr each truncated to 16 KB. `stdout_truncated` / `stderr_truncated` flags set when hit.
- **Timeout**: 120 seconds. `TimeoutExpired` returns `{"error": "timeout", "duration_ms": 120000, "stdout": "<partial>", "stderr": "<partial>"}`.
- **Working directory**: the skill's own dir. `scripts/foo.sh` can read/write `assets/output.json` if needed.
- **Environment**: inherits supervisor env, plus `KC_SKILL_DIR=<abs path>`. Secrets are not stripped — user trusts skills they author or install.

**Error responses:**
- `{"error": "skill_not_found", "name": "<name>"}`
- `{"error": "script_not_found", "script": "<script>"}`
- `{"error": "not_executable", "script": "<script>"}` — fix is `chmod +x`.
- `{"error": "approval_denied", "reason": "<user reason or null>"}`
- `{"error": "path_outside_skill_dir"}` — same validation as `skill_view`.
- `{"error": "timeout", ...}` — see above.

**Out of scope for v1**: streaming output, interactive scripts (no stdin), long-running daemons.

### Tool wiring

`kc-skills/src/kc_skills/tools.py` exposes:

```python
def build_skill_tools(
    *,
    skill_index: SkillIndex,
    approval_broker: ApprovalBroker,
    current_agent_provider: Callable[[], str],   # returns the calling agent's name
) -> list[Tool]: ...
```

`current_agent_provider` is a closure that the supervisor binds per-agent in `assembly.py` — it returns the name of the agent the current call is running under, so `skill_run_script` can pass `agent=<name>` to the approval broker.

## Slash command parsing

### Helper

`kc-supervisor/src/kc_supervisor/skill_slash.py`:

```python
import re
from typing import Optional, Tuple
from kc_skills import SkillIndex

_SLASH_RE = re.compile(r"^/([a-z][a-z0-9-]{0,63})(?:\s+(.+))?$", re.DOTALL)


def resolve_slash_command(
    text: str, *, skill_index: SkillIndex,
) -> Optional[Tuple[str, str]]:
    """If text starts with /<known-skill-name>, returns
    (loaded_user_message, original_user_instruction). Otherwise None.

    The loaded_user_message is what gets handed to send_stream as the
    actual prompt. Format:

        [Skill activation: github-auth]

        <SKILL.md body>

        [Skill directory: /Users/.../skills/github/github-auth]

        ---

        The user's instruction is: set up SSH
    """
    text = text.strip()
    m = _SLASH_RE.match(text)
    if m is None:
        return None

    skill_name = m.group(1)
    skill = skill_index.get(skill_name)
    if skill is None:
        return None

    user_instruction = (m.group(2) or "").strip()
    parts = [
        f"[Skill activation: {skill_name}]",
        "",
        skill.body.strip(),
        "",
        f"[Skill directory: {skill.summary.skill_dir}]",
    ]
    if user_instruction:
        parts.append("")
        parts.append(f"---\n\nThe user's instruction is: {user_instruction}")
    return ("\n".join(parts), user_instruction)
```

### Wiring at three call sites

1. **`ws_routes.py` `/ws/chat/{conversation_id}`** — inside the `async with lock:` block, before `deps.conversations.append(...)`. The persisted `UserMessage` is the user's original text (or `/<name>` if no instruction); `send_stream` receives the loaded body.

2. **`inbound.py` `InboundRouter.handle_text`** — same shape for Telegram + iMessage messages.

3. **No HTTP route** — slash commands only fire from real chat surfaces. The dashboard `Skills` tab is read-only.

### What gets persisted vs what the model sees

- **Persisted in `messages` table**: user's original `/github-auth set up SSH` text. Chat transcript stays clean.
- **Sent to the model**: the loaded skill body + user instruction (one big UserMessage). Not persisted.
- **Subsequent turns**: persisted `/github-auth ...` text is what gets rehydrated for history. Skill body is not re-injected on each turn — it's a one-time activation. Replaying it would balloon context.

### Edge cases

- `/foo` where foo isn't a known skill → falls through as regular user message (model sees literal `/foo`).
- `/known-skill` with no instruction → loaded body, no instruction footer.
- Wrong case `/Github-Auth` → no match (regex is lowercase-only).
- Leading whitespace ` /known-skill foo` → matches (text is stripped).

## Dashboard `Skills` tab

Read-only in v1.

### Backend routes (added to `kc-supervisor/src/kc_supervisor/http_routes.py`)

```python
@app.get("/skills")
def list_skills_endpoint():
    deps = app.state.deps
    idx = deps.skill_index
    if idx is None:
        raise HTTPException(503, detail={"code": "skill_index_unavailable"})
    return {"skills": [_summary_to_dict(s) for s in idx.list()]}


@app.get("/skills/{name}")
def get_skill_endpoint(name: str):
    deps = app.state.deps
    idx = deps.skill_index
    if idx is None:
        raise HTTPException(503, detail={"code": "skill_index_unavailable"})
    skill = idx.get(name)
    if skill is None:
        raise HTTPException(404, detail={"code": "skill_not_found", "name": name})
    return _skill_to_dict(skill)


@app.get("/skills/{name}/files/{file_path:path}")
def get_skill_file_endpoint(name: str, file_path: str):
    deps = app.state.deps
    idx = deps.skill_index
    if idx is None:
        raise HTTPException(503, detail={"code": "skill_index_unavailable"})
    if idx.get(name) is None:
        raise HTTPException(404, detail={"code": "skill_not_found", "name": name})
    try:
        content = idx.read_supporting_file(name, file_path)
    except PathOutsideSkillDir:
        raise HTTPException(422, detail={"code": "path_outside_skill_dir", "file_path": file_path})
    if content is None:
        raise HTTPException(404, detail={"code": "file_not_found", "file_path": file_path})
    return {"name": name, "file_path": file_path, "content": content}
```

No POST/PATCH/DELETE — read-only.

### Frontend layout (`kc-dashboard/src/views/Skills.tsx`, ~150 lines)

Top to bottom, matching `Reminders.tsx` / `Audit.tsx` conventions:

1. **Filter chips row** — toggleable category chips (one per discovered category) and an "All" chip. Multi-select within the category group. State in URL search params (`?category=github&category=productivity`).
2. **Search box** — fuzzy filter on name + description. In-memory; doesn't refetch.
3. **List** — rows showing:
   - Name (mono, prominent)
   - Category pill (small, accent-colored)
   - Description (one-line truncated, full on hover)
   - Tag pills if `tags` is set
   - Platform indicators (`MAC` / `LIN` / `WIN` pills) when `platforms` is set; absent means all-platforms
4. **Expand-on-click row → detail panel** showing:
   - Frontmatter as a small key/value table (version, tags, related_skills, platforms, skill_dir)
   - Full SKILL.md body rendered as markdown (reuse the existing `MessageBubble` markdown renderer from `Chat.tsx`)
   - Supporting files section: "References", "Templates", "Scripts", "Assets" — each a list of relative filenames. Clicking a filename pulls from `GET /skills/{name}/files/{path}` and shows it inline in a code block.
   - "Invoke" affordance: a one-line read-only input showing `/<skill-name> <your instruction>` that the user can copy and paste into the chat tab.

### API client (`kc-dashboard/src/api/skills.ts`)

```typescript
export type Skill = {
  name: string;
  category: string | null;
  description: string;
  version: string | null;
  platforms: string[] | null;
  tags: string[];
  related_skills: string[];
  skill_dir: string;
};

export type SkillDetail = Skill & {
  body: string;
  supporting_files: {
    references: string[]; templates: string[]; assets: string[]; scripts: string[];
  };
};

export const listSkills = () => apiGet<{ skills: Skill[] }>("/skills");
export const getSkill = (name: string) => apiGet<SkillDetail>(`/skills/${name}`);
export const getSkillFile = (name: string, path: string) =>
  apiGet<{ name: string; file_path: string; content: string }>(
    `/skills/${name}/files/${encodeURIComponent(path)}`,
  );
```

### Refresh / freshness

- React Query `refetchInterval: 30_000`. Dashboard sees disk changes within 30 s.
- `SkillIndex.list()` mtime-checks on every call → the GET endpoint always returns current state.

### Empty / error states

- No skills → centered "No skills yet. Drop a SKILL.md under `~/KonaClaw/skills/<category>/<skill>/`."
- 503 (`skill_index_unavailable`) → top banner.
- 404 on detail → row shows error inline.

### Visual style

Match `Reminders.tsx` exactly: registration marks at corners, `border-line` / `bg-panel` palette tokens, mono fonts for metadata, display fonts for headings. Nav tab `08 — SKILLS` after `07 — REMINDERS` in `App.tsx`.

## Error handling & edge cases

### Skill index startup

- `~/KonaClaw/skills/` doesn't exist → supervisor creates it (`mkdir(parents=True, exist_ok=True)`). Empty index, all routes/tools return empty results, no errors.
- Permission denied on the dir → log error, set `deps.skill_index = None`. Routes return 503; tools return 503-shaped error JSON.

### SKILL.md parse failures

- Bad YAML, missing `---`, bad encoding → skill excluded, error logged with file path. Other skills unaffected. Dashboard shows a footer "N skills failed to parse — check supervisor logs" if any failed.
- Missing `name` → excluded, logged.
- Missing `description` → included, shown as `(missing)`.
- Invalid `name` chars → excluded, logged.
- Duplicate `name` across categories → first wins, second logged.

### Slash command edges

- `/foo` where foo isn't known → falls through as regular text.
- Whitespace before `/` → matches.
- Empty user message → existing "must include non-empty content" error.

### `skill_run_script` errors

Already enumerated under Tools section. Notably:
- Approval denied → `{"error": "approval_denied", "reason": "<reason>"}`.
- Script exits non-zero → not a tool error; agent decides what to do with `exit_code`, `stdout`, `stderr`.
- Timeout → returns partial output.
- Path escape → rejected upfront.

### Concurrency

- Multiple agent turns hitting `skill_index.list()` concurrently → serialized through `threading.Lock`. mtime invalidation is idempotent.
- Two `/ws/chat` connections invoking the same skill simultaneously → independent UserMessage flows; no shared state.

### Filesystem race

- User saves a SKILL.md while dashboard is open → polled within 30 s; mtime invalidation picks up the change on the next `list()`.
- User deletes a SKILL.md mid-turn after a slash command already loaded → loaded body is in agent context; turn completes cleanly. Subsequent `/<deleted-name>` invocations fall through.

### Out-of-scope failure modes (acceptable in v1)

- Transient YAML parse failures during partial saves (next `list()` re-parses; users typically save fast enough that a single missed cycle is invisible).
- No skill versioning / rollback. `version` field is informational.
- No quota / rate limit on `skill_run_script`. Approval prompt is the rate limiter.

## Testing strategy

### `kc-skills` unit tests

- `test_frontmatter.py` — happy path; missing closing `---`; bad YAML; missing/invalid `name`; missing `description`; `platforms` matching/not-matching current OS.
- `test_skill_index.py`:
  - Empty dir → empty list.
  - Single skill flat layout.
  - Single skill `<category>/<skill>/` layout — category populated.
  - Multiple categories, deterministic alphabetical order.
  - `get(unknown)` → None.
  - `read_supporting_file` happy path; rejects `..`, symlinks pointing outside, absolute paths.
  - `script_path` happy path; same rejection rules.
  - Mtime invalidation: write, list, modify, list again — change visible.
  - Mtime invalidation: delete after listing — next list omits it.
  - Duplicate `name`: first wins, second logged via `caplog`.
  - Bad YAML: skill excluded, others unaffected.

### kc-supervisor — slash command parser

- `test_skill_slash.py`:
  - `/known-skill` with instruction → loaded body + instruction footer.
  - `/known-skill` without instruction → loaded body, no footer.
  - `/unknown-skill` → None.
  - Plain text → None.
  - Leading whitespace → matches.
  - Wrong case → None.
  - Persisted UserMessage uses original text, not loaded body.

### kc-supervisor — tool integration

- `test_skill_tools.py`:
  - `skills_list()` JSON shape from a seeded `tmp_path` skills dir.
  - `skills_list(category="github")` filters.
  - `skill_view("name")` returns body + supporting_files manifest.
  - `skill_view("name", "references/foo.md")` returns file contents.
  - `skill_view("name", "../etc/passwd")` → `path_outside_skill_dir`.
  - `skill_run_script` happy path: seeded executable shell script, faked approval=allowed; asserts stdout.
  - `skill_run_script` approval denied: `{"error": "approval_denied"}`.
  - `skill_run_script` script not found / not executable / path escape — each returns the right error code.
  - `skill_run_script` timeout: sleep-script returns `{"error": "timeout"}`.
  - `skill_run_script` output truncation at 16KB.

### kc-supervisor — HTTP routes

- `test_http_skills.py` (uses existing `app` fixture; no scheduler needed):
  - `GET /skills` → 200 with skills array.
  - `GET /skills` → 503 when `deps.skill_index` is None.
  - `GET /skills/{name}` → 200 with detail.
  - `GET /skills/{name}` → 404 on unknown name.
  - `GET /skills/{name}/files/{path}` → 200 with content.
  - `GET /skills/{name}/files/{path}` → 422 on path escape (`code=path_outside_skill_dir`).
  - `GET /skills/{name}/files/{path}` → 404 on missing file (`code=file_not_found`).

### kc-supervisor — WS / inbound integration

- `test_ws_chat_slash_command.py`:
  - Send `/known-skill foo` over WS → persisted UserMessage is `/known-skill foo`; agent's `send_stream` was called with the loaded body. Mock the agent.
- `test_inbound_slash_command.py`:
  - Same shape via `InboundRouter.handle_text` with `channel="telegram"`.

### kc-dashboard component tests (Vitest)

- `Skills.test.tsx`:
  - Renders rows from mocked `listSkills`.
  - Category chip filters update URL params + filter list.
  - Search box filters in-memory.
  - Click row → expand panel shows body + frontmatter + supporting files.
  - Click supporting file → fetches and displays content.
  - "Invoke" affordance shows `/<name>` text, read-only.
  - Empty state when API returns `{skills: []}`.
  - 503 banner when API errors.

### End-to-end smoke gates

Manual checklist (new file `docs/superpowers/specs/2026-05-10-skills-SMOKE.md`):

1. Author `~/KonaClaw/skills/test/hello-world/SKILL.md` with `name: hello-world`, simple description. Open dashboard → Skills tab. Row appears within 30 s. Expand → body renders.
2. In chat type `/hello-world greet me`. Agent responds in line with the skill's body. Persisted message reads `/hello-world greet me` (not the loaded body).
3. Edit SKILL.md description on disk. Within 30 s the dashboard shows the new description.
4. Add `scripts/say-hi.sh` (executable, prints "hi"). Trigger the agent to call `skill_run_script`. Approval prompt appears in Permissions tab. Approve → script runs → stdout returned.
5. Try `/known-skill` and `/random-string` in chat. Known activates; unknown falls through.
6. Send `/hello-world` from Telegram (or iMessage) → same activation.
7. Author a SKILL.md with bad YAML. Other skills still listed; supervisor logs the parse error.
8. Author a skill with `platforms: [linux]` on a macOS machine → not listed; not invokable via slash command.

### Out of test scope (v1)

- Performance/load with hundreds of skills.
- Concurrency stress beyond single-process serialization.
- The `bash` tool design.

## File-level change inventory

### `kc-skills/` (new package)

- `pyproject.toml`
- `src/kc_skills/__init__.py` — exports `SkillIndex`, `SkillSummary`, `Skill`, `build_skill_tools`.
- `src/kc_skills/frontmatter.py` — YAML parsing, platform check, validation rules.
- `src/kc_skills/skill_index.py` — `SkillIndex` class.
- `src/kc_skills/tools.py` — `build_skill_tools(skill_index, approval_broker, current_agent_provider) -> list[Tool]`.
- `tests/test_frontmatter.py`
- `tests/test_skill_index.py`
- `tests/test_tools.py`

### kc-supervisor

- `src/kc_supervisor/service.py` — add `skill_index: Optional[SkillIndex] = None` to `Deps`.
- `src/kc_supervisor/main.py` — construct `SkillIndex(home / "skills")` at boot.
- `src/kc_supervisor/assembly.py` — register `build_skill_tools(...)` output on every `AssembledAgent.registry`.
- `src/kc_supervisor/skill_slash.py` (new) — `resolve_slash_command` helper.
- `src/kc_supervisor/ws_routes.py` — call `resolve_slash_command` per chat turn; persist original text, send loaded body.
- `src/kc_supervisor/inbound.py` — same per Telegram/iMessage message.
- `src/kc_supervisor/http_routes.py` — `GET /skills`, `GET /skills/{name}`, `GET /skills/{name}/files/{file_path:path}`.
- Tests at `tests/test_skill_slash.py`, `tests/test_skill_tools.py`, `tests/test_http_skills.py`, `tests/test_ws_chat_slash_command.py`, `tests/test_inbound_slash_command.py`.

### kc-dashboard

- `src/App.tsx` — add nav tab `08 — SKILLS`.
- `src/main.tsx` — register `<Route path="skills" element={<Skills />} />`.
- `src/views/Skills.tsx` — new view.
- `src/api/skills.ts` — new typed client.
- `src/views/Skills.test.tsx` — component tests.

### Docs

- `docs/superpowers/specs/2026-05-10-skills-SMOKE.md` — manual end-to-end checklist.

## Pointers

- Hermes inspiration: https://github.com/NousResearch/hermes-agent/tree/main/skills
- Hermes skill_utils.py (frontmatter + index): `agent/skill_utils.py` in the hermes-agent repo
- Hermes skills_tool.py (the public tools): `tools/skills_tool.py`
- Hermes skill_commands.py (slash command dispatch): `agent/skill_commands.py`
- Anthropic Claude Skills inspiration (progressive disclosure, ≤64-char names, ≤1024-char descriptions)
- Existing similar pattern in this repo: `kc-memory/src/kc_memory/reader.py` (small focused module producing prompt content), `kc-supervisor/src/kc_supervisor/scheduling/` (multi-file feature with HTTP + WS surfaces, used as the structural template)
