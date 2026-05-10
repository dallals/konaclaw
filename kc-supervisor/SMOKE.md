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

## v0.2.1 polish smoke

1. Boot with existing plaintext `~/KonaClaw/config/secrets.yaml`.
   - Expect log line `migrated secrets to encrypted store`.
   - Expect `~/KonaClaw/config/secrets.yaml.enc` exists.
   - Expect `~/KonaClaw/config/secrets.yaml` is gone.
   - Restart; expect normal boot (no migration log).

2. Send a Telegram message from an allowlisted chat.
   - Expect a row in `connector_conv_map` (sqlite3 ~/KonaClaw/data/db.sqlite "SELECT * FROM connector_conv_map").
   - Restart supervisor.
   - Send another message from same chat.
   - Expect same conv_id reused (no new row in conversations).

3. Trigger an approval-required tool; click Deny in the dashboard.
   - Expect a row in audit with decision='denied' and result containing the reason.

4. Close the launcher's terminal window without Ctrl-C.
   - Relaunch.
   - Expect clean boot, no "address already in use" errors.
   - If port-in-use: expect a clear error message + immediate exit.

## Known not-yet-wired (v0.3)

- Approval timeout (currently blocks indefinitely if no `/ws/approvals` client connects).
- Idempotent undo (re-running undo on the same audit_id 500s today; v0.3 will return "already undone").
- Streaming-while-tool-running (today, tool execution pauses the token stream).
- Encrypted secrets store, launchd auto-restart, Prometheus `/metrics`.

## tokens-per-second metric (added 2026-05-09)

- [ ] After a successful chat turn, the SQLite messages table for the AssistantMessage row has a non-NULL `usage_json` column whose JSON parses to `{input_tokens, output_tokens, ttfb_ms, generation_ms, calls, usage_reported}`.
- [ ] After a turn that errored mid-stream (e.g. kill the model server during reply), no AssistantMessage row is written and no `{type:"usage"}` WS frame is sent.
- [ ] An inbound (Telegram) reply also persists `usage_json` on its AssistantMessage row.

## reminders & cron — Phase 1 (added 2026-05-09)

- [ ] On Telegram, ask Kona "remind me in 2 minutes to test reminder fire". 2 minutes later Telegram receives `⏰ test reminder fire`. The dashboard chat for that conversation shows the same string in an assistant bubble.
- [ ] On the dashboard, ask Kona "remind me in 1 minute to test dashboard fire". 1 minute later the chat view shows `⏰ test dashboard fire` as a new bubble.
- [ ] Schedule a daily cron: "every weekday at 9am remind me to check email". Confirm the agent's reply has `human_summary` like "every weekday at 09:00". The next 9am the reminder fires.
- [ ] Schedule a reminder, restart the supervisor, confirm with `SELECT * FROM scheduled_jobs WHERE status='pending'` that the row is intact, then wait for the original due time — the reminder still fires.
- [ ] Cancel by description: schedule "dinner reminder", say "cancel the dinner reminder". Agent confirms cancellation. `SELECT * FROM scheduled_jobs` shows the row is gone.
- [ ] Disambiguation flow: schedule "meeting prep" and "meeting notes", say "cancel the meeting one". Agent gets `ambiguous=True` and asks which. Cancel by ID. Confirm only the chosen one is removed.
- [ ] Confirm scheduling tools are NOT available to non-Kona agents: inspect any non-Kona agent's tool list (via the dashboard's Agents view) and verify the four scheduling tools (`schedule_reminder`, `schedule_cron`, `list_reminders`, `cancel_reminder`) are absent.

## Reminders Phase 2 — manual smoke gates (post-merge)

Pre-req: `channel_routing` table seeded with `telegram → 8627206839 (enabled)`.
Run via: `python -m kc_supervisor channel-routing add --db <KC_DB_PATH> telegram 8627206839`

1. **Literal cross-channel dashboard → telegram.**
   In the dashboard chat: "Kona, set a reminder on Telegram in 2 minutes saying 'phase 2 smoke 1'".
   Wait 2 minutes. Verify: a Telegram message arrives reading "⏰ phase 2 smoke 1".

2. **Agent-phrased same-channel on dashboard.**
   In the dashboard chat: "Kona, in 2 minutes use agent-phrased mode to remind me about my standup notes".
   Wait 2 minutes. Verify: a freshly composed message appears in the dashboard (NOT prefixed with ⏰), with text the model wrote at fire time. Run the same scenario twice — composed text should differ across runs (it's a new model call each time).

3. **Agent-phrased cross-channel telegram → dashboard.**
   In the Telegram chat: "Kona, agent-phrase a reminder to my dashboard in 2 minutes about checking the build".
   Wait 2 minutes. Verify: a composed message appears in the dashboard chat thread (NOT in the Telegram chat), without ⏰ prefix.

4. **Disabled-channel safety.**
   Run: `python -m kc_supervisor channel-routing disable --db <KC_DB_PATH> telegram`.
   Schedule a NEW cross-channel reminder to telegram from the dashboard. Verify: the agent surfaces an error like "channel 'telegram' is disabled". Re-enable: `python -m kc_supervisor channel-routing add --db <KC_DB_PATH> telegram 8627206839`. Verify any in-flight reminder scheduled BEFORE the disable still fires (already-scheduled rows are immune to allowlist toggles).
