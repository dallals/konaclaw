from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol


class Tier(str, Enum):
    SAFE = "safe"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


@dataclass
class Decision:
    allowed: bool
    tier: Tier
    source: str            # "tier", "callback", "override"
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
    ) -> None:
        self.tier_map = dict(tier_map)
        self.agent_overrides = {a: dict(o) for a, o in agent_overrides.items()}
        self.approval_callback = approval_callback

    def check(self, agent: str, tool: str, arguments: dict[str, Any]) -> Decision:
        # Resolve effective tier
        override = self.agent_overrides.get(agent, {}).get(tool)
        if override is not None:
            tier = override
            source = "override"
        else:
            # Spec rule: unknown tools default to DESTRUCTIVE
            tier = self.tier_map.get(tool, Tier.DESTRUCTIVE)
            source = "tier"

        if tier in (Tier.SAFE, Tier.MUTATING):
            return Decision(allowed=True, tier=tier, source=source)

        # DESTRUCTIVE — ask the callback. If we got here because of an
        # override (not the default tier_map), record both facts in the
        # source so audit logs can distinguish "default DESTRUCTIVE → callback"
        # from "override raised tier → callback".
        allowed, reason = self.approval_callback(agent, tool, arguments)
        callback_source = "override+callback" if source == "override" else "callback"
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
