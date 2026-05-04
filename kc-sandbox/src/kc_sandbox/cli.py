"""KonaClaw interactive CLI.

Installed as the `konaclaw` entry point via pyproject `[project.scripts]`.

First run creates `~/.konaclaw/{shares.yaml,agent.yaml}` and `~/Documents/konaclaw/`
with safe defaults. Subsequent runs read those configs and start an interactive
REPL bound to a sandboxed agent.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

from kc_core.ollama_client import OllamaClient
from kc_sandbox.approval import InteractiveApproval
from kc_sandbox.wiring import build_sandboxed_agent


HOME_CONFIG = Path.home() / ".konaclaw"
DEFAULT_SHARE_PATH = Path.home() / "Documents" / "konaclaw"
DEFAULT_MODEL = "gemma3:4b"


def ensure_first_run_setup() -> None:
    """Create `~/.konaclaw/` and the default share dir with starter configs."""
    HOME_CONFIG.mkdir(parents=True, exist_ok=True)
    DEFAULT_SHARE_PATH.mkdir(parents=True, exist_ok=True)

    shares_yaml = HOME_CONFIG / "shares.yaml"
    if not shares_yaml.exists():
        shares_yaml.write_text(
            "shares:\n"
            "  - name: docs\n"
            f"    path: {DEFAULT_SHARE_PATH}\n"
            "    mode: read-write\n"
        )

    agent_yaml = HOME_CONFIG / "agent.yaml"
    if not agent_yaml.exists():
        agent_yaml.write_text(
            "name: KonaClaw\n"
            f"model: {DEFAULT_MODEL}\n"
            "system_prompt: |\n"
            "  You are KonaClaw, a local-first assistant running on Sammy's Mac via Ollama.\n"
            "  Reply in plain text, no markdown headers. Be direct and useful.\n"
            "  If you don't know something, say so - don't guess.\n"
            "\n"
            "  You have access to the \"docs\" share via file.read, file.list, file.write,\n"
            "  and file.delete. Always use a share name and a relative path when calling\n"
            "  file tools.\n"
            "shares: [docs]\n"
            "tools: [\"file.*\"]\n"
            "permission_overrides: {}\n"
            "spawn_policy: persistent\n"
        )


def _model_from_agent_yaml(agent_yaml: Path) -> str:
    data = yaml.safe_load(agent_yaml.read_text()) or {}
    model = data.get("model")
    return model if isinstance(model, str) and model else DEFAULT_MODEL


async def repl(agent) -> None:
    print(f"KonaClaw ready. Share: {DEFAULT_SHARE_PATH}")
    print("Type a message, or 'exit' / Ctrl-D to quit.")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line in ("exit", "quit"):
            return
        if not line:
            continue
        try:
            reply = await agent.send(line)
            print(f"\n{reply.content}")
        except Exception as e:
            print(f"\n[error] {type(e).__name__}: {e}")


def main() -> int:
    ensure_first_run_setup()
    agent_yaml = HOME_CONFIG / "agent.yaml"
    shares_yaml = HOME_CONFIG / "shares.yaml"
    undo_db = HOME_CONFIG / "undo.db"
    model = _model_from_agent_yaml(agent_yaml)

    agent = build_sandboxed_agent(
        agent_yaml=agent_yaml,
        shares_yaml=shares_yaml,
        undo_db=undo_db,
        client=OllamaClient(model=model),
        approval_callback=InteractiveApproval(),
    )
    asyncio.run(repl(agent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
