from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from kc_subagents.templates import SubagentTemplate

@dataclass
class EphemeralAgentConfig:
    """Subset of fields the supervisor's assemble_agent consumes for an ephemeral run.

    Mapped one-to-one to AgentConfig at the supervisor seam (assembly.py reads
    these fields). Keeping a separate dataclass avoids a cross-package import of
    AgentConfig into kc-subagents and lets us add ephemeral-only fields later.
    """
    name: str
    model: str
    system_prompt: str
    tool_whitelist: list[str] = field(default_factory=list)
    tool_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    mcp_action_filter: dict[str, list[str]] = field(default_factory=dict)
    memory_mode: str = "none"
    memory_scope: str | None = None
    shares: list[str] = field(default_factory=list)
    permission_overrides: dict[str, str] = field(default_factory=dict)
    model_options: dict[str, Any] = field(default_factory=dict)

def template_to_agent_config(
    t: SubagentTemplate, *, instance_id: str, parent_agent: str
) -> EphemeralAgentConfig:
    return EphemeralAgentConfig(
        name=f"{parent_agent}/{instance_id}/{t.name}",
        model=t.model,
        system_prompt=t.system_prompt,
        tool_whitelist=list(t.tools.keys()),
        tool_config=dict(t.tools),
        mcp_servers=list(t.mcp_servers),
        mcp_action_filter=dict(t.mcp_action_filter),
        memory_mode=t.memory.get("mode", "none"),
        memory_scope=t.memory.get("scope"),
        shares=list(t.shares),
        permission_overrides=dict(t.permission_overrides),
        model_options=dict(t.model_options),
    )


import asyncio, secrets, time
from typing import Callable, Optional


