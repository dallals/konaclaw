import asyncio
import pytest
from kc_supervisor.locks import ConversationLocks


def test_get_returns_lock():
    cl = ConversationLocks()
    lock = cl.get(1)
    assert isinstance(lock, asyncio.Lock)


def test_same_cid_returns_same_lock():
    cl = ConversationLocks()
    assert cl.get(1) is cl.get(1)


def test_different_cids_get_different_locks():
    cl = ConversationLocks()
    assert cl.get(1) is not cl.get(2)


@pytest.mark.asyncio
async def test_concurrent_acquires_on_same_cid_serialize():
    """If task A holds the lock for cid=1, task B has to wait."""
    cl = ConversationLocks()
    order: list[str] = []

    async def task_a():
        async with cl.get(1):
            order.append("a-start")
            await asyncio.sleep(0.05)
            order.append("a-end")

    async def task_b():
        await asyncio.sleep(0.01)  # ensure A grabs first
        async with cl.get(1):
            order.append("b-start")
            order.append("b-end")

    await asyncio.gather(task_a(), task_b())
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_different_cids_run_in_parallel():
    """Two concurrent acquires on different cids do not block each other."""
    cl = ConversationLocks()
    started: list[int] = []
    finished: list[int] = []

    async def task(cid: int):
        async with cl.get(cid):
            started.append(cid)
            await asyncio.sleep(0.05)
            finished.append(cid)

    await asyncio.gather(task(1), task(2))
    assert sorted(started) == [1, 2]
    assert sorted(finished) == [1, 2]
