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
| GET | `/conversations[?agent=name]` | List conversations |
| POST | `/agents/{name}/conversations` | Start a new conversation |
| GET | `/conversations/{id}/messages` | List messages in a conversation |
| GET | `/audit[?agent=name][&limit=N]` | Recent tool-call audit (limit capped at 1000) |
| POST | `/undo/{audit_id}` | (501 in v1) Undo a journaled action |
| WS | `/ws/chat/{conversation_id}` | Send/receive messages, agent runs |
| WS | `/ws/approvals` | Approval request stream + responses |

## Environment

- `KC_HOME` — root for `agents/`, `data/`, `config/` (default `~/KonaClaw`)
- `KC_OLLAMA_URL` — Ollama URL (default `http://localhost:11434`); consumed by kc-core when production wiring lands in v0.2
- `KC_DEFAULT_MODEL` — default model name (default `qwen2.5:7b`)
- `KC_PORT` — bind port (default `8765`)

## Roadmap (v0.2 follow-ups)

- Wire `POST /undo/{audit_id}` to `kc_sandbox.undo.Undoer` via the `audit_undo_link` table
- Wire `AgentRuntime.core_agent` to a sandboxed kc_core.Agent at registry-load time (today the test injects fakes)
- Encrypted secrets store at `~/KonaClaw/data/secrets.enc`
- launchd plist for auto-restart on crash
- `/metrics` Prometheus endpoint
- Token-streaming over `/ws/chat` (kc-core's streaming path bypasses tool execution today; unifying needs design)
- Per-agent loop locking when multiple WS connections target the same agent
