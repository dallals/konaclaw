# kc-sandbox

KonaClaw sandbox layer (sub-project 2 of 8). Provides named "shares," tiered
permissions, sandboxed file tools, a git-backed write journal, and undo.

Depends on `kc-core`. See umbrella spec for context.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-sandbox
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core
    pip install -e ".[dev]"
