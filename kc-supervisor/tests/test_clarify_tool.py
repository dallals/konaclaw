import asyncio
import json
import pytest

from kc_supervisor.clarify.broker import ClarifyBroker
from kc_supervisor.clarify.tools import build_clarify_tool


@pytest.fixture
def broker():
    return ClarifyBroker()


@pytest.fixture
def tool(broker):
    ctx = {"conversation_id": 40, "agent": "Kona-AI",
           "channel": "dashboard", "chat_id": "dashboard:40"}
    return build_clarify_tool(broker=broker, current_context=lambda: ctx)


def test_tool_metadata(tool):
    assert tool.name == "clarify"
    assert "required" in tool.parameters
    assert set(tool.parameters["required"]) == {"question", "choices"}


@pytest.mark.asyncio
async def test_happy_path(tool, broker):
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.02)
        broker.resolve(seen[0]["request_id"], choice="Tuesday")

    asyncio.create_task(resolver())
    out = json.loads(await tool.impl(
        question="Which day?", choices=["Monday", "Tuesday"], timeout_seconds=2,
    ))
    assert out["choice"] == "Tuesday"
    assert out["choice_index"] == 1


@pytest.mark.asyncio
async def test_missing_question(tool):
    out = json.loads(await tool.impl(question="   ", choices=["a", "b"]))
    assert out == {"error": "missing_question"}


@pytest.mark.asyncio
async def test_missing_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=None))
    assert out == {"error": "missing_choices"}


@pytest.mark.asyncio
async def test_too_few_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=["only"]))
    assert out == {"error": "too_few_choices", "count": 1, "minimum": 2}


@pytest.mark.asyncio
async def test_too_many_choices(tool):
    out = json.loads(await tool.impl(question="Q?", choices=[str(i) for i in range(9)]))
    assert out == {"error": "too_many_choices", "count": 9, "maximum": 8}


@pytest.mark.asyncio
async def test_duplicate_choices(tool):
    out = json.loads(await tool.impl(
        question="Q?", choices=["A", "B", "A", "C", "B"],
    ))
    assert out["error"] == "duplicate_choices"
    assert set(out["values"]) == {"A", "B"}


@pytest.mark.asyncio
async def test_timeout_clamped_low(tool, broker):
    # Pass 1 → clamped to 10 (minimum). We won't wait the full 10s; we'll
    # resolve quickly and just check it didn't time out at 1s.
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.05)
        broker.resolve(seen[0]["request_id"], choice="A")

    asyncio.create_task(resolver())
    out = json.loads(await tool.impl(
        question="Q?", choices=["A", "B"], timeout_seconds=1,
    ))
    # Frame should reflect the clamped value.
    assert seen[0]["timeout_seconds"] == 10
    assert out["choice"] == "A"


@pytest.mark.asyncio
async def test_timeout_clamped_high(tool, broker):
    seen = []
    broker.subscribe(lambda f: seen.append(f))

    async def resolver():
        await asyncio.sleep(0.05)
        broker.resolve(seen[0]["request_id"], choice="A")

    asyncio.create_task(resolver())
    await tool.impl(question="Q?", choices=["A", "B"], timeout_seconds=99999)
    assert seen[0]["timeout_seconds"] == 600
