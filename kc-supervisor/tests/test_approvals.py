import asyncio
import pytest
from kc_supervisor.approvals import ApprovalBroker, ApprovalRequest


@pytest.mark.asyncio
async def test_resolve_request_fulfills_waiter():
    b = ApprovalBroker()
    seen = []
    b.subscribe(lambda req: seen.append(req))
    task = asyncio.create_task(b.request_approval(agent="kc", tool="file.delete", arguments={}))
    await asyncio.sleep(0)  # let the task post the request
    assert len(seen) == 1
    req_id = seen[0].request_id
    b.resolve(req_id, allowed=True, reason=None)
    allowed, reason = await task
    assert allowed is True


@pytest.mark.asyncio
async def test_deny_request():
    b = ApprovalBroker()
    seen = []
    b.subscribe(lambda req: seen.append(req))
    task = asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    await asyncio.sleep(0)
    b.resolve(seen[0].request_id, allowed=False, reason="user said no")
    allowed, reason = await task
    assert allowed is False
    assert reason == "user said no"


@pytest.mark.asyncio
async def test_resolve_unknown_request_id_is_no_op():
    b = ApprovalBroker()
    b.resolve("nonexistent", allowed=True, reason=None)  # must not raise


@pytest.mark.asyncio
async def test_pending_lists_open_requests():
    b = ApprovalBroker()
    asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    asyncio.create_task(b.request_approval(agent="kc", tool="y", arguments={}))
    await asyncio.sleep(0)
    p = b.pending()
    assert len(p) == 2


@pytest.mark.asyncio
async def test_unsubscribe_stops_notifications():
    b = ApprovalBroker()
    seen = []
    handle = b.subscribe(lambda req: seen.append(req))
    handle.unsubscribe()
    asyncio.create_task(b.request_approval(agent="kc", tool="x", arguments={}))
    await asyncio.sleep(0)
    assert seen == []
