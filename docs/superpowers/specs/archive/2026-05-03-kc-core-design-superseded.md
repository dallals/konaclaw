# kc-core — Design Spec

**Status:** Draft, ready for implementation.
**Date:** 2026-05-03
**Owner:** Sammy Dallal
**Parent spec:** [`2026-05-02-konaclaw-design.md`](./2026-05-02-konaclaw-design.md) (umbrella)
**Sub-project:** kc-core (sub-project #1 of 8 in the KonaClaw umbrella)

---

## 1. Purpose

kc-core is the agent runtime foundation for KonaClaw. It is a Python package that runs a single-agent chat loop against an OpenAI-compatible chat-completions endpoint, drivable from a CLI.

This spec covers v1 of kc-core, which uses **OpenRouter as a bootstrap-only model provider**. The umbrella spec's permanent target is local Ollama; OpenRouter is in place because Sammy is building before his MacBook Pro M5 Max (128 GB) arrives and wants progress now. The provider abstraction is designed so swapping to Ollama later is a config change, not a rewrite.

## 2. Scope

**In scope (v1):**
- Single Python package `konaclaw.core` installable via `pip install -e .`.
- One CLI entry point, `konaclaw`, that runs an interactive chat REPL.
- One agent (no subagents). Chat-only — no tool calls.
- OpenAI-compatible chat provider pointed at OpenRouter.
- Streaming token output to the terminal.
- Per-session JSONL transcript log.
- Optional global memory file (`user.md`) prepended to the system prompt.
- Slash commands: `/quit`, `/reset`, `/model <name>`.

**Out of scope (deferred to later sub-projects):**
- Tool calls of any kind, including filesystem, web fetch, MCP, connectors. (kc-sandbox, kc-mcp, kc-connectors)
- Subagents — persistent or ephemeral. (kc-supervisor)
- Sandbox, shares, permissions, undo. (kc-sandbox)
- HTTP / WebSocket service, dashboard. (kc-supervisor, kc-dashboard)
- SQLite persistence. (kc-supervisor)
- Encrypted secrets store. (kc-supervisor)
- Per-agent `MEMORY.md` files, memory writes. (kc-memory)
- async / concurrency. The CLI is synchronous.
- Live-Ollama tests; live-OpenRouter tests in CI.

## 3. Decisions Locked In (from Brainstorming, 2026-05-03)

| Topic | Decision |
|---|---|
| Scope | Strict kc-core: CLI, one agent, chat-only, no tools |
| Privacy stance | OpenRouter is **bootstrap-only**. CLI prints `[REMOTE MODEL]` banner on session start. Config field is named `provider.kind: openrouter` to make removal trivial. A `# TODO(post-ollama)` marker lives next to the OpenRouter branch in `provider.py`. |
| Default model | `qwen/qwen-2.5-72b-instruct` (closest available analog to the eventual local default). `--model` CLI flag and `/model` slash command override per-session. |
| Repo layout | Monorepo at `~/Desktop/claudeCode/SammyClaw/`. Single `pyproject.toml` at root. Source under `src/konaclaw/`. Future sub-projects become sibling packages: `src/konaclaw/sandbox/`, `src/konaclaw/supervisor/`, etc. |
| Conversation persistence | One JSONL file per CLI invocation at `~/KonaClaw/conversations/<ISO-timestamp>.jsonl`. No cross-session resume. Future kc-supervisor will own SQLite + retention. |
| Long-term memory | kc-core reads `~/KonaClaw/memory/user.md` on startup if present and prepends to the system prompt. Empty/absent file is fine. Writes to user.md are kc-memory's concern, not kc-core's. |
| API key handling | `OPENROUTER_API_KEY` env var wins; falls back to `~/KonaClaw/config/.env`. CLI logs the source on startup. No encrypted store yet. |

## 4. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  CLI process (konaclaw)                                  │
│                                                          │
│  cli.py ──► config.py ──► loads konaclaw.yaml + .env     │
│      │                                                   │
│      ▼                                                   │
│  agent.py (sync chat loop, owns history)                 │
│      │  ▲                                                │
│      │  │ prepends                                       │
│      │  └── memory.py (reads user.md once)               │
│      │                                                   │
│      ├──► provider.py ──HTTP─►  OpenRouter API           │
│      │                          (api.openrouter.ai/v1)   │
│      │                                                   │
│      └──► conversation.py ──►  ~/KonaClaw/conversations/ │
│                                  <ts>.jsonl              │
└──────────────────────────────────────────────────────────┘
```

Single synchronous process. No threads, no async. The provider streams via `httpx`; the agent forwards chunks to stdout as they arrive.

### 4.1 Module layout

```
src/konaclaw/core/
  __init__.py           # version
  cli.py                # argparse + main(); handles slash commands
  agent.py              # Agent class; runs the chat loop, owns history
  provider.py           # OpenAIChatProvider + ProviderError; httpx streaming
  config.py             # KonaClawConfig dataclass + loaders + first-run init
  conversation.py       # JSONL session writer (append-only); atexit hook
  memory.py             # read_user_memory() -> str (returns "" if absent)
  errors.py             # KonaClawError, ConfigError, ProviderError, RuntimeError
  SMOKE.md              # manual verification checklist (§9)

tests/core/
  test_agent.py
  test_provider.py      # respx-mocked httpx
  test_config.py
  test_conversation.py
  test_memory.py
  test_smoke_integration.py
```

### 4.2 Boundaries

- **`provider.py` is the only file that knows about OpenRouter.** Swapping to Ollama later means changing `base_url` in config; `provider.py` stays as-is because both speak `/v1/chat/completions`.
- **`agent.py` is provider-agnostic.** It depends on a `ChatProvider` protocol (`stream_chat(messages, model) -> Iterator[str]`), not on `provider.py`'s implementation.
- **`cli.py` is the only file that touches stdin/stdout for user interaction.** Errors flow up as exceptions; cli.py decides what to print.
- **`conversation.py` writes; nothing reads.** Past sessions are inspected by hand (grep, jq); no programmatic reload in v1.

## 5. The Chat Loop (`agent.py`)

```
On Agent init:
  1. Combine system prompt:
       config.agent.system_prompt
       + (memory.read_user_memory() if non-empty: "\n\n## User context\n\n<memory>")
  2. Initialize history = [{"role":"system","content":<combined>}]
  3. Open conversation JSONL writer at ~/KonaClaw/conversations/<ISO-ts>.jsonl
  4. Write session_start record (model, provider, ts, system_prompt_sha256)

Per turn (loop until /quit, EOF, or second Ctrl-C):
  a. Read line from stdin (prompt_toolkit; line editing + in-process history)
  b. If starts with "/", dispatch slash command and continue.
  c. Append {"role":"user","content":<input>} to history
     Write {"kind":"user","content":<input>,"ts":...} to JSONL
  d. Call provider.stream_chat(history, model) -> Iterator[str]
  e. Print chunks to stdout with flush=True; accumulate into reply_text
  f. Append {"role":"assistant","content":reply_text} to history
     Write {"kind":"assistant","content":reply_text,"ts":...} to JSONL
  g. Loop

On exit (any path):
  - atexit hook writes {"kind":"session_end","reason":<quit|eof|exception>,"ts":...}
  - JSONL file closed.
```

### 5.1 Slash commands

| Command | Behavior |
|---|---|
| `/quit` | Exit cleanly. JSONL session_end reason = `quit`. |
| `/reset` | Clear history except the system message. Write `{"kind":"reset","ts":...}` to JSONL. Same session file continues. |
| `/model <name>` | Switch immediately and write `{"kind":"model_switch","from":...,"to":...}` to JSONL. No pre-validation. If the next chat turn fails with HTTP 400/404 (unknown model), print the provider error, **revert to the previous model**, and write a `model_switch` record reverting it. |
| `/help` | Print available commands. No JSONL record. |

Anything else starting with `/` prints `unknown command, /help for list` and is **not** treated as user input. (Avoids accidental "/cd ~/Documents" leaking into a remote model.)

### 5.2 Tool-call safety

If the model emits an OpenAI-format `tool_calls` field unprompted, the provider passes the raw text through; the agent treats it as plain content. No tool dispatcher exists in kc-core — the field is ignored. This is a kc-sandbox / kc-mcp concern.

### 5.3 Banner

Printed once at session start (not per turn):

```
KonaClaw v0.1 — kc-core
[REMOTE MODEL] qwen/qwen-2.5-72b-instruct via OpenRouter (bootstrap)
session: ~/KonaClaw/conversations/2026-05-03T14-22-08.jsonl
key source: ~/KonaClaw/config/.env
> 
```

`[REMOTE MODEL]` line is gated on `provider.kind == openrouter`. When provider becomes `ollama`, the banner reads `[LOCAL MODEL]` and the line is informational only.

## 6. Configuration (`config.py`)

### 6.1 `~/KonaClaw/config/konaclaw.yaml`

Written with defaults on first run if absent. Hand-editable thereafter.

```yaml
provider:
  kind: openrouter           # bootstrap-only; "ollama" after migration
  base_url: https://openrouter.ai/api/v1
  default_model: qwen/qwen-2.5-72b-instruct

agent:
  name: KonaClaw
  system_prompt: |
    You are KonaClaw, a local-first agent assistant running on Sammy's Mac.
    You're chatting via a CLI; respond in plain text.
    If the user's request would require tools you don't have yet (file ops,
    web fetch, sending messages), say so plainly — don't fabricate results.

runtime:
  conversations_dir: ~/KonaClaw/conversations
  memory_file: ~/KonaClaw/memory/user.md
```

### 6.2 `~/KonaClaw/config/.env`

Secrets only. **Not** auto-created — user writes it. Loaded via `python-dotenv`. File mode 0600 enforced if kc-core ever writes it (it doesn't in v1, but the helper is there).

```
OPENROUTER_API_KEY=sk-or-...
```

### 6.3 Resolution order

1. `OPENROUTER_API_KEY` env var → if set, use it. Banner: `key source: env`.
2. Else read `~/KonaClaw/config/.env`. Banner: `key source: ~/KonaClaw/config/.env`.
3. Else exit with code 2 and the message:
   ```
   No OpenRouter API key found.
   Set OPENROUTER_API_KEY in your shell, or add it to ~/KonaClaw/config/.env.
   Get a key at https://openrouter.ai/keys
   ```

### 6.4 First-run behavior

If `~/KonaClaw/` doesn't exist:
- Create `~/KonaClaw/`, `~/KonaClaw/config/`, `~/KonaClaw/memory/`, `~/KonaClaw/conversations/`.
- Write the default `konaclaw.yaml` from §6.1.
- **Do not** create `.env` — user supplies it.
- **Do not** create `user.md` — left absent on purpose.
- Print: `Initialized ~/KonaClaw/. Add your API key to ~/KonaClaw/config/.env to get started.`

### 6.5 Bootstrap-mode flag

`config.load()` returns a `KonaClawConfig` dataclass with a derived `bootstrap_mode: bool = (provider.kind == "openrouter")`. The banner reads it; future code can branch on it. A single comment marker lives in `provider.py`:

```python
# TODO(post-ollama): remove the OpenRouter branch once Ollama is wired up.
```

This is the explicit, grep-able trace of the bootstrap pivot.

## 7. Storage Layout (kc-core's slice of `~/KonaClaw/`)

```
~/KonaClaw/
├── config/
│   ├── konaclaw.yaml          # kc-core writes default on first run
│   └── .env                    # user creates; CLI reads
├── memory/
│   └── user.md                 # optional; kc-core reads if present
└── conversations/
    └── 2026-05-03T14-22-08.jsonl
```

Future sub-projects fill in `agents/`, `data/konaclaw.db`, `data/secrets.enc`, `shares/`, `logs/` per the umbrella spec.

### 7.1 JSONL session record format

One JSON object per line. ISO-8601 UTC timestamps with `Z` suffix. Schema:

```jsonl
{"ts":"...","kind":"session_start","model":"...","provider":"openrouter","system_prompt_sha256":"...","kc_core_version":"0.1.0"}
{"ts":"...","kind":"user","content":"..."}
{"ts":"...","kind":"assistant","content":"...","truncated":false,"interrupted":false}
{"ts":"...","kind":"reset"}
{"ts":"...","kind":"model_switch","from":"...","to":"..."}
{"ts":"...","kind":"session_end","reason":"quit"}
```

Optional fields on `assistant` records: `truncated:true` (stream cut off) or `interrupted:true` (user Ctrl-C).

`system_prompt_sha256` is sha256 of the *combined* system prompt (config + user.md). Lets future-you tell which sessions used which prompt revision.

### 7.2 Forward-compatibility

JSONL is intentionally easy to replay into kc-supervisor's future SQLite schema:

- `session_start` → row in `conversations` table.
- `user` / `assistant` → rows in `messages` table.
- Other kinds → audit/state events.

A migration script will live in kc-supervisor; kc-core does not need to know about it.

### 7.3 No retention / rotation

Files accumulate. User can `rm` them. kc-supervisor owns retention later.

## 8. Error Handling

Principle: **all errors surface clearly; nothing crashes the loop except unrecoverable startup failures.**

| Failure | Where | Response |
|---|---|---|
| Missing API key | startup | Exit code 2 + setup hint from §6.3. |
| Malformed `konaclaw.yaml` | startup | Exit code 2. Print `ConfigError: <field>: <yaml error>` with line number. |
| `~/KonaClaw/` not writable | startup | Exit code 2. Print path + OS error. |
| OpenRouter HTTP 401 | first turn | Exit code 2. `Auth failed — check your key. Source was <env\|.env>.` |
| OpenRouter HTTP 429 | mid-loop | Print `[rate limited, retrying in 4s…]`. Retry up to 3× with exponential backoff (4s, 8s, 16s). Then print error, **return to prompt** — user can type again. |
| OpenRouter HTTP 5xx | mid-loop | Same retry pattern as 429. |
| Network error (timeout, DNS) | mid-loop | Same. |
| Stream interrupted mid-token | mid-loop | Print `[stream interrupted]`. Save partial reply with `truncated:true`. Return to prompt. |
| User Ctrl-C during stream | mid-loop | Cancel the HTTP request (close the streaming `httpx` response inside the iterator's `except KeyboardInterrupt`). Save partial reply with `interrupted:true`. Return to prompt. **Ctrl-C at an empty prompt** exits cleanly with code 0. |
| Invalid `/model` name | next turn | Provider returns 400/404; caught; print error; revert to previous model. |
| `user.md` exists but unreadable | startup | **Warn, don't exit:** `[memory] couldn't read user.md: <err> — continuing without it.` |

**No silent failures.** Every error path either prints to the user, writes to the JSONL session file, or both. `atexit` ensures `session_end` is written even on uncaught exceptions.

**Logging:** stdout/stderr only in v1. The JSONL session file is the durable record. kc-supervisor adds structured `supervisor.log` later.

## 9. Testing Strategy

### 9.1 Unit tests (pytest + respx)

`tests/core/`. Target: full suite runs in <1 second, no network, no real `~/KonaClaw/` (uses `tmp_path`).

- `test_provider.py` — `respx` mocks `httpx`. Asserts: streams chunks correctly, raises typed errors on 401/429/5xx, retries with backoff on 429/5xx, surfaces network timeouts as `ProviderError`.
- `test_config.py` — load valid YAML; fail clearly on malformed YAML; env var beats `.env`; missing key raises the right error; default config written on first run; `~/KonaClaw/` creation works against `tmp_path`.
- `test_memory.py` — present file read, absent file returns `""`, unreadable file warns + returns `""`.
- `test_conversation.py` — `session_start` written first; `session_end` written via `atexit`; `truncated` and `interrupted` flags set correctly; JSONL is valid line-by-line.
- `test_agent.py` — given a fake `ChatProvider` that yields a canned stream, the agent appends user + assistant messages in order; `/quit` exits; `/reset` clears non-system history and writes a `reset` record; `/model X` updates the model field and writes `model_switch`.

### 9.2 Integration test

`tests/core/test_smoke_integration.py` — single test that:

1. Spins up `Agent` with a fake provider returning a canned 3-turn dialog.
2. Points config at a `tmp_path` `~/KonaClaw/`.
3. Drops a known string into `tmp_path/memory/user.md`.
4. Runs three turns.
5. Asserts: JSONL contains exactly the expected records in order; history matches; `user.md` content was prepended to the system prompt; `session_end` written on exit.

### 9.3 Manual smoke checklist

`src/konaclaw/core/SMOKE.md`. Every item must pass before kc-core ships:

- [ ] `pip install -e .` succeeds; `konaclaw --help` prints usage.
- [ ] First run with no `~/KonaClaw/` creates the dirs + default config.
- [ ] Missing key → clean error with setup hint.
- [ ] Bad key → clean 401 message, exits.
- [ ] Valid key + default config → banner prints, `> ` prompt appears.
- [ ] Type "hi" → tokens stream live; full reply lands in JSONL.
- [ ] Drop a sentence into `~/KonaClaw/memory/user.md` → next session, model recalls it.
- [ ] `/model anthropic/claude-sonnet-4-6` mid-session → next turn uses the new model; recorded in JSONL.
- [ ] Ctrl-C mid-stream → partial reply saved with `interrupted:true`; prompt returns.
- [ ] `/quit` → `session_end` record present; file closed.
- [ ] Pull the wifi mid-stream → retry messages print; eventually returns to prompt without crashing.

CI runs §9.1 + §9.2 on every commit. §9.3 runs locally before merging the kc-core PR.

**Out of scope for v1:** live-OpenRouter tests in CI (cost + flakiness); live-Ollama tests (kc-supervisor's `tests/live/` will own that).

## 10. Dependencies

`pyproject.toml` runtime deps (kc-core only):

- `httpx` — HTTP client with streaming.
- `pyyaml` — config parsing.
- `prompt-toolkit` — readline replacement (line editing, history, Ctrl-C handling).
- `python-dotenv` — `.env` loading.

Dev deps:

- `pytest`
- `respx` — `httpx` mocking.
- `ruff` — lint + format.
- `mypy` — type checking; strict on `konaclaw.core`.

Python: **3.11+** (already present on macOS via Homebrew; no constraint pulls us older).

## 11. Migration Path (post-Ollama)

When the new MacBook arrives and Ollama comes online:

1. Edit `~/KonaClaw/config/konaclaw.yaml`:
   ```yaml
   provider:
     kind: ollama
     base_url: http://localhost:11434/v1
     default_model: qwen2.5:32b
   ```
2. Unset `OPENROUTER_API_KEY` (Ollama needs none; provider will pass an empty `Authorization` header, which Ollama ignores).
3. Run `konaclaw`. Banner now reads `[LOCAL MODEL]`. `bootstrap_mode` is `False`. Same loop, same JSONL, same memory.
4. Grep the codebase for `TODO(post-ollama)`. Remove the OpenRouter branch in `provider.py` (or leave it as a fallback — your call). At this point kc-core is fully aligned with the umbrella spec's "all reasoning happens locally" stance.

## 12. Done Means

Per the umbrella spec, kc-core is done when **you can chat with one agent against your model backend, no tools yet.** Concretely, all items in §9.3 pass.

Next sub-project after kc-core: **kc-sandbox** (shares, permissions, undo) — adds the first real tools to the agent.
