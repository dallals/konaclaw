from __future__ import annotations
import asyncio
import json
import os
from typing import Any

from kc_core.tools import Tool
from kc_sandbox.permissions import Tier

from kc_terminal.classifier import (
    classify_argv,
    classify_command,
    RawTier,
    BadArgvError,
)
from kc_terminal.config import TerminalConfig
from kc_terminal.env import build_child_env
from kc_terminal.paths import (
    validate_cwd,
    CwdNotAbsolute,
    CwdDoesNotExist,
    CwdNotADirectory,
    CwdOutsideRoots,
)
from kc_terminal.runner import run as runner_run


_PARAMS = {
    "type": "object",
    "properties": {
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Argv list (preferred form). Mutually exclusive with `command`. "
                "First element is the executable; subsequent are arguments."
            ),
        },
        "command": {
            "type": "string",
            "description": (
                "Shell command string interpreted by /bin/bash. Mutually exclusive "
                "with `argv`. Always tiers MUTATING or DESTRUCTIVE (never SAFE). "
                "Use only when pipes/redirects/conjunctions are needed."
            ),
        },
        "cwd": {
            "type": "string",
            "description": (
                "Absolute path inside an allowlisted root. REQUIRED. "
                "Symlinks resolved before the containment check."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": (
                "Optional. Clamped to [1, max_timeout] (default max 600). "
                "Default if unset: 60."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Optional. Short human label shown in the approval prompt "
                "(e.g. 'run pytest in kc-supervisor')."
            ),
        },
    },
    "required": ["cwd"],
}


_DESCRIPTION = (
    "Run a shell command on the host. Pass exactly one of `argv` (list, preferred) "
    "or `command` (shell string with /bin/bash). `cwd` must be an absolute path "
    "inside an allowlisted root. No stdin, no TTY — interactive commands will "
    "hang and be killed at timeout. Returns JSON with mode/exit_code/stdout/"
    "stderr/duration_ms/timed_out/cwd/tier. On error returns {error: <code>, ...}."
)


def _raw_tier_for(args: dict[str, Any]) -> RawTier:
    """Classify based on argv or command. Raises BadArgvError on malformed args."""
    argv = args.get("argv")
    command = args.get("command")
    if argv is not None:
        if not isinstance(argv, list) or not argv:
            raise BadArgvError("argv must be a non-empty list")
        return classify_argv(list(argv))
    if command is not None:
        if not isinstance(command, str) or not command.strip():
            raise BadArgvError("command must be a non-empty string")
        return classify_command(command)
    raise BadArgvError("no argv or command provided")


def terminal_tier_resolver(args: dict[str, Any]) -> Tier:
    """Map the 3-state RawTier from the classifier to the engine's 2-state policy:
    - RawTier.SAFE        -> Tier.SAFE        (engine auto-allows, no prompt)
    - RawTier.MUTATING    -> Tier.DESTRUCTIVE (engine prompts)
    - RawTier.DESTRUCTIVE -> Tier.DESTRUCTIVE (engine prompts)

    Fails closed on classifier errors: malformed/missing args yield Tier.DESTRUCTIVE
    so the engine requires approval before the impl ever runs. PermissionEngine
    catches resolver exceptions and also fails closed, but we do it here too for
    a cleaner audit trail (source='resolver' rather than 'resolver' via fallback).
    """
    try:
        raw = _raw_tier_for(args)
    except (BadArgvError, ValueError, TypeError):
        return Tier.DESTRUCTIVE
    if raw == RawTier.SAFE:
        return Tier.SAFE
    return Tier.DESTRUCTIVE


def build_terminal_tool(cfg: TerminalConfig) -> Tool:
    async def impl(
        argv: list[str] | None = None,
        command: str | None = None,
        cwd: str | None = None,
        timeout_seconds: int | None = None,
        description: str | None = None,
    ) -> str:
        # 1. Validate arg shape (mutual exclusion, required cwd).
        if argv is None and command is None:
            return json.dumps({"error": "must_provide_argv_or_command"})
        if argv is not None and command is not None:
            return json.dumps({"error": "both_argv_and_command_provided"})
        if argv is not None and len(argv) == 0:
            return json.dumps({"error": "empty_argv"})
        if cwd is None:
            return json.dumps({"error": "cwd_required"})

        # 2. Validate cwd against allowlisted roots.
        try:
            cwd_path = validate_cwd(cwd, list(cfg.roots))
        except CwdNotAbsolute:
            return json.dumps({"error": "cwd_not_absolute", "cwd": cwd})
        except CwdDoesNotExist:
            return json.dumps({"error": "cwd_does_not_exist", "cwd": cwd})
        except CwdNotADirectory:
            return json.dumps({"error": "cwd_not_a_directory", "cwd": cwd})
        except CwdOutsideRoots:
            return json.dumps({"error": "cwd_outside_roots", "cwd": cwd})

        # 3. Classify -- record the RawTier in the result JSON for audit clarity.
        try:
            raw_tier = _raw_tier_for({"argv": argv, "command": command})
        except BadArgvError as e:
            return json.dumps({"error": "bad_args", "detail": str(e)})

        # 4. Build child env + clamp timeout.
        child_env = build_child_env(dict(os.environ), cfg.secret_prefixes)
        clamped = cfg.clamp_timeout(timeout_seconds)

        # 5. Execute via to_thread so the sync subprocess doesn't block the loop.
        result = await asyncio.to_thread(
            runner_run,
            argv=argv,
            command=command,
            cwd=cwd_path,
            env=child_env,
            timeout_seconds=clamped,
            output_cap_bytes=cfg.output_cap_bytes,
        )

        # 6. Annotate result with cwd echo + raw tier (only on success path).
        if "error" not in result:
            result["cwd"] = str(cwd_path)
            result["tier"] = raw_tier.value
        return json.dumps(result)

    return Tool(
        name="terminal_run",
        description=_DESCRIPTION,
        parameters=_PARAMS,
        impl=impl,
    )
