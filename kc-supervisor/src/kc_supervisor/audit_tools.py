from __future__ import annotations
import contextvars
import json
from typing import Any, Optional
from kc_core.tools import Tool, ToolRegistry
from kc_sandbox.permissions import Decision, PermissionEngine
from kc_sandbox.undo import UndoLog, UndoEntry
from kc_supervisor.storage import Storage


# Contextvars that thread Decision and eid through the async tool-execution path.
# Per-cid lock (in ws_routes) guarantees no cross-conversation bleed.
_decision_contextvar: contextvars.ContextVar[Optional[Decision]] = contextvars.ContextVar(
    "kc_supervisor_decision", default=None,
)
_eid_contextvar: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "kc_supervisor_eid", default=None,
)


class RecordingUndoLog(UndoLog):
    """UndoLog subclass that captures each record()'s returned eid into a contextvar.

    Used by kc-supervisor's audit pipeline so the AuditingToolRegistry can link an
    audit row to its journal op without modifying kc-sandbox's tool surface.
    """

    def record(self, e: UndoEntry) -> int:
        eid = super().record(e)
        _eid_contextvar.set(eid)
        return eid


class AuditingToolRegistry(ToolRegistry):
    """ToolRegistry that wraps each registered tool's impl with an audit writer.

    After every invoke (success or exception):
      - Writes one row to the supervisor's `audit` table, capturing the Decision
        (set by make_audit_aware_callback earlier in the same async task) and the
        tool result (or stringified exception).
      - If a new eid was captured by RecordingUndoLog during the invoke, writes
        a corresponding `audit_undo_link` row.
    """

    def __init__(self, *, audit_storage: Storage, agent_name: str) -> None:
        super().__init__()
        self._audit_storage = audit_storage
        self._agent_name = agent_name

    def register(self, tool: Tool) -> None:  # type: ignore[override]
        wrapped = self._wrap(tool)
        super().register(wrapped)

    def _wrap(self, tool: Tool) -> Tool:
        original_impl = tool.impl
        agent_name = self._agent_name
        storage = self._audit_storage
        tool_name = tool.name

        def audited_impl(*args, **kwargs):
            # Reset eid contextvar — only count eids written during THIS call
            _eid_contextvar.set(None)
            decision = _decision_contextvar.get()
            decision_source = decision.source if decision is not None else "unknown"

            args_json = json.dumps(kwargs if kwargs else list(args), default=str)

            try:
                result = original_impl(*args, **kwargs)
                result_str = str(result)
                exc: Optional[BaseException] = None
            except Exception as e:
                result_str = f"Error: {type(e).__name__}: {e}"
                exc = e

            captured_eid = _eid_contextvar.get()
            undoable = captured_eid is not None
            audit_id = storage.append_audit(
                agent=agent_name,
                tool=tool_name,
                args_json=args_json,
                decision=decision_source,
                result=result_str,
                undoable=undoable,
            )
            if captured_eid is not None:
                storage.link_audit_undo(audit_id, captured_eid)

            if exc is not None:
                raise exc
            return result

        return Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            impl=audited_impl,
        )


def make_audit_aware_callback(engine: PermissionEngine, *, agent_name: str):
    """Return an async permission_check that calls engine.check_async and stashes
    the resulting Decision into _decision_contextvar so AuditingToolRegistry can
    record it after the tool runs.

    Replaces engine.to_async_agent_callback when wiring an AssembledAgent.
    """

    async def _check(_runtime_agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
        d = await engine.check_async(agent=agent_name, tool=tool, arguments=args)
        _decision_contextvar.set(d)
        return (d.allowed, d.reason)

    return _check
