from __future__ import annotations
import asyncio, json
from typing import Callable
from kc_core.tools import Tool
from kc_subagents.templates import SubagentIndex
from kc_subagents.runner import SubagentRunner

CurrentContext = Callable[[], tuple[str, str]]       # () -> (conversation_id, parent_agent)

def build_subagent_tools(
    *,
    index: SubagentIndex,
    runner: SubagentRunner,
    current_context: CurrentContext,
) -> list[Tool]:

    async def spawn_impl(
        template: str, task: str,
        context: dict | None = None,
        label: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        t = index.get(template)
        if t is None:
            degraded = index.degraded()
            if template in degraded:
                return f"error: template {template!r} is degraded: {degraded[template]}"
            return f"error: unknown template {template!r}"
        try:
            cid, parent_agent = current_context()
        except Exception:
            return "error: no current conversation context"
        try:
            handle = runner.spawn(
                template=t, task=task, context=context, label=label,
                parent_conversation_id=cid, parent_agent=parent_agent,
                timeout_override=timeout_seconds,
            )
        except RuntimeError as e:
            return f"error: {e}"
        return json.dumps({
            "subagent_id": handle, "status": "running",
            "template": template, "label": label,
        })

    async def await_impl(
        subagent_ids: list[str], timeout_seconds: int | None = None,
    ) -> str:
        ceiling = None
        if timeout_seconds is not None:
            ceiling = max(10, min(int(timeout_seconds), 1800))
        results = await asyncio.gather(
            *[runner.await_one(h, ceiling_seconds=ceiling) for h in subagent_ids],
            return_exceptions=False,
        )
        out = []
        for r in results:
            row = {
                "subagent_id":     r.subagent_id,
                "status":          r.status,
                "duration_ms":     r.duration_ms,
                "tool_calls_used": r.tool_calls_used,
            }
            if r.status == "ok":
                row["reply"] = r.reply or ""
            else:
                row["error"] = r.error or ""
            out.append(row)
        return json.dumps(out)

    spawn_tool = Tool(
        name="spawn_subagent",
        description=(
            "Spawn an ephemeral subagent from a registered template to perform a "
            "single mission. Returns a handle JSON; pair with await_subagents to "
            "collect the result. Subagent runs in fresh context."
        ),
        parameters={
            "type": "object",
            "properties": {
                "template":        {"type": "string"},
                "task":            {"type": "string"},
                "context":         {"type": "object"},
                "label":           {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["template", "task"],
        },
        impl=spawn_impl,
    )
    await_tool = Tool(
        name="await_subagents",
        description=(
            "Join one or more subagent handles previously returned by "
            "spawn_subagent. Returns a JSON array preserving input order."
        ),
        parameters={
            "type": "object",
            "properties": {
                "subagent_ids":    {"type": "array", "items": {"type": "string"},
                                    "minItems": 1, "maxItems": 8},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["subagent_ids"],
        },
        impl=await_impl,
    )
    return [spawn_tool, await_tool]
