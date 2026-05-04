# kc-supervisor — Smoke Checklist

Run by hand on the target machine after `pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"`.

## Prereqs

- Ollama running locally with `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`).
- `wscat` for the WebSocket sections (`npm i -g wscat` if needed).

## Boot

- [ ] `KC_HOME=~/KonaClaw kc-supervisor` boots without error and binds to `127.0.0.1:8765`.
- [ ] First boot creates `~/KonaClaw/{agents,data,config}` and a stub `config/shares.yaml`.
- [ ] `curl http://127.0.0.1:8765/health` returns `{"status":"ok","uptime_s":<n>,"agents":<n>}`.

## Define an agent

Drop a YAML at `~/KonaClaw/agents/kc.yaml`:
```yaml
name: KonaClaw
model: qwen2.5:7b
system_prompt: |
  You are KonaClaw, a helpful local agent.
```
Then restart the supervisor (Ctrl-C, re-run) so it picks the new file up.

## HTTP

- [ ] `curl http://127.0.0.1:8765/agents` lists `KonaClaw` with `status: idle`.
- [ ] `curl -XPOST -H 'content-type: application/json' -d '{"channel":"dashboard"}' http://127.0.0.1:8765/agents/KonaClaw/conversations` returns `{"conversation_id": <int>}`.
- [ ] `curl http://127.0.0.1:8765/conversations` shows the new conversation.
- [ ] `curl http://127.0.0.1:8765/conversations?agent=KonaClaw` filters to it.
- [ ] `curl http://127.0.0.1:8765/audit` returns `{"entries":[]}` initially.
- [ ] `curl -XPOST http://127.0.0.1:8765/undo/1` returns 501 with detail mentioning "not yet wired".
- [ ] `curl -XPOST http://127.0.0.1:8765/agents/ghost/conversations -H 'content-type: application/json' -d '{}'` returns 404 with detail `unknown agent: ghost`.

## WebSocket chat (NOT wired in v1)

`AgentRuntime.core_agent` is `None` by default in v1 — production wiring of kc_core.Agent into the runtime lands in v0.2 once shares are configured per agent.

- [ ] `wscat -c ws://127.0.0.1:8765/ws/chat/<conversation_id>` followed by `{"type":"user_message","content":"hello"}` produces `{"type":"error","message":"agent KonaClaw not initialized"}` and closes. (Expected — confirms the WS path is reachable and the wiring stub is honest.)

## WebSocket approvals

- [ ] `wscat -c ws://127.0.0.1:8765/ws/approvals` opens cleanly. The connection stays alive, no immediate frame (no pending requests yet).

## Restart resume

- [ ] Stop the supervisor (Ctrl-C), restart, hit `/conversations` — your old conversation is still listed.

## Negative cases

- [ ] `wscat -c ws://127.0.0.1:8765/ws/chat/99999` (non-existent cid) → server sends `{"type":"error","message":"unknown conversation 99999"}` then closes.

## Known not-yet-wired (v0.2)

- `AgentRuntime.core_agent` production wiring (kc-core Agent + kc-sandbox sandboxed tools)
- `POST /undo/{audit_id}` (returns 501 today; the `audit_undo_link` table is ready to use)
- Token-streaming `/ws/chat` (today emits one `assistant_complete` per turn)
- Encrypted secrets store
- launchd auto-restart plist
- Prometheus `/metrics` endpoint
