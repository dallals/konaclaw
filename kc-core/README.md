# kc-core

KonaClaw core agent runtime — sub-project 1 of 8. See the umbrella spec at [`../docs/superpowers/specs/2026-05-02-konaclaw-design.md`](../docs/superpowers/specs/2026-05-02-konaclaw-design.md) for the full architecture.

## Install (dev)

    python3 -m venv .venv          # requires Python >= 3.11
    source .venv/bin/activate
    pip install -e ".[dev]"

## Run

    kc-chat --agent tests/fixtures/agents/echo.yaml

(README will be polished further in Task 11.)
