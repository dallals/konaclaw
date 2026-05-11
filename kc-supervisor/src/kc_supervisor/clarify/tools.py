from __future__ import annotations
import json
from typing import Any, Callable

from kc_core.tools import Tool

from kc_supervisor.clarify.broker import ClarifyBroker


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


_DESCRIPTION = (
    "Ask the user a multiple-choice question and pause until they click an "
    "answer. The dashboard renders a card with one button per choice plus a "
    "Skip button. Returns the user's selection (or {choice: null, reason: "
    "'skipped'|'timeout'} if they decline or take too long). Best for "
    "narrow questions like picking a day, picking from a short list, or "
    "asking 'should I do X or Y?'. Don't use this for open-ended questions — "
    "the user can just type a regular reply faster."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "REQUIRED. The question text."},
        "choices":  {"type": "array", "items": {"type": "string"},
                     "description": "REQUIRED. 2-8 distinct option strings."},
        "timeout_seconds": {"type": "integer",
                            "description": "Optional. Default 300, clamped to [10, 600]."},
    },
    "required": ["question", "choices"],
}


def build_clarify_tool(
    broker: ClarifyBroker,
    current_context: Callable[[], dict],
) -> Tool:
    async def impl(
        question: str = "",
        choices: Any = None,
        timeout_seconds: int = 300,
    ) -> str:
        # Validate.
        if not isinstance(question, str) or not question.strip():
            return _json({"error": "missing_question"})
        if not isinstance(choices, list):
            return _json({"error": "missing_choices"})
        if not all(isinstance(c, str) for c in choices):
            return _json({"error": "missing_choices"})
        if len(choices) < 2:
            return _json({"error": "too_few_choices", "count": len(choices), "minimum": 2})
        if len(choices) > 8:
            return _json({"error": "too_many_choices", "count": len(choices), "maximum": 8})
        seen: set[str] = set()
        dupes: list[str] = []
        for c in choices:
            if c in seen and c not in dupes:
                dupes.append(c)
            seen.add(c)
        if dupes:
            return _json({"error": "duplicate_choices", "values": dupes})

        # Clamp.
        try:
            t = int(timeout_seconds)
        except (TypeError, ValueError):
            t = 300
        t = max(10, min(600, t))

        ctx = current_context()
        result = await broker.request_clarification(
            conversation_id=ctx["conversation_id"],
            agent=ctx["agent"],
            question=question.strip(),
            choices=list(choices),
            timeout_seconds=t,
        )
        return _json(result)

    return Tool(
        name="clarify",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        impl=impl,
    )
