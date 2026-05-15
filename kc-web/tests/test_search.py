import json
from pathlib import Path

import pytest

from kc_web.budget import BudgetStore
from kc_web.client import WebClientError, SearchResult
from kc_web.config import WebConfig
from kc_web.search import build_web_search_impl


class FakeClient:
    def __init__(self, results=None, exc=None):
        self.results = results or []
        self.exc = exc
        self.calls: list[tuple[str, int, str]] = []

    async def search(self, query, max_results, freshness):
        self.calls.append((query, max_results, freshness))
        if self.exc:
            raise self.exc
        return self.results


@pytest.fixture
def cfg(tmp_path: Path) -> WebConfig:
    return WebConfig(
        firecrawl_api_key="k",
        session_soft_cap=10,
        daily_hard_cap=100,
        fetch_cap_bytes=1024,
        default_search_max_results=5,
        default_fetch_timeout_s=30,
        budget_db_path=tmp_path / "b.sqlite",
        extra_blocked_hosts=(),
        session_id="s",
    )


@pytest.fixture
def budget(cfg: WebConfig) -> BudgetStore:
    return BudgetStore(
        db_path=cfg.budget_db_path,
        session_id=cfg.session_id,
        session_soft_cap=cfg.session_soft_cap,
        daily_hard_cap=cfg.daily_hard_cap,
    )


@pytest.mark.asyncio
async def test_happy_path(cfg, budget):
    client = FakeClient(results=[
        SearchResult("Title A", "https://a.example", "snip A"),
        SearchResult("Title B", "https://b.example", "snip B"),
    ])
    impl = build_web_search_impl(cfg, client, budget)
    out = json.loads(await impl(query="claude opus", max_results=2))
    assert out["query"] == "claude opus"
    assert out["result_count"] == 2
    assert out["results"][0] == {
        "title": "Title A", "url": "https://a.example", "snippet": "snip A",
    }
    assert "duration_ms" in out
    assert client.calls == [("claude opus", 2, "any")]


@pytest.mark.asyncio
async def test_default_max_results(cfg, budget):
    client = FakeClient(results=[])
    impl = build_web_search_impl(cfg, client, budget)
    await impl(query="x")
    assert client.calls[0][1] == 5  # cfg.default_search_max_results


@pytest.mark.asyncio
async def test_max_results_clamped(cfg, budget):
    client = FakeClient(results=[])
    impl = build_web_search_impl(cfg, client, budget)
    await impl(query="x", max_results=999)
    assert client.calls[0][1] == 25
    await impl(query="x", max_results=0)
    assert client.calls[1][1] == 1


@pytest.mark.asyncio
async def test_invalid_freshness(cfg, budget):
    client = FakeClient(results=[])
    impl = build_web_search_impl(cfg, client, budget)
    out = json.loads(await impl(query="x", freshness="hourly"))
    assert out == {"error": "invalid_freshness", "value": "hourly"}
    assert client.calls == []  # firecrawl never called


@pytest.mark.asyncio
async def test_missing_query(cfg, budget):
    client = FakeClient()
    impl = build_web_search_impl(cfg, client, budget)
    out = json.loads(await impl(query=""))
    assert out == {"error": "missing_query"}
    assert client.calls == []


@pytest.mark.asyncio
async def test_backend_error(cfg, budget):
    client = FakeClient(exc=WebClientError(429, "rate limited"))
    impl = build_web_search_impl(cfg, client, budget)
    out = json.loads(await impl(query="x"))
    assert out == {"error": "backend_error", "status": 429, "message": "web backend error status=429: rate limited"}


@pytest.mark.asyncio
async def test_budget_blocks_call(cfg, tmp_path):
    # Soft cap of 1 -> first call allowed, second blocked.
    budget = BudgetStore(
        db_path=tmp_path / "b.sqlite",
        session_id="s",
        session_soft_cap=1,
        daily_hard_cap=10,
    )
    client = FakeClient(results=[])
    impl = build_web_search_impl(cfg, client, budget)
    await impl(query="x")
    out = json.loads(await impl(query="x"))
    assert out == {"error": "session_cap_exceeded", "limit": 1}
    assert len(client.calls) == 1  # second call never reached firecrawl


@pytest.mark.asyncio
async def test_timeout(cfg, budget, monkeypatch):
    """If search hangs past the internal timeout, return timeout error."""
    import asyncio as _asyncio

    class HangingClient:
        async def search(self, query, max_results, freshness):
            await _asyncio.sleep(10)
            raise AssertionError("should never get here")

    # Patch the internal timeout from 30 -> 1 for fast test
    orig_wait_for = _asyncio.wait_for
    async def short_wait_for(coro, timeout):
        return await orig_wait_for(coro, timeout=1)  # override to 1s
    monkeypatch.setattr("kc_web.search.asyncio.wait_for", short_wait_for)

    impl = build_web_search_impl(cfg, HangingClient(), budget)
    out = json.loads(await impl(query="x"))
    assert out["error"] == "timeout"
    assert out["elapsed_ms"] >= 900
