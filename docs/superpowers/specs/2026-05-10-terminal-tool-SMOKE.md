# Terminal Tool — SMOKE Checklist

**Spec:** `2026-05-10-terminal-tool-design.md`
**Plan:** `2026-05-10-terminal-tool-plan.md`
**Phase:** Tools Rollout — Phase A

Run on macOS, by Sammy, after merging `phase-a-terminal-tool` to `main`. Six gates.

## Setup

1. In the kc-supervisor runtime env, set: `KC_TERMINAL_ENABLED=true`.
   (Update wherever supervisor reads its env — typically `.envrc`, `~/.kc/env`, or the launch script.)
2. Restart the supervisor:
   ```bash
   pkill -f kc_supervisor || true
   cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor && uv run kc-supervisor
   ```
3. Confirm via Dashboard or logs that `terminal_run` appears in the registered tool list for the agent.

## Gates

- [ ] **Gate 1 — SAFE, no prompt.**
  Ask Kona via Telegram/iMessage/Dashboard: "Run `git status` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: NO approval prompt appears. Tool returns the repo status. Audit row shows `tier: "SAFE"`.

- [ ] **Gate 2 — MUTATING, prompts.**
  Ask Kona: "Run `git commit -m 'smoke test'` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: Dashboard shows an approval prompt. Approve. Tool returns either commit success or `nothing to commit`. Audit row shows `tier: "MUTATING"` even though the engine gated as DESTRUCTIVE.

- [ ] **Gate 3 — DESTRUCTIVE, prompts.**
  Ask Kona: "Run `curl https://example.com`."
  - Expected: Dashboard prompts. Approve. Tool returns the example.com HTML. Audit row shows `tier: "DESTRUCTIVE"`.

- [ ] **Gate 4 — Env stripping.**
  Ask Kona: "Run the shell command `env | grep -iE '(anthropic|supabase|kona|kc_|telegram)'`."
  - Expected: Shell mode → MUTATING → engine gates as DESTRUCTIVE → prompt. Approve. Output should be EMPTY (or contain only non-secret matches). No KonaClaw API keys, no `KC_*` config, no Telegram tokens leak into the subprocess.

- [ ] **Gate 5 — Path rejection.**
  Ask Kona: "Run `ls` in `/etc`."
  - Expected: NO prompt. Tool returns `{"error": "cwd_outside_roots", "cwd": "/etc"}`. The validation short-circuits before the engine ever sees a tier.

- [ ] **Gate 6 — Timeout.**
  Ask Kona: "Run `sleep 5` with timeout_seconds 1."
  - Expected: `sleep` is MUTATING → prompt. Approve. Tool returns `{"timed_out": true, "exit_code": -1, "duration_ms": ~1000}`. Process is killed by the timeout.

- [ ] **Gate 7 — Argv-mode `env` wrapper unwrap.**
  Ask Kona: "Run `terminal_run argv=['env', 'FOO=bar', 'rm', 'nothing-here']` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: DESTRUCTIVE prompt (the `env` prefix must NOT skip approval). Approve. Tool returns `rm: nothing-here: No such file or directory` (or similar).

- [ ] **Gate 8 — Argv-mode language runner with -c.**
  Ask Kona: "Run `terminal_run argv=['python3', '-c', 'print(1)']` in `~/Desktop/claudeCode/SammyClaw`."
  - Expected: DESTRUCTIVE prompt (language -c is arbitrary code). Approve. Tool returns `1`.

- [ ] **Gate 9 — Argv-mode find -delete.**
  Ask Kona: "Run `terminal_run argv=['find', '.', '-delete']` in some safe tmp dir."
  - Expected: DESTRUCTIVE prompt. DENY (don't actually delete anything). Confirm the deny path returns `{error: "approval_denied", ...}` and the subprocess does not run.

## Anti-Goal Verification

- [ ] **Confirm no interactive commands sneak through.** Try asking Kona to run something interactive like `git commit` (without `-m`). Expected: tool either hangs and times out (because stdin is DEVNULL), or returns a quick error from git complaining about no editor. Either way, the supervisor should not lock up.

- [ ] **Confirm `false` flag value disables.** Set `KC_TERMINAL_ENABLED=false`, restart supervisor. Confirm `terminal_run` is NOT in the agent's tool list. (Verifies the gate is real, not vestigial.)

## Rollback

If any gate fails:
1. Set `KC_TERMINAL_ENABLED=false`.
2. Restart supervisor: `pkill -f kc_supervisor && cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor && uv run kc-supervisor`.
3. Confirm `terminal_run` is no longer in the tool list.
4. File an issue noting which gate failed, the command/cwd/argv tried, the audit row, and the supervisor log lines around the failure.

The tool is gated by `KC_TERMINAL_ENABLED` precisely so rollback is one env var + one restart away.

## Post-SMOKE

Once all six gates pass:
1. Update `github-pr-workflow` skill to remove its punt and call `terminal_run` instead.
2. Consider authoring a `kc-terminal/README.md` documenting the tool surface, the tier rules, and the env-strip list for future contributors.
3. Move on to Phase B (web fetch + search) brainstorm.
