from __future__ import annotations
import contextvars
import inspect
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

        def _write_audit(result_str: str, decision_source: str, args_json: str) -> None:
            from kc_supervisor.approvals import subagent_attribution_var
            captured_eid = _eid_contextvar.get()
            attrib = subagent_attribution_var.get()
            parent_agent      = (attrib or {}).get("parent_agent")
            subagent_id       = (attrib or {}).get("subagent_id")
            subagent_template = None
            if attrib and parent_agent and "/" in agent_name:
                # Synthetic ephemeral name: "<parent>/<instance_id>/<template>"
                subagent_template = agent_name.rsplit("/", 1)[-1]
            audit_id = storage.append_audit(
                agent=agent_name,
                tool=tool_name,
                args_json=args_json,
                decision=decision_source,
                result=result_str,
                undoable=captured_eid is not None,
                parent_agent=parent_agent,
                subagent_id=subagent_id,
                subagent_template=subagent_template,
            )
            if captured_eid is not None:
                storage.link_audit_undo(audit_id, captured_eid)

        # Two impl variants: keep sync tools sync (so existing sync callers
        # like reg.invoke() in tests continue to work) and only return a
        # coroutine when the underlying impl is itself async. The kc-core
        # agent loop awaits coroutine results either way.
        if inspect.iscoroutinefunction(original_impl):
            async def audited_impl(*args, **kwargs):
                _eid_contextvar.set(None)
                decision = _decision_contextvar.get()
                decision_source = decision.source if decision is not None else "unknown"
                args_json = json.dumps(kwargs if kwargs else list(args), default=str)
                try:
                    result = await original_impl(*args, **kwargs)
                except Exception as e:
                    _write_audit(f"Error: {type(e).__name__}: {e}", decision_source, args_json)
                    raise
                _write_audit(str(result), decision_source, args_json)
                return result
        else:
            def audited_impl(*args, **kwargs):
                _eid_contextvar.set(None)
                decision = _decision_contextvar.get()
                decision_source = decision.source if decision is not None else "unknown"
                args_json = json.dumps(kwargs if kwargs else list(args), default=str)
                try:
                    result = original_impl(*args, **kwargs)
                except Exception as e:
                    _write_audit(f"Error: {type(e).__name__}: {e}", decision_source, args_json)
                    raise
                _write_audit(str(result), decision_source, args_json)
                return result

        return Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            impl=audited_impl,
        )


def make_audit_aware_callback(
    engine: PermissionEngine, *, agent_name: str, storage: Optional[Storage] = None,
):
    """Return an async permission_check that calls engine.check_async, stashes
    the resulting Decision in _decision_contextvar, and writes an audit row
    when the decision is a deny.

    Storage is optional only because some test setups don't need a DB; in
    production the supervisor always passes it.

    Replaces engine.to_async_agent_callback when wiring an AssembledAgent.
    """

    async def _check(_runtime_agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
        d = await engine.check_async(agent=agent_name, tool=tool, arguments=args)
        _decision_contextvar.set(d)
        if not d.allowed and storage is not None:
            storage.append_audit(
                agent=agent_name,
                tool=tool,
                args_json=json.dumps(args, default=str),
                decision="denied",
                result=json.dumps({"reason": d.reason, "source": d.source}),
                undoable=False,
            )
        return (d.allowed, d.reason)

    return _check
