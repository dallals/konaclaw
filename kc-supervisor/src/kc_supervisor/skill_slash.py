"""Slash command resolver for skill activation.

Pure helper used by ws_routes.py and inbound.py. Detects user messages
that start with /<known-skill-name> and produces (loaded_message,
original_instruction) where loaded_message is what to feed to send_stream
and original_instruction is the user's text after the slash command (so
the chat history shows what they actually typed).
"""
from __future__ import annotations
import re
from typing import Optional, Tuple

from kc_skills import SkillIndex


_SLASH_RE = re.compile(r"^/([a-z][a-z0-9-]{0,63})(?:\s+(.+))?$", re.DOTALL)


def resolve_slash_command(
    text: str, *, skill_index: SkillIndex,
) -> Optional[Tuple[str, str]]:
    """Try to resolve `text` as a /<skill-name> [instruction] invocation.

    Returns:
      None if the text doesn't start with a slash command pattern, OR if
      the named skill doesn't exist.

      (loaded_message, original_instruction) when matched. The loaded_message
      is suitable for handing to the agent's send_stream; the
      original_instruction should be persisted as the user's message
      (or `/<name>` when they typed only the bare command).
    """
    stripped = text.strip()
    m = _SLASH_RE.match(stripped)
    if m is None:
        return None

    skill_name = m.group(1)
    skill = skill_index.get(skill_name)
    if skill is None:
        return None

    instruction = (m.group(2) or "").strip()
    parts = [
        f"[Skill activation: {skill_name}]",
        "",
        skill.body.strip(),
        "",
        f"[Skill directory: {skill.summary.skill_dir}]",
    ]
    if instruction:
        parts.append("")
        parts.append(f"---\n\nThe user's instruction is: {instruction}")
    return ("\n".join(parts), instruction)
