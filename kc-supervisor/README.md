# kc-supervisor

KonaClaw supervisor — sub-project 3 of 8. FastAPI service that hosts kc-core
agents with kc-sandbox tools, persists state in SQLite, and exposes HTTP +
WebSocket APIs for the dashboard.

## Install (dev)

    cd ~/Desktop/claudeCode/SammyClaw/kc-supervisor
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ../kc-core -e ../kc-sandbox -e ".[dev]"

## Run

    KC_HOME=~/KonaClaw kc-supervisor