def _make_counted_impl(original_impl, instance: "EphemeralInstance", tool_name: str):
    """Return an async impl that wraps the original with counter + cap check."""
    async def _counted_impl(**kwargs):
        if instance.tool_calls_used >= instance.template.max_tool_calls:
            return (
                f"error: max_tool_calls cap reached "
                f"({instance.template.max_tool_calls})"
            )
        instance.tool_calls_used += 1
        result = original_impl(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return result
    return _counted_impl


def _wrap_tools_with_counter(assembled, instance: "EphemeralInstance") -> None:
    """Mutate the assembled agent's tools so each call bumps instance.tool_calls_used
    and short-circuits with an error string after max_tool_calls.

    Handles two shapes:
      - Fake agents (used in tests): a plain list at `assembled.tools`.
        Replaces that list with a list of new Tool objects wrapping the impls.
      - Real AssembledAgent: tools live in `assembled.core_agent.tools` as a
        ToolRegistry. Mutates each registered Tool's `.impl` in place (Tool is
        a mutable dataclass).
    """
    from kc_core.tools import Tool

    # Shape detection
    has_list = hasattr(assembled, "tools") and isinstance(getattr(assembled, "tools", None), list)
    has_registry = (
        hasattr(assembled, "core_agent")
        and hasattr(assembled.core_agent, "tools")
        and hasattr(assembled.core_agent.tools, "names")   # ToolRegistry shape
    )

    if has_list:
        wrapped: list = []
        for t in assembled.tools:
            wrapped.append(Tool(
                name=t.name, description=t.description, parameters=t.parameters,
                impl=_make_counted_impl(t.impl, instance, t.name),
            ))
        assembled.tools = wrapped
        return

    if has_registry:
        registry = assembled.core_agent.tools
        for name in list(registry.names()):
            tool = registry.get(name)
            tool.impl = _make_counted_impl(tool.impl, instance, name)
        return

    # Unknown shape — silently skip; tool count stays 0 but instance still runs.


@dataclass
class InstanceResult:
    subagent_id: str
    status: str            # ok | error | timeout | stopped
    reply: str | None
    duration_ms: int
    tool_calls_used: int
    error: str | None = None


def _gen_id() -> str:
    return "ep_" + secrets.token_hex(3)  # 6 hex chars


class EphemeralInstance:
    def __init__(
        self, *,
        instance_id: str,
        template: SubagentTemplate,
        parent_agent: str,
        parent_conversation_id: str,
        task: str,
        context: dict | None,
        label: str | None,
        effective_timeout: int,
        assembled,                                 # AssembledAgent or fake
        on_frame: Callable[[dict], None],
        audit_start: Callable[..., None],
        audit_finish: Callable[..., None],
    ):
        self.id = instance_id
        self.template = template
        self.parent_agent = parent_agent
        self.parent_conversation_id = parent_conversation_id
        self.task = task
        self.context = context
        self.label = label
        self.effective_timeout = effective_timeout
        self.assembled = assembled
        self._on_frame = on_frame
        self._audit_start = audit_start
        self._audit_finish = audit_finish
        self._task: asyncio.Task | None = None
        self._result_future: asyncio.Future[InstanceResult] = (
            asyncio.get_running_loop().create_future()
        )
        self.tool_calls_used = 0

    def _emit(self, frame: dict) -> None:
        self._on_frame({
            **frame,
            "subagent_id": self.id,
            "parent_conversation_id": self.parent_conversation_id,
        })

    def _compose_message(self) -> str:
        if not self.context:
            return self.task
        import json as _json
        return f"{self.task}\n\n## Context\n```json\n{_json.dumps(self.context, indent=2)}\n```"

    async def run(self) -> InstanceResult:
        started = time.monotonic()
        self._audit_start(
            id=self.id, parent_conversation_id=self.parent_conversation_id,
            parent_agent=self.parent_agent, template=self.template.name,
            label=self.label, task_preview=self.task,
            context_keys=list(self.context.keys()) if self.context else None,
        )
        self._emit({
            "type": "subagent_started",
            "template": self.template.name,
            "label": self.label,
            "task_preview": self.task[:200],
        })

        # Lazy import: subagent_attribution_var lives in kc_supervisor.approvals.
        # We accept a None fallback if kc_supervisor isn't installed (test isolation).
        token = None
        _attrib_var = None
        try:
            from kc_supervisor.approvals import subagent_attribution_var as _sv
            _attrib_var = _sv
            token = _attrib_var.set({
                "parent_agent": self.parent_agent,
                "subagent_id":  self.id,
            })
        except ImportError:
            pass

        reply: str | None = None
        status = "ok"
        error: str | None = None
        raise_after = False
        try:
            assistant = await asyncio.wait_for(
                self.assembled.core_agent.send(self._compose_message()),
                timeout=self.effective_timeout,
            )
            reply = (getattr(assistant, "content", "") or "").strip()
        except asyncio.TimeoutError:
            status = "timeout"
            error  = f"timed out after {self.effective_timeout}s"
        except asyncio.CancelledError:
            status = "stopped"
            error  = "stopped by user"
            raise_after = True
        except Exception as e:                       # noqa: BLE001
            status = "error"
            error  = str(e)
        else:
            raise_after = False
        finally:
            if token is not None and _attrib_var is not None:
                try:
                    _attrib_var.reset(token)
                except (LookupError, ValueError):
                    pass   # contextvar token reset can fail in unusual paths; safe to ignore

        duration_ms = int((time.monotonic() - started) * 1000)
        result = InstanceResult(
            subagent_id=self.id, status=status, reply=reply,
            duration_ms=duration_ms, tool_calls_used=self.tool_calls_used, error=error,
        )
        self._emit({
            "type": "subagent_finished",
            "status": status,
            "reply_preview": (reply or "")[:400],
            "duration_ms": duration_ms,
            "tool_calls_used": self.tool_calls_used,
            "error_message": error,
        })
        self._audit_finish(
            id=self.id, status=status, duration_ms=duration_ms,
            tool_calls_used=self.tool_calls_used,
            reply_chars=len(reply) if reply else 0, error_message=error,
        )
        if not self._result_future.done():
            self._result_future.set_result(result)
        if raise_after:
            # Propagate cancellation only after we've recorded the terminal state.
            raise asyncio.CancelledError()
        return result

    async def wait(self, ceiling_seconds: int | None = None) -> InstanceResult:
        if ceiling_seconds is None:
            return await self._result_future
        return await asyncio.wait_for(asyncio.shield(self._result_future), timeout=ceiling_seconds)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


class SubagentRunner:
    """Owns the in-flight registry. One per supervisor."""

    PER_CONV_CAP = 4
    GLOBAL_CAP   = 16

    def __init__(
        self, *,
        build_assembled: Callable[[EphemeralAgentConfig], object],
        audit_start: Callable[..., None],
        audit_finish: Callable[..., None],
        on_frame: Callable[[dict], None],
    ):
        self._build_assembled = build_assembled
        self._audit_start = audit_start
        self._audit_finish = audit_finish
        self._on_frame = on_frame
        self._instances: dict[str, EphemeralInstance] = {}
        self._completed: dict[str, InstanceResult] = {}  # result cache for already-finished instances

    def _count_in_flight(self, parent_conversation_id: str | None = None) -> int:
        if parent_conversation_id is None:
            return len(self._instances)
        return sum(
            1 for i in self._instances.values()
            if i.parent_conversation_id == parent_conversation_id
        )

    def spawn(
        self, *,
        template: SubagentTemplate,
        task: str,
        context: dict | None,
        label: str | None,
        parent_conversation_id: str,
        parent_agent: str,
        timeout_override: int | None,
    ) -> str:
        if self._count_in_flight(parent_conversation_id) >= self.PER_CONV_CAP:
            raise RuntimeError(
                f"too many in-flight subagents on this conversation "
                f"({self.PER_CONV_CAP}/{self.PER_CONV_CAP}); await some before spawning more"
            )
        if self._count_in_flight() >= self.GLOBAL_CAP:
            raise RuntimeError(
                f"supervisor in-flight subagent cap reached "
                f"({self.GLOBAL_CAP}/{self.GLOBAL_CAP}); retry shortly"
            )
        eff_timeout = template.timeout_seconds
        if timeout_override is not None:
            if timeout_override < 10 or timeout_override > template.timeout_seconds:
                raise RuntimeError(
                    f"timeout_seconds {timeout_override} exceeds template max "
                    f"({template.timeout_seconds})"
                )
            eff_timeout = timeout_override
        instance_id = _gen_id()
        while instance_id in self._instances:
            instance_id = _gen_id()
        cfg = template_to_agent_config(template, instance_id=instance_id, parent_agent=parent_agent)
        assembled = self._build_assembled(cfg)
        inst = EphemeralInstance(
            instance_id=instance_id, template=template,
            parent_agent=parent_agent, parent_conversation_id=parent_conversation_id,
            task=task, context=context, label=label,
            effective_timeout=eff_timeout, assembled=assembled,
            on_frame=self._on_frame,
            audit_start=self._audit_start, audit_finish=self._audit_finish,
        )

        # Wrap the tools so they bump inst.tool_calls_used and short-circuit at cap.
        # Handles both list shape (fake agents in tests) and ToolRegistry shape
        # (real AssembledAgent with core_agent.tools). Shape detection is inside
        # _wrap_tools_with_counter; unknown shapes are silently skipped.
        _wrap_tools_with_counter(assembled, inst)

        self._instances[instance_id] = inst

        async def _run_and_clean():
            try:
                result = await inst.run()
                self._completed[instance_id] = result
            finally:
                self._instances.pop(instance_id, None)

        inst._task = asyncio.create_task(_run_and_clean())
        return instance_id

    async def await_one(self, instance_id: str, *, ceiling_seconds: int | None) -> InstanceResult:
        # Fast path: already completed.
        cached = self._completed.get(instance_id)
        if cached is not None:
            return cached
        inst = self._instances.get(instance_id)
        if inst is None:
            return InstanceResult(
                subagent_id=instance_id, status="error", reply=None,
                duration_ms=0, tool_calls_used=0, error="unknown subagent_id",
            )
        return await inst.wait(ceiling_seconds=ceiling_seconds)

    def stop(self, instance_id: str) -> bool:
        inst = self._instances.get(instance_id)
        if not inst:
            return False
        inst.stop()
        return True

    def active(self) -> list[dict]:
        return [{
            "subagent_id": i.id,
            "template": i.template.name,
            "label": i.label,
            "parent_conversation_id": i.parent_conversation_id,
            "started_ts": None,  # filled by audit query if needed
            "tool_calls_used": i.tool_calls_used,
        } for i in self._instances.values()]
