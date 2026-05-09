#!/bin/bash
# KonaClaw dashboard launcher for macOS.
#
# Double-click to start the supervisor (HTTP/WS API on :8765) AND the
# dashboard dev server (Vite on :5173), then open the browser.
# Press Ctrl-C in this window to stop both.
#
# Config root: ~/KonaClaw/{agents/, config/shares.yaml, data/}
# Models come from ollama (default qwen2.5:7b).

set -euo pipefail

KC_SUPERVISOR="$HOME/Desktop/claudeCode/SammyClaw/kc-supervisor"
KC_DASHBOARD="$HOME/Desktop/claudeCode/SammyClaw/kc-dashboard"
KC_HOME_DIR="$HOME/KonaClaw"

if [[ ! -x "$KC_SUPERVISOR/.venv/bin/kc-supervisor" ]]; then
    echo "kc-supervisor not installed at $KC_SUPERVISOR/.venv"
    echo "See $KC_SUPERVISOR/README.md for install instructions."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

if [[ ! -d "$KC_DASHBOARD/node_modules" ]]; then
    echo "kc-dashboard dependencies not installed."
    echo "Run: cd $KC_DASHBOARD && npm install"
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

mkdir -p "$KC_HOME_DIR/agents" "$KC_HOME_DIR/config" "$KC_HOME_DIR/data"

for port in 8765 5173; do
    if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Port $port is already in use. Run 'lsof -nP -iTCP:$port' to find the holder."
        exit 1
    fi
done

if ! curl -sf -m 1 http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "warning: ollama not reachable at http://127.0.0.1:11434"
    echo "         start it with: ollama serve  (or open the Ollama app)"
    echo "         the supervisor will boot anyway, but chat will fail until ollama is up."
    echo
fi

# Optional OpenAI-compatible chat endpoint (NVIDIA NIM, OpenRouter, etc.) —
# much faster than local Ollama for tool-calling. Put your secrets in
# ~/.konaclaw.env (outside this repo, gitignored by being in $HOME):
#
#     export KC_OLLAMA_URL="https://integrate.api.nvidia.com/v1"
#     export KC_OLLAMA_API_KEY="nvapi-..."
#
# Leave the file absent to fall back to local Ollama at $KC_OLLAMA_URL.
[ -f "$HOME/.konaclaw.env" ] && source "$HOME/.konaclaw.env"

KC_HOME="$KC_HOME_DIR" "$KC_SUPERVISOR/.venv/bin/kc-supervisor" &
SUP_PID=$!

( cd "$KC_DASHBOARD" && npm run dev ) &
VITE_PID=$!

trap 'echo; echo "Shutting down..."; kill "$VITE_PID" "$SUP_PID" 2>/dev/null; wait 2>/dev/null' INT TERM HUP EXIT

for _ in $(seq 1 40); do
    if curl -sf -m 1 http://127.0.0.1:5173 >/dev/null; then
        open http://localhost:5173
        break
    fi
    sleep 0.5
done

echo
echo "KonaClaw running. Press Ctrl-C in this window to stop."
echo "  supervisor: http://127.0.0.1:8765/health"
echo "  dashboard:  http://localhost:5173"

wait
