"""Sub-agent delegation tool.

Injects a `delegate_to_agent` tool into each AssembledAgent at assembly
time. When a parent agent calls it, the supervisor:

  1. Resolves the target agent via a late-bound resolver (so the registry
     can be passed in before all agents are assembled).
  2. Checks the in-flight delegation chain (contextvar) for loop and depth
     violations and rejects with a descriptive error string (returned as
     the tool result, NOT raised — keeps the parent's loop alive).
  3. Calls `target.core_agent.send(message)` synchronously and returns the
     resulting AssistantMessage's text content.

The chain contextvar tracks agent NAMES across nested delegations within
a single asyncio task; the per-cid lock in ws_routes guarantees no
cross-conversation bleed of the contextvar.

Tier: SAFE (no approval queue). Loop guard + depth limit are the safety
net — promote to MUTATING/DESTRUCTIVE per-agent via permission_overrides
if a parent grows powerful enough that you want each delegation gated.
"""
from __future__ import annotations
import contextvars
from typing import Callable, Optional
from kc_core.tools import Tool


# Chain of agent names currently being awaited in a delegation tree.
# len(chain) == current depth. Resets per asyncio task; the per-cid lock
# in ws_routes prevents cross-conversation contamination.
_delegation_chain: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "kc_supervisor_delegation_chain", default=(),
)


def get_delegation_chain() -> tuple[str, ...]:
    """Test/inspection accessor."""
    return _delegation_chain.get()


# Resolver returns (assembled_agent_or_none, status_string).
# status_string lets the tool report "degraded" vs "unknown" distinctly.
ResolveAgent = Callable[[str], tuple[Optional[object], str]]


def make_delegate_tool(
    resolve_agent: ResolveAgent,
    *,
    parent_name: str,
    depth_limit: int = 1,
) -> Tool:
    """Build a `delegate_to_agent` tool bound to `parent_name`.

    `resolve_agent(name)` is late-bound so the registry can hand back
    AssembledAgents that didn't exist when the parent was being assembled.
    Returns (assembled, status) where status is "ok"/"unknown"/"degraded".

    `depth_limit` is enforced from the parent's perspective:
      - depth_limit=1 means the parent (depth 0) may delegate, but the
        target (depth 1) MAY NOT delegate further.
      - depth_limit=2 allows one extra layer of nesting, etc.
    """

    async def delegate(target: str, message: str) -> str:
        chain = _delegation_chain.get()

        # Self-delegation is always pointless; reject before resolving.
        if target == parent_name:
            return f"error: cannot delegate to self ({parent_name!r})"

        # Loop guard: if target is already being awaited up the chain.
        if target in chain:
            path = " -> ".join((*chain, target))
            return f"error: delegation loop detected ({path})"

        # Depth cap. Chain length == nesting beyond the user-initiated chat.
        # depth_limit is measured from the original parent: chain has the
        # in-flight nested targets (NOT including parent_name itself).
        if len(chain) >= depth_limit:
            return (
                f"error: delegation depth limit reached "
                f"(limit={depth_limit}, current chain={list(chain)})"
            )

        assembled, status = resolve_agent(target)
        if status == "unknown":
            return f"error: unknown agent {target!r}"
        if status == "degraded" or assembled is None:
            return f"error: agent {target!r} is degraded; cannot delegate"

        # Run the target with the delegation chain extended.
        token = _delegation_chain.set((*chain, target))
        try:
            # Fresh history per delegation — the child does NOT see the
            # parent's conversation. The tool result string is the only
            # information that flows back up.
            child_agent = assembled.core_agent
            saved_history = list(child_agent.history)
            child_agent.history = []
            try:
                reply = await child_agent.send(message)
            finally:
                child_agent.history = saved_history
            text = (reply.content or "").strip()
            return text or "(empty reply)"
        finally:
            _delegation_chain.reset(token)

    return Tool(
        name="delegate_to_agent",
        description=(
            "Delegate a task to another agent on this supervisor. The target "
            "agent runs in a fresh context and returns a single text reply. "
            "Use this when a sibling agent specializes in the work at hand."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the agent to delegate to.",
                },
                "message": {
                    "type": "string",
                    "description": "The task or question to send to the target agent.",
                },
            },
            "required": ["target", "message"],
        },
        impl=delegate,
    )
