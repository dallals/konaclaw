from __future__ import annotations

import sys
from typing import Any, Optional, TextIO


class InteractiveApproval:
    """ApprovalCallback that prompts the user via stdin/stdout for destructive ops.

    Default behavior is privacy-first: any answer other than "y" or "yes"
    (case-insensitive) denies the action.

    Pass to `build_sandboxed_agent(approval_callback=InteractiveApproval())`.
    """

    def __init__(
        self,
        *,
        in_stream: Optional[TextIO] = None,
        out_stream: Optional[TextIO] = None,
    ) -> None:
        self._in = in_stream if in_stream is not None else sys.stdin
        self._out = out_stream if out_stream is not None else sys.stdout

    def __call__(
        self,
        agent: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        arg_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        self._out.write(f"\n[approval] {agent} wants to call: {tool}({arg_str})\n")
        self._out.write("Allow? [y/N] ")
        self._out.flush()
        response = self._in.readline().strip().lower()
        if response in ("y", "yes"):
            return (True, None)
        return (False, "user declined at approval prompt")
