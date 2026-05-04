from __future__ import annotations
import json
import re
import uuid
from typing import Any

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_text_tool_calls(
    text: str,
    known_tools: list[str],
) -> list[dict[str, Any]]:
    """Best-effort parse of JSON-in-text tool calls from assistant output.

    Looks for fenced JSON blocks first, then the whole text as JSON. Each
    candidate must have shape {"tool": <name>, "arguments": <obj>} with a
    name that's in `known_tools`. Malformed JSON or unknown tools are
    silently skipped — we'd rather under-extract than hallucinate calls.
    """
    candidates: list[str] = _FENCED_JSON_RE.findall(text)
    if not candidates and text.strip().startswith("{"):
        candidates = [text.strip()]

    out: list[dict[str, Any]] = []
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = obj.get("tool")
        args = obj.get("arguments")
        if not isinstance(name, str) or name not in known_tools:
            continue
        if not isinstance(args, dict):
            continue
        out.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": name,
            "arguments": args,
        })
    return out
