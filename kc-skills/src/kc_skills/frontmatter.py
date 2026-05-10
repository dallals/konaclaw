"""SKILL.md frontmatter parsing + platform check.

Pure functions, no I/O. Used by SkillIndex when ingesting SKILL.md files
and re-used by tests directly.
"""
from __future__ import annotations
import re
import sys
from typing import Any

import yaml


MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

_PLATFORM_MAP = {
    "macos": "darwin",
    "linux": "linux",
    "windows": "win32",
}


class FrontmatterError(ValueError):
    """Raised when a SKILL.md cannot be parsed or fails validation."""


def parse_skill_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and validate the required fields.

    Returns (frontmatter_dict, body_string). The frontmatter dict is
    normalized: missing `description` becomes `"(missing)"`.

    Raises FrontmatterError on:
      - missing opening `---`
      - missing closing `---`
      - malformed YAML
      - missing or invalid `name`
      - description longer than 1024 chars
    """
    if not content.startswith("---"):
        raise FrontmatterError("missing opening '---'")

    end_match = re.search(r"\n---\s*\n", content[3:])
    if end_match is None:
        raise FrontmatterError("missing closing '---'")

    yaml_block = content[3 : end_match.start() + 3]
    body = content[end_match.end() + 3 :]

    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise FrontmatterError(f"YAML parse error: {e}") from e

    if not isinstance(parsed, dict):
        raise FrontmatterError("YAML frontmatter must be a mapping")

    name = parsed.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise FrontmatterError(
            f"invalid or missing 'name' field "
            f"(must match {_NAME_RE.pattern!r}, ≤{MAX_NAME_LENGTH} chars)"
        )

    description = parsed.get("description")
    if description is None:
        parsed["description"] = "(missing)"
    elif not isinstance(description, str):
        raise FrontmatterError("'description' must be a string")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        raise FrontmatterError(
            f"'description' too long ({len(description)} > {MAX_DESCRIPTION_LENGTH})"
        )

    return parsed, body


def skill_matches_platform(frontmatter: dict[str, Any]) -> bool:
    """Return True when the skill is compatible with the current OS.

    Platforms field forms accepted:
      - omitted / None / [] → matches every OS
      - string ("macos") → coerced to single-item list
      - list ["macos", "linux"] → current OS must match at least one

    Valid platform names: macos, linux, windows.
    """
    platforms = frontmatter.get("platforms")
    if platforms is None or platforms == []:
        return True
    if isinstance(platforms, str):
        platforms = [platforms]
    if not isinstance(platforms, list):
        return True  # malformed value — be permissive (logged elsewhere)

    current = sys.platform
    for raw in platforms:
        normalized = _PLATFORM_MAP.get(str(raw).lower().strip(), str(raw).lower().strip())
        if current.startswith(normalized):
            return True
    return False
