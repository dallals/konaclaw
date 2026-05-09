from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from kc_connectors.news_adapter import (
    Article,
    NewsClient,
    NewsResult,
    build_news_tools,
)


def fake_http(payload: dict, *, status: int = 200, raises: Exception | None = None):
    """Return a callable matching NewsClient's `http` signature: http(url) -> (status, body_bytes)."""
    calls = []

    def call(url: str, *, timeout: float):
        calls.append(url)
        if raises is not None:
            raise raises
        return status, json.dumps(payload).encode()

    call.calls = calls  # type: ignore[attr-defined]
    return call


def ok_payload(*titles: str) -> dict:
    return {
        "status": "ok",
        "articles": [
            {
                "title": t,
                "source": {"name": "BBC News"},
                "url": f"https://example.com/{t}",
                "publishedAt": "2026-05-08T10:00:00Z",
                "description": f"snippet for {t}",
            }
            for t in titles
        ],
    }


def test_search_topic_returns_articles():
    http = fake_http(ok_payload("Story A", "Story B"))
    c = NewsClient(api_key="k", http=http)
    r = c.search_topic("ai", max_results=2)
    assert r.error is None
    assert r.cached is False
    assert [a.title for a in r.articles] == ["Story A", "Story B"]
    assert r.articles[0].source == "BBC News"
    assert r.articles[0].url == "https://example.com/Story A"
    assert "q=ai" in http.calls[0]
    assert "pageSize=2" in http.calls[0]


def test_from_source_returns_articles():
    http = fake_http(ok_payload("Headline 1"))
    c = NewsClient(api_key="k", http=http)
    r = c.from_source("bbc-news", max_results=5)
    assert r.error is None
    assert [a.title for a in r.articles] == ["Headline 1"]
    assert "sources=bbc-news" in http.calls[0]


def test_max_results_capped_at_10():
    http = fake_http(ok_payload())
    c = NewsClient(api_key="k", http=http)
    c.search_topic("x", max_results=999)
    assert "pageSize=10" in http.calls[0]


def test_cache_hit_avoids_second_http_call():
    http = fake_http(ok_payload("Story A"))
    c = NewsClient(api_key="k", http=http, ttl_seconds=600)
    r1 = c.search_topic("ai", max_results=5)
    r2 = c.search_topic("AI", max_results=5)  # case + whitespace normalized
    assert len(http.calls) == 1
    assert r1.cached is False
    assert r2.cached is True
    assert [a.title for a in r2.articles] == ["Story A"]


def test_cache_expires_after_ttl(monkeypatch):
    http = fake_http(ok_payload("Story A"))
    c = NewsClient(api_key="k", http=http, ttl_seconds=10)
    fake_now = [1000.0]
    monkeypatch.setattr("kc_connectors.news_adapter.time.monotonic", lambda: fake_now[0])
    c.search_topic("ai", max_results=5)
    fake_now[0] = 1011.0  # 11s later — past TTL
    c.search_topic("ai", max_results=5)
    assert len(http.calls) == 2


def test_quota_reached_mapping():
    http = fake_http({"status": "error", "code": "rateLimited", "message": "no quota"})
    c = NewsClient(api_key="k", http=http)
    r = c.search_topic("ai")
    assert r.error == "quota_reached"
    assert r.articles == []


def test_unknown_source_mapping():
    http = fake_http({"status": "error", "code": "sourcesDoesntExist", "message": "x"})
    c = NewsClient(api_key="k", http=http)
    r = c.from_source("nope")
    assert r.error == "unknown_source"


def test_upstream_error_on_other_codes():
    http = fake_http({"status": "error", "code": "apiKeyInvalid", "message": "bad key"})
    c = NewsClient(api_key="api-key-xyz", http=http)
    r = c.search_topic("ai")
    assert r.error == "upstream_error"
    assert "bad key" in (r.message or "")


def test_upstream_error_on_network_exception():
    http = fake_http({}, raises=ConnectionError("boom"))
    c = NewsClient(api_key="k", http=http)
    r = c.search_topic("ai")
    assert r.error == "upstream_error"
    assert "boom" in (r.message or "")


def test_empty_articles_no_error():
    http = fake_http(ok_payload())
    c = NewsClient(api_key="k", http=http)
    r = c.search_topic("ai")
    assert r.error is None
    assert r.articles == []


def test_tool_search_topic_formats_results():
    http = fake_http(ok_payload("Story A", "Story B"))
    c = NewsClient(api_key="k", http=http)
    tools = build_news_tools(c)
    out = tools["news.search_topic"].impl(query="ai", max_results=2)
    assert "1. Story A — BBC News" in out
    assert "2. Story B — BBC News" in out
    assert "https://example.com/Story A" in out


def test_tool_from_source_unknown_includes_examples():
    http = fake_http({"status": "error", "code": "sourcesDoesntExist", "message": "x"})
    c = NewsClient(api_key="k", http=http)
    tools = build_news_tools(c)
    out = tools["news.from_source"].impl(source="nope")
    assert "unknown source: 'nope'" in out
    assert "bbc-news" in out


def test_tool_quota_reached_message():
    http = fake_http({"status": "error", "code": "rateLimited", "message": "no"})
    c = NewsClient(api_key="k", http=http)
    tools = build_news_tools(c)
    out = tools["news.search_topic"].impl(query="ai")
    assert out == "(News API quota reached)"


def test_tool_no_results_message():
    http = fake_http(ok_payload())
    c = NewsClient(api_key="k", http=http)
    tools = build_news_tools(c)
    out = tools["news.search_topic"].impl(query="ai")
    assert out == "(no results)"


def test_cache_evicts_when_over_max(monkeypatch):
    """Cache size stays bounded under a flood of distinct queries."""
    import kc_connectors.news_adapter as na
    monkeypatch.setattr(na, "_CACHE_MAX", 3)
    http = fake_http(ok_payload("X"))
    c = NewsClient(api_key="k", http=http, ttl_seconds=600)
    for q in ["a", "b", "c", "d", "e"]:
        c.search_topic(q)
    assert len(c._cache) <= 3


def test_cache_sweeps_expired_entries_on_write(monkeypatch):
    """Expired entries get cleared when a fresh fetch runs."""
    http = fake_http(ok_payload("X"))
    c = NewsClient(api_key="k", http=http, ttl_seconds=10)
    fake_now = [1000.0]
    monkeypatch.setattr("kc_connectors.news_adapter.time.monotonic", lambda: fake_now[0])
    c.search_topic("old")
    assert len(c._cache) == 1
    fake_now[0] = 2000.0  # well past TTL
    c.search_topic("new")
    # The "old" entry should have been swept; only "new" remains.
    assert len(c._cache) == 1
    assert ("topic", "new", 5) in c._cache


def test_message_scrubs_api_key():
    """api_key never appears verbatim in result.message."""
    secret = "SECRET-API-KEY-12345"
    http = fake_http({
        "status": "error",
        "code": "apiKeyInvalid",
        "message": f"Your key '{secret}' is not valid",
    })
    c = NewsClient(api_key=secret, http=http)
    r = c.search_topic("ai")
    assert r.error == "upstream_error"
    assert secret not in (r.message or "")
    assert "***" in (r.message or "")
