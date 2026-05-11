import asyncio
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker


@pytest.mark.asyncio
async def test_request_allocates_id_and_publishes():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda frame: seen.append(frame))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice="A")

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out["choice"] == "A"
    assert out["choice_index"] == 0
    assert "elapsed_ms" in out
    assert len(seen) == 1
    assert seen[0]["type"] == "clarify_request"
    assert seen[0]["question"] == "Q?"
    assert seen[0]["choices"] == ["A", "B"]


@pytest.mark.asyncio
async def test_skip_returns_skipped_payload():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda frame: seen.append(frame))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice=None, reason="skipped")

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out == {"choice": None, "reason": "skipped"}


@pytest.mark.asyncio
async def test_timeout_returns_timeout_payload():
    broker = ClarifyBroker()
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=0.1,
    )
    assert out["choice"] is None
    assert out["reason"] == "timeout"
    assert out["elapsed_ms"] >= 80
    assert out["elapsed_ms"] < 1000


def test_resolve_unknown_id_is_noop():
    broker = ClarifyBroker()
    # Should not raise.
    broker.resolve("does-not-exist", choice="A")


@pytest.mark.asyncio
async def test_resolve_already_resolved_is_noop():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.01)
        rid = seen[0]["request_id"]
        broker.resolve(rid, choice="A")
        broker.resolve(rid, choice="B")  # second resolve, must not raise

    asyncio.create_task(resolver())
    out = await broker.request_clarification(
        conversation_id=40, agent="Kona-AI",
        question="Q?", choices=["A", "B"], timeout_seconds=2,
    )
    assert out["choice"] == "A"


@pytest.mark.asyncio
async def test_pending_for_conversation():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        # Start two in conv 40, one in conv 41.
        t40a = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="A?", choices=["x", "y"], timeout_seconds=10,
        ))
        t40b = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="B?", choices=["x", "y"], timeout_seconds=10,
        ))
        t41  = asyncio.create_task(broker.request_clarification(
            conversation_id=41, agent="Kona-AI",
            question="C?", choices=["x", "y"], timeout_seconds=10,
        ))
        await asyncio.sleep(0.05)
        pending40 = broker.pending_for_conversation(40)
        pending41 = broker.pending_for_conversation(41)
        assert len(pending40) == 2
        assert len(pending41) == 1
        assert {p["question"] for p in pending40} == {"A?", "B?"}
        assert pending41[0]["question"] == "C?"
        # Resolve all so the tasks finish.
        for f in seen:
            broker.resolve(f["request_id"], choice="x")
        await asyncio.gather(t40a, t40b, t41)

    await driver()


@pytest.mark.asyncio
async def test_concurrent_requests_unique_ids():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        tasks = [
            asyncio.create_task(broker.request_clarification(
                conversation_id=40, agent="Kona-AI",
                question=f"Q{i}", choices=["x", "y"], timeout_seconds=10,
            )) for i in range(5)
        ]
        await asyncio.sleep(0.05)
        rids = {f["request_id"] for f in seen}
        assert len(rids) == 5
        for f in seen:
            broker.resolve(f["request_id"], choice="x")
        await asyncio.gather(*tasks)

    await driver()


@pytest.mark.asyncio
async def test_subscriber_exception_swallowed():
    broker = ClarifyBroker()
    broker.subscribe(lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    captured = []
    broker.subscribe(lambda f: captured.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["x", "y"], timeout_seconds=10,
        ))
        await asyncio.sleep(0.05)
        # The good subscriber still got the frame despite the bad one raising.
        assert len(captured) == 1
        broker.resolve(captured[0]["request_id"], choice="x")
        await task

    await driver()
