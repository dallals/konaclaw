# kc-sandbox

KonaClaw sandbox — sub-project 2 of 8. Provides:

- Named "shares" (allowlisted folders) with traversal-safe path resolution
- A tiered permission engine (Safe / Mutating / Destructive) with per-agent overrides
- Sandboxed `file.*` tools (`read`, `list`, `write`, `delete`)
- A per-share git journal so every write is a commit
- An undo log + `Undoer` that can revert any journaled change

Depends on `kc-core` (sub-project 1). See the umbrella spec at
`../docs/superpowers/specs/2026-05-02-konaclaw-design.md`.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core
    pip install -e ".[dev]"

## Test

    pytest tests/ -v

See `SMOKE.md` for the manual end-to-end walkthrough.

## Public surface

- `kc_sandbox.shares.SharesRegistry`
- `kc_sandbox.permissions.PermissionEngine`, `Tier`, `AlwaysAllow`, `AlwaysDeny`
- `kc_sandbox.journal.Journal`
- `kc_sandbox.undo.UndoLog`, `UndoEntry`, `Undoer`
- `kc_sandbox.tools.build_file_tools`, `DEFAULT_FILE_TOOL_TIERS`
- `kc_sandbox.wiring.build_sandboxed_agent` — the one-call helper for entry points
