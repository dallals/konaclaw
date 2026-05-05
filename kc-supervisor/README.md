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
