# KonaClaw Skills — manual smoke gates

Run these after merging the implementation. Each gate is a fresh dev environment:
supervisor and dashboard both running, Telegram + iMessage configured.

## SG-1 — Author a skill, dashboard sees it

Create `~/KonaClaw/skills/test/hello-world/SKILL.md`:

```
---
name: hello-world
description: Greet the user politely.
tags: [demo]
---

# Hello World

When the user says hello, greet them by name and ask what they need.
```

- [ ] Open dashboard → Skills tab. Within 30s the row appears.
- [ ] Expand row → body renders, `skill_dir` matches the path.

## SG-2 — Slash command from dashboard chat

- [ ] In Chat tab, type `/hello-world greet me`. Agent's reply uses the skill body's instructions.
- [ ] Persisted message in chat history reads `/hello-world greet me` (NOT the loaded body).

## SG-3 — Live edit picked up

- [ ] Edit the SKILL.md description on disk to "Greet warmly."
- [ ] Within 30s the dashboard list shows the new description.

## SG-4 — Slash command from Telegram

- [ ] Send `/hello-world` from Telegram.
- [ ] Agent activates the skill identically to dashboard.

## SG-5 — skill_run_script approval flow

Create `~/KonaClaw/skills/test/hello-world/scripts/say-hi.sh`:

```bash
#!/bin/sh
echo "hi from $1"
```

```
chmod +x ~/KonaClaw/skills/test/hello-world/scripts/say-hi.sh
```

- [ ] Trigger the agent to call `skill_run_script(name="hello-world", script="say-hi.sh", args=["sammy"])`. (Tell it to run the say-hi script in chat.)
- [ ] Approval prompt appears in dashboard's Permissions tab.
- [ ] Approve → script runs → agent surfaces stdout containing "hi from sammy".
- [ ] Repeat with Deny → agent receives an error, doesn't run the script.

## SG-6 — Agent-driven discovery

- [ ] In a fresh chat (no slash command), say "I want to set up github auth — do you have anything?". The agent should call `skills_list`, find a relevant skill (if one exists), then `skill_view` it before responding.
- [ ] If no relevant skill exists, agent should respond honestly without inventing one.

## SG-7 — Bad-YAML resilience

- [ ] Author a SKILL.md with deliberately broken YAML.
- [ ] Other skills still listed in the dashboard.
- [ ] Supervisor logs include the parse error.

## SG-8 — Platform gating

- [ ] Author a skill with `platforms: [linux]` on a macOS machine.
- [ ] Dashboard does NOT list it.
- [ ] `/<that-skill-name>` in chat falls through as plain text.

## SG-9 — Path escape rejected

- [ ] In chat, ask the agent to run `skill_run_script(name="hello-world", script="../../etc/passwd")`.
- [ ] Agent receives `{"error": "path_outside_skill_dir"}`.

Mark each gate ✅ before merging Skills to main.
