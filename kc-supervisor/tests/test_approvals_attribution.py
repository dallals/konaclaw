import asyncio
import pytest
from kc_supervisor.approvals import (
    ApprovalBroker, ApprovalRequest, subagent_attribution_var,
)


@pytest.mark.asyncio
async def test_request_picks_up_attribution_from_contextvar():
    broker = ApprovalBroker()
    captured: list[ApprovalRequest] = []

    def on_request(req: ApprovalRequest) -> None:
        captured.append(req)
        # Resolve immediately so request_approval returns.
        broker.resolve(req.request_id, allowed=True, reason=None)

    sub = broker.subscribe(on_request)
    try:
        token = subagent_attribution_var.set(
            {"parent_agent": "Kona-AI", "subagent_id": "ep_test"}
        )
        try:
            allowed, _ = await broker.request_approval(
                agent="web-researcher", tool="terminal_run", arguments={},
            )
        finally:
            subagent_attribution_var.reset(token)
    finally:
        sub.unsubscribe()

    assert allowed is True
    assert len(captured) == 1
    assert captured[0].parent_agent == "Kona-AI"
    assert captured[0].subagent_id == "ep_test"


@pytest.mark.asyncio
async def test_request_attribution_defaults_to_none_when_contextvar_unset():
    broker = ApprovalBroker()
    captured: list[ApprovalRequest] = []

    def on_request(req: ApprovalRequest) -> None:
        captured.append(req)
        broker.resolve(req.request_id, allowed=True, reason=None)

    sub = broker.subscribe(on_request)
    try:
        # Do NOT set the contextvar.
        await broker.request_approval(
            agent="Kona-AI", tool="terminal_run", arguments={},
        )
    finally:
        sub.unsubscribe()

    assert captured[0].parent_agent is None
    assert captured[0].subagent_id is None


@pytest.mark.asyncio
async def test_attribution_var_is_task_local():
    """Two concurrent request_approval calls with different contextvar values
    must see the correct attribution each — contextvars are task-local."""
    broker = ApprovalBroker()
    captured: list[ApprovalRequest] = []

    def on_request(req: ApprovalRequest) -> None:
        captured.append(req)
        broker.resolve(req.request_id, allowed=True, reason=None)

    sub = broker.subscribe(on_request)

    async def with_attrib(parent: str, sid: str):
        token = subagent_attribution_var.set(
            {"parent_agent": parent, "subagent_id": sid}
        )
        try:
            await broker.request_approval(
                agent=sid, tool="terminal_run", arguments={},
            )
        finally:
            subagent_attribution_var.reset(token)

    try:
        await asyncio.gather(
            with_attrib("Kona-AI", "ep_a"),
            with_attrib("Kona-AI", "ep_b"),
        )
    finally:
        sub.unsubscribe()

    sids = sorted(r.subagent_id for r in captured)
    assert sids == ["ep_a", "ep_b"]
