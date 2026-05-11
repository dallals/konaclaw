from __future__ import annotations
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Protocol


class Tier(str, Enum):
    SAFE = "safe"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


@dataclass
class Decision:
    allowed: bool
    tier: Tier
    source: str            # "tier", "callback", "override", "override+callback",
                           #  "resolver", "resolver+callback"
    reason: Optional[str] = None


class ApprovalCallback(Protocol):
    """Returns (allowed, reason). Called only for DESTRUCTIVE tier."""
    def __call__(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[bool, Optional[str]]: ...


class AlwaysAllow:
    def __call__(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[bool, Optional[str]]:
        return (True, None)


@dataclass
class AlwaysDeny:
    reason: Optional[str] = None

    def __call__(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[bool, Optional[str]]:
        return (False, self.reason)


class PermissionEngine:
    def __init__(
        self,
        tier_map: dict[str, Tier],
        agent_overrides: dict[str, dict[str, Tier]],
        approval_callback: ApprovalCallback,
        tier_resolvers: dict[str, "Callable[[dict[str, Any]], Tier]"] | None = None,
    ) -> None:
        self.tier_map = dict(tier_map)
        self.agent_overrides = {a: dict(o) for a, o in agent_overrides.items()}
        self.approval_callback = approval_callback
        self.tier_resolvers = dict(tier_resolvers or {})

    def _resolve_tier(self, agent: str, tool: str, arguments: dict[str, Any]) -> tuple[Tier, str]:
        override = self.agent_overrides.get(agent, {}).get(tool)
        if override is not None:
            return override, "override"
        resolver = self.tier_resolvers.get(tool)
        if resolver is not None:
            try:
                return resolver(arguments), "resolver"
            except Exception:
                # Resolver crashed (malformed args, buggy classifier, etc.).
                # Fail closed: require approval rather than silently propagating.
                return Tier.DESTRUCTIVE, "resolver"
        return self.tier_map.get(tool, Tier.DESTRUCTIVE), "tier"

    def check(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        tier, source = self._resolve_tier(agent, tool, arguments)
        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)
        allowed, reason = self.approval_callback(agent, tool, arguments)
        callback_source = (
            "override+callback" if source == "override"
            else "resolver+callback" if source == "resolver"
            else "callback"
        )
        return Decision(allowed=allowed, tier=tier, source=callback_source, reason=reason)

    def to_agent_callback(self, agent: str):
        """Returns a callable in the shape kc_core.Agent.permission_check expects.

        The closure binds to `agent` — the agent_name passed at runtime by
        kc-core is ignored in favor of the bound name. This guarantees that
        per-agent overrides for `agent` apply regardless of what kc-core sends.
        """
        def _check(agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
            d = self.check(agent=agent, tool=tool, arguments=args)
            return (d.allowed, d.reason)
        return _check

    async def check_async(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        tier, source = self._resolve_tier(agent, tool, arguments)
        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)
        result = self.approval_callback(agent, tool, arguments)
        if inspect.iscoroutine(result):
            result = await result
        allowed, reason = result
        callback_source = (
            "override+callback" if source == "override"
            else "resolver+callback" if source == "resolver"
            else "callback"
        )
        return Decision(allowed=allowed, tier=tier, source=callback_source, reason=reason)

    def to_async_agent_callback(self, agent: str):
        """Returns an async callable in the shape kc_core.Agent.permission_check expects.

        Like to_agent_callback, the closure binds to `agent` — the agent_name
        passed at runtime by kc-core is ignored.
        """
        async def _check(agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
            d = await self.check_async(agent=agent, tool=tool, arguments=args)
            return (d.allowed, d.reason)
        return _check
