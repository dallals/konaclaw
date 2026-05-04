#!/bin/bash
# KonaClaw double-click launcher for macOS.
#
# Double-click this file (or the copy you put on your Desktop / in your Dock)
# to start the sandboxed KonaClaw assistant. Quit with `exit` or Ctrl-D.
#
# Default share: ~/Documents/konaclaw
# Default config: ~/.konaclaw/{shares.yaml, agent.yaml}
# These are created on first run.

set -euo pipefail

KC_SANDBOX="$HOME/Desktop/claudeCode/SammyClaw/kc-sandbox"

if [[ ! -d "$KC_SANDBOX/.venv" ]]; then
    echo "kc-sandbox venv not found at $KC_SANDBOX/.venv"
    echo "Run the install steps in $KC_SANDBOX/README.md first."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

cd "$KC_SANDBOX"
source .venv/bin/activate
exec konaclaw
