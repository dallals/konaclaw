from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kc_connectors.news_adapter import Article, NewsResult


@pytest.fixture
def app_with_news(deps):
    """Use the standard `deps` fixture from conftest, but attach a mocked
    NewsClient before constructing the app."""
    from kc_supervisor.service import create_app
    deps.news_client = MagicMock()
    return create_app(deps), deps


def test_news_returns_503_when_not_configured(app):
    # `app` fixture from conftest has news_client=None by default
    with TestClient(app) as client:
        r = client.get("/api/news?mode=topic&q=ai")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "not_configured"


def test_news_topic_happy_path(app_with_news):
    app, deps = app_with_news
    deps.news_client.search_topic.return_value = NewsResult(
        articles=[Article(
            title="Story A", source="BBC News",
            url="https://example.com/a",
            published_at="2026-05-08T10:00:00Z",
            snippet="snip",
        )],
        cached=False,
    )
    with TestClient(app) as client:
        r = client.get("/api/news?mode=topic&q=ai&max_results=5")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert len(body["articles"]) == 1
    a = body["articles"][0]
    assert a["title"] == "Story A"
    assert a["source"] == "BBC News"
    assert a["url"] == "https://example.com/a"
    assert a["published_at"] == "2026-05-08T10:00:00Z"
    assert a["snippet"] == "snip"
    deps.news_client.search_topic.assert_called_once_with(query="ai", max_results=5)


def test_news_source_happy_path(app_with_news):
    app, deps = app_with_news
    deps.news_client.from_source.return_value = NewsResult(articles=[], cached=True)
    with TestClient(app) as client:
        r = client.get("/api/news?mode=source&source=bbc-news")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True
    assert body["articles"] == []
    deps.news_client.from_source.assert_called_once_with(source="bbc-news", max_results=5)


def test_news_quota_reached_returns_429(app_with_news):
    app, deps = app_with_news
    deps.news_client.search_topic.return_value = NewsResult(
        error="quota_reached", message="no quota",
    )
    with TestClient(app) as client:
        r = client.get("/api/news?mode=topic&q=ai")
    assert r.status_code == 429
    assert r.json()["error"] == "quota_reached"


def test_news_unknown_source_returns_400(app_with_news):
    app, deps = app_with_news
    deps.news_client.from_source.return_value = NewsResult(
        error="unknown_source", message="x",
    )
    with TestClient(app) as client:
        r = client.get("/api/news?mode=source&source=nope")
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_source"


def test_news_upstream_error_returns_502(app_with_news):
    app, deps = app_with_news
    deps.news_client.search_topic.return_value = NewsResult(
        error="upstream_error", message="boom",
    )
    with TestClient(app) as client:
        r = client.get("/api/news?mode=topic&q=ai")
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_error"


def test_news_missing_q_for_topic_mode(app_with_news):
    app, _ = app_with_news
    with TestClient(app) as client:
        r = client.get("/api/news?mode=topic")
    assert r.status_code == 400
    assert r.json()["error"] == "missing_param"


def test_news_missing_source_for_source_mode(app_with_news):
    app, _ = app_with_news
    with TestClient(app) as client:
        r = client.get("/api/news?mode=source")
    assert r.status_code == 400
    assert r.json()["error"] == "missing_param"


def test_news_invalid_mode(app_with_news):
    app, _ = app_with_news
    with TestClient(app) as client:
        r = client.get("/api/news?mode=garbage&q=x")
    assert r.status_code == 422  # FastAPI rejects via Literal type
