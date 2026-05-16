"""Detect `@<template-name> <task>` shorthand at the start of a user message.

When a chat surface receives a message that begins with `@tessy ...`, the
router bypasses the parent agent's LLM turn and directly spawns the named
subagent — saving 2 LLM round-trips (parent decides to spawn, parent formats
the reply) and matching how users naturally address specialists.
"""
from __future__ import annotations
import re

# Same name shape as SubagentTemplate._NAME_RE: lowercase-kebab, ≤64 chars.
_AT_RE = re.compile(r"^@([a-z][a-z0-9-]{0,63})(?:\s+(.+))?$", re.DOTALL)


def parse_at_mention(content: str) -> tuple[str, str] | None:
    """Return (template_name, task) if `content` starts with `@<name> <task>`.

    Returns None for empty task ("@tessy" alone) so callers can show a usage
    hint instead of spawning a subagent with no instructions.
    """
    if not content:
        return None
    m = _AT_RE.match(content.strip())
    if not m:
        return None
    name, task = m.group(1), (m.group(2) or "").strip()
    if not task:
        return None
    return name, task
