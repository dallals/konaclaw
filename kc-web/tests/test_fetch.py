import json
from pathlib import Path

import pytest

from kc_web.budget import BudgetStore
from kc_web.client import FirecrawlError, ScrapeResult
from kc_web.config import WebConfig
from kc_web.fetch import build_web_fetch_impl


class FakeClient:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, int, bool]] = []

    async def scrape(self, url, timeout_seconds, include_links):
        self.calls.append((url, timeout_seconds, include_links))
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def cfg(tmp_path: Path) -> WebConfig:
    return WebConfig(
        firecrawl_api_key="k",
        session_soft_cap=10,
        daily_hard_cap=100,
        fetch_cap_bytes=200,  # tiny so truncation is easy to trigger
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
async def test_happy_path_short_content(cfg, budget):
    client = FakeClient(result=ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        title="Example Domain",
        markdown="# Example\n\nHello.",
    ))
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="https://example.com"))
    assert out["url"] == "https://example.com"
    assert out["final_url"] == "https://example.com"
    assert out["status_code"] == 200
    assert out["title"] == "Example Domain"
    assert out["content"] == "# Example\n\nHello."
    assert out["content_truncated"] is False
    assert "duration_ms" in out
    assert client.calls == [("https://example.com", 30, False)]


@pytest.mark.asyncio
async def test_truncation(cfg, budget):
    long_md = "A" * 1000
    client = FakeClient(result=ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        title="t",
        markdown=long_md,
    ))
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="https://example.com"))
    assert out["content_truncated"] is True
    assert "[TRUNCATED" in out["content"]


@pytest.mark.asyncio
async def test_url_blocked_localhost(cfg, budget):
    client = FakeClient()
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="http://localhost:3000"))
    assert out == {
        "error": "url_blocked",
        "url": "http://localhost:3000",
        "reason": "local_hostname",
    }
    assert client.calls == []


@pytest.mark.asyncio
async def test_url_not_http(cfg, budget):
    client = FakeClient()
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="file:///etc/passwd"))
    assert out == {"error": "url_not_http", "url": "file:///etc/passwd"}
    assert client.calls == []


@pytest.mark.asyncio
async def test_url_invalid(cfg, budget):
    client = FakeClient()
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="not a url"))
    assert out["error"] in ("url_invalid", "url_not_http")
    assert client.calls == []


@pytest.mark.asyncio
async def test_redirect_final_url_differs(cfg, budget):
    client = FakeClient(result=ScrapeResult(
        url="https://example.com",
        final_url="https://www.example.com/landing",
        status_code=200,
        title="t",
        markdown="hi",
    ))
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="https://example.com"))
    assert out["url"] == "https://example.com"
    assert out["final_url"] == "https://www.example.com/landing"


@pytest.mark.asyncio
async def test_timeout_clamping(cfg, budget):
    client = FakeClient(result=ScrapeResult(
        url="https://example.com", final_url="https://example.com",
        status_code=200, title="t", markdown="",
    ))
    impl = build_web_fetch_impl(cfg, client, budget)
    await impl(url="https://example.com", timeout_seconds=999)
    assert client.calls[0][1] == 120  # clamped to max
    await impl(url="https://example.com", timeout_seconds=0)
    assert client.calls[1][1] == 1


@pytest.mark.asyncio
async def test_firecrawl_error(cfg, budget):
    client = FakeClient(exc=FirecrawlError(502, "bad gateway"))
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="https://example.com"))
    assert out["error"] == "firecrawl_error"
    assert out["status"] == 502


@pytest.mark.asyncio
async def test_extra_blocked_host(tmp_path):
    cfg = WebConfig(
        firecrawl_api_key="k",
        session_soft_cap=10, daily_hard_cap=100,
        fetch_cap_bytes=200, default_search_max_results=5,
        default_fetch_timeout_s=30,
        budget_db_path=tmp_path / "b.sqlite",
        extra_blocked_hosts=("evil.com",),
        session_id="s",
    )
    budget = BudgetStore(
        db_path=cfg.budget_db_path, session_id="s",
        session_soft_cap=10, daily_hard_cap=100,
    )
    client = FakeClient()
    impl = build_web_fetch_impl(cfg, client, budget)
    out = json.loads(await impl(url="https://evil.com/page"))
    assert out == {
        "error": "url_blocked",
        "url": "https://evil.com/page",
        "reason": "extra_blocked",
    }


@pytest.mark.asyncio
async def test_budget_blocks_call(cfg, tmp_path):
    budget = BudgetStore(
        db_path=tmp_path / "b.sqlite", session_id="s",
        session_soft_cap=1, daily_hard_cap=10,
    )
    client = FakeClient(result=ScrapeResult(
        url="https://example.com", final_url="https://example.com",
        status_code=200, title="t", markdown="x",
    ))
    impl = build_web_fetch_impl(cfg, client, budget)
    await impl(url="https://example.com")
    out = json.loads(await impl(url="https://example.com"))
    assert out == {"error": "session_cap_exceeded", "limit": 1}
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_timeout(cfg, budget):
    """If the client coroutine takes longer than timeout_seconds, return
    timeout error shape with elapsed_ms."""
    import asyncio as _asyncio

    class HangingClient:
        async def scrape(self, url, timeout_seconds, include_links):
            await _asyncio.sleep(10)  # longer than any test timeout
            raise AssertionError("should never get here")

    impl = build_web_fetch_impl(cfg, HangingClient(), budget)
    out = json.loads(await impl(url="https://example.com", timeout_seconds=1))
    assert out["error"] == "timeout"
    assert out["elapsed_ms"] >= 900  # ~1000ms within tolerance
    assert out["elapsed_ms"] < 5000  # not the full 10s
