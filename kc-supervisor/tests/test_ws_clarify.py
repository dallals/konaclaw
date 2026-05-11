import asyncio
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker


@pytest.mark.asyncio
async def test_broker_resolve_via_response_handler():
    """Simulate ws_routes handling a clarify_response: route it to broker.resolve.
    This is a logic test, not a WS-protocol test — see SMOKE for the live path."""
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["A", "B"], timeout_seconds=2,
        ))
        await asyncio.sleep(0.02)
        rid = seen[0]["request_id"]
        # Simulate the WS handler routing an inbound frame:
        broker.resolve(rid, choice="B")
        out = await task
        assert out["choice"] == "B"
        assert out["choice_index"] == 1

    await driver()


@pytest.mark.asyncio
async def test_pending_for_conversation_after_reconnect_snapshot():
    broker = ClarifyBroker()
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def driver():
        task = asyncio.create_task(broker.request_clarification(
            conversation_id=40, agent="Kona-AI",
            question="Q?", choices=["A", "B"], timeout_seconds=5,
        ))
        await asyncio.sleep(0.02)
        # On WS reconnect for conv 40, the handler calls pending_for_conversation
        # and re-sends each frame. Snapshot here:
        snapshot = broker.pending_for_conversation(40)
        assert len(snapshot) == 1
        assert snapshot[0]["question"] == "Q?"
        assert snapshot[0]["type"] == "clarify_request"
        # Resolve so the task finishes:
        broker.resolve(snapshot[0]["request_id"], choice="A")
        await task

    await driver()
