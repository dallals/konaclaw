import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kc_web.budget import BudgetStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "budget.sqlite"


@pytest.fixture
def store(db_path: Path) -> BudgetStore:
    return BudgetStore(
        db_path=db_path,
        session_id="sess-A",
        session_soft_cap=5,
        daily_hard_cap=10,
    )


@pytest.mark.asyncio
async def test_first_call_allowed(store: BudgetStore):
    ok, err = await store.check_and_record("web_fetch")
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_session_soft_cap(store: BudgetStore):
    for _ in range(5):
        ok, err = await store.check_and_record("web_fetch")
        assert ok is True
    # 6th in same session -> rejected
    ok, err = await store.check_and_record("web_fetch")
    assert ok is False
    assert err == {"error": "session_cap_exceeded", "limit": 5}


@pytest.mark.asyncio
async def test_daily_hard_cap_across_sessions(db_path: Path):
    # Fill 10 calls across 3 sessions.
    for sess in ("s1", "s2", "s3"):
        store = BudgetStore(
            db_path=db_path,
            session_id=sess,
            session_soft_cap=5,
            daily_hard_cap=10,
        )
        # 4 calls per session for 12 total attempts; cap kicks in at 11th overall.
        for _ in range(4):
            await store.check_and_record("web_fetch")
    # New session, daily cap already at 10 (3*4=12 attempted, but 10 succeeded
    # before cap; sessions s1+s2 succeeded fully, s3 blocked at 3rd).
    # Re-create store and try one more call:
    store = BudgetStore(
        db_path=db_path,
        session_id="s4",
        session_soft_cap=5,
        daily_hard_cap=10,
    )
    ok, err = await store.check_and_record("web_fetch")
    assert ok is False
    assert err == {"error": "daily_cap_exceeded", "limit": 10}


@pytest.mark.asyncio
async def test_blocked_calls_recorded(db_path: Path):
    store = BudgetStore(
        db_path=db_path,
        session_id="s",
        session_soft_cap=2,
        daily_hard_cap=100,
    )
    for _ in range(2):
        await store.check_and_record("web_search")
    await store.check_and_record("web_search")  # blocked
    # Direct DB read -> 3 rows total, 1 with blocked=1
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT blocked FROM web_calls").fetchall()
    assert len(rows) == 3
    assert sum(r[0] for r in rows) == 1


@pytest.mark.asyncio
async def test_summary(db_path: Path):
    store = BudgetStore(
        db_path=db_path,
        session_id="s-X",
        session_soft_cap=5,
        daily_hard_cap=10,
    )
    for _ in range(3):
        await store.check_and_record("web_fetch")
    s = store.summary()
    assert s["session_id"] == "s-X"
    assert s["session_count"] == 3
    assert s["session_cap"] == 5
    assert s["daily_count"] == 3
    assert s["daily_cap"] == 10
    assert s["day_utc"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_concurrent_calls_serialized(db_path: Path):
    store = BudgetStore(
        db_path=db_path,
        session_id="s",
        session_soft_cap=5,
        daily_hard_cap=100,
    )
    results = await asyncio.gather(
        *(store.check_and_record("web_fetch") for _ in range(20))
    )
    allowed = sum(1 for ok, _ in results if ok)
    blocked = sum(1 for ok, _ in results if not ok)
    assert allowed == 5
    assert blocked == 15


@pytest.mark.asyncio
async def test_day_rollover(db_path: Path, monkeypatch):
    """When day_utc changes, daily counter resets but session counter does not."""
    store = BudgetStore(
        db_path=db_path,
        session_id="s",
        session_soft_cap=100,
        daily_hard_cap=3,
    )
    # Simulate day 1
    monkeypatch.setattr(
        "kc_web.budget._today_utc",
        lambda: "2026-05-10",
    )
    for _ in range(3):
        ok, _ = await store.check_and_record("web_fetch")
        assert ok
    ok, err = await store.check_and_record("web_fetch")
    assert not ok
    assert err == {"error": "daily_cap_exceeded", "limit": 3}

    # Simulate day 2 -> daily counter resets
    monkeypatch.setattr(
        "kc_web.budget._today_utc",
        lambda: "2026-05-11",
    )
    ok, err = await store.check_and_record("web_fetch")
    assert ok
    assert err is None
