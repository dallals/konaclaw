# kc-supervisor — Smoke Checklist (v0.2)

Run by hand on the target machine after `pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"`.

## Prereqs

- Ollama running locally with `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`).
- `wscat` for the WebSocket sections (`npm i -g wscat`).
- A share configured: edit `~/KonaClaw/config/shares.yaml` to add:
  ```yaml
  shares:
    - name: scratch
      path: /tmp/kc-scratch
      mode: read-write
  ```
  And `mkdir -p /tmp/kc-scratch`.

## Boot

- [ ] `KC_HOME=~/KonaClaw kc-supervisor` boots without error and binds to `127.0.0.1:8765`.
- [ ] `curl http://127.0.0.1:8765/health` returns `{"status":"ok",...}`.

## Define an agent

Drop a YAML at `~/KonaClaw/agents/kc.yaml`:
```yaml
name: KonaClaw
model: qwen2.5:7b
system_prompt: |
  You are KonaClaw, a helpful local agent with access to a scratch share. When
  the user asks you to read or write files, use the file.* tools. Always confirm
  destructive ops.
```
Restart the supervisor.

## HTTP

- [ ] `curl http://127.0.0.1:8765/agents` lists `KonaClaw` with `status: idle` (NOT `degraded`).
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"helper","system_prompt":"You assist KonaClaw.","model":"qwen2.5:7b"}'` returns 200 with the new agent. `~/KonaClaw/agents/helper.yaml` exists.
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"helper","system_prompt":"x"}'` returns 409 (collision).
- [ ] `curl -XPOST http://127.0.0.1:8765/agents -H 'content-type: application/json' -d '{"name":"../evil","system_prompt":"x"}'` returns 422 (bad name).
- [ ] `curl -XPOST -H 'content-type: application/json' -d '{"channel":"dashboard"}' http://127.0.0.1:8765/agents/KonaClaw/conversations` returns a `conversation_id`.
- [ ] `curl http://127.0.0.1:8765/audit` returns `{"entries":[]}` initially.

## WebSocket chat (real Ollama)

- [ ] `wscat -c ws://127.0.0.1:8765/ws/chat/<conversation_id>`.
- [ ] Send `{"type":"user_message","content":"Say hello in 5 words."}`. Receive a stream: `agent_status` → multiple `token` frames → `assistant_complete` with the model's actual output.
- [ ] Repeat with `{"type":"user_message","content":"Write a file at scratch/note.txt with the text Hello World."}`. Expect the model to call `file.write`. Frame sequence: `agent_status` → `tool_call` → `tool_result` → `token` (model summarizing) → `assistant_complete`.
- [ ] `cat /tmp/kc-scratch/note.txt` shows `Hello World`.
- [ ] `curl http://127.0.0.1:8765/audit` shows the entries (file.write undoable=true, plus any read/list entries).
- [ ] `curl http://127.0.0.1:8765/conversations/<conversation_id>/messages` returns the full turn history including tool_call/tool_result rows.

## Approvals (real Ollama, real /ws/approvals)

- [ ] In another terminal: `wscat -c ws://127.0.0.1:8765/ws/approvals` (kept open).
- [ ] In the chat WS, send `{"type":"user_message","content":"Delete scratch/note.txt"}`. The model should call `file.delete` (DESTRUCTIVE) — the supervisor pauses.
- [ ] The approvals WS receives `{"type":"approval_request","request_id":"...","agent":"KonaClaw","tool":"file.delete","arguments":{"share":"scratch","relpath":"note.txt"}}`.
- [ ] Send back `{"type":"approval_response","request_id":"<that-id>","allowed":true}`. The chat WS unblocks; tool runs; `assistant_complete` fires.
- [ ] `cat /tmp/kc-scratch/note.txt` → `No such file or directory`.

## Undo

- [ ] `curl http://127.0.0.1:8765/audit` and find the `file.delete` row's `id`. Note `undoable: 1`.
- [ ] `curl -XPOST http://127.0.0.1:8765/undo/<that-id>` returns 200 with `{"reversed": {"kind": "git-revert", "details": {...}}}`.
- [ ] `cat /tmp/kc-scratch/note.txt` → `Hello World` (restored).

## Restart resume

- [ ] Stop the supervisor (Ctrl-C), restart it, hit `/conversations` — your old conversation is still listed. Re-open the WS for that cid; the model sees prior turns when you send a new message (history rehydration from SQLite).

## Negative cases

- [ ] WS chat against a non-existent `conversation_id` → server sends `{"type":"error","message":"unknown conversation 99999"}` then closes.
- [ ] Re-running `POST /undo/<same-id>` after a successful undo → 500 with detail mentioning a journal failure (idempotent-applied tracking is a v0.3 follow-up).
- [ ] `POST /undo/<audit_id_for_a_file_read>` → 422 "no journal op".

## Known not-yet-wired (v0.3)

- Approval timeout (currently blocks indefinitely if no `/ws/approvals` client connects).
- Idempotent undo (re-running undo on the same audit_id 500s today; v0.3 will return "already undone").
- Streaming-while-tool-running (today, tool execution pauses the token stream).
- Encrypted secrets store, launchd auto-restart, Prometheus `/metrics`.
