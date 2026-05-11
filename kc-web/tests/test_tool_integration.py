import json
from pathlib import Path

import pytest

from kc_core.tools import Tool, ToolRegistry
from kc_web.client import SearchResult, ScrapeResult
from kc_web.config import WebConfig
from kc_web.tools import build_web_tools


class FakeClient:
    async def search(self, query, max_results, freshness):
        return [SearchResult("T", "https://example.com", "S")]

    async def scrape(self, url, timeout_seconds, include_links):
        return ScrapeResult(
            url=url, final_url=url, status_code=200,
            title="t", markdown="m",
        )


@pytest.fixture
def cfg(tmp_path: Path) -> WebConfig:
    return WebConfig(
        firecrawl_api_key="k",
        session_soft_cap=10, daily_hard_cap=100,
        fetch_cap_bytes=1024, default_search_max_results=5,
        default_fetch_timeout_s=30,
        budget_db_path=tmp_path / "b.sqlite",
        extra_blocked_hosts=(), session_id="s",
    )


def test_returns_two_tools(cfg):
    tools = build_web_tools(cfg, client=FakeClient())
    names = [t.name for t in tools]
    assert sorted(names) == ["web_fetch", "web_search"]


def test_tools_register_in_registry(cfg):
    tools = build_web_tools(cfg, client=FakeClient())
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    assert registry.get("web_search").name == "web_search"
    assert registry.get("web_fetch").name == "web_fetch"


def test_tool_parameters_have_required_field(cfg):
    tools = {t.name: t for t in build_web_tools(cfg, client=FakeClient())}
    assert "query" in tools["web_search"].parameters["required"]
    assert "url" in tools["web_fetch"].parameters["required"]


@pytest.mark.asyncio
async def test_invoking_tools_via_registry(cfg):
    tools = build_web_tools(cfg, client=FakeClient())
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    out = await registry.invoke("web_search", {"query": "hi"})
    parsed = json.loads(out)
    assert parsed["query"] == "hi"
    assert parsed["result_count"] == 1


def test_factory_idempotent_no_shared_state(cfg):
    """Calling build_web_tools twice produces independent tool sets."""
    a = build_web_tools(cfg, client=FakeClient())
    b = build_web_tools(cfg, client=FakeClient())
    # Different Tool instances:
    assert a[0] is not b[0]
