import json

import httpx
import pytest

from kc_web.client import OllamaClient, SearchResult


def _make_client(handler):
    """Build an OllamaClient whose AsyncClient uses an httpx MockTransport.

    `handler` is a sync callable `(httpx.Request) -> httpx.Response`.
    """
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, timeout=10.0)
    return OllamaClient(api_key="sk-test", http=http, base_url="https://ollama.example/api")


@pytest.mark.asyncio
async def test_search_happy_path_maps_results():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://a.example", "content": "snip1"},
                    {"title": "T2", "url": "https://b.example", "content": "snip2"},
                ]
            },
        )

    client = _make_client(handler)
    results = await client.search("hello", max_results=5, freshness="any")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://ollama.example/api/web_search"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"] == {"query": "hello", "max_results": 5}
    assert results == [
        SearchResult(title="T1", url="https://a.example", snippet="snip1"),
        SearchResult(title="T2", url="https://b.example", snippet="snip2"),
    ]


@pytest.mark.asyncio
async def test_search_empty_results_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    client = _make_client(handler)
    results = await client.search("nothing here", max_results=5, freshness="any")
    assert results == []


from kc_web.client import ScrapeResult


@pytest.mark.asyncio
async def test_scrape_happy_path_maps_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "title": "Example",
                "content": "# Hello\n\nWorld.",
                "links": ["https://a.example", "https://b.example"],
            },
        )

    client = _make_client(handler)
    result = await client.scrape(
        "https://example.org/page",
        timeout_seconds=15,
        include_links=False,
    )

    assert captured["url"] == "https://ollama.example/api/web_fetch"
    assert captured["body"] == {"url": "https://example.org/page"}
    assert result == ScrapeResult(
        url="https://example.org/page",
        final_url="https://example.org/page",
        status_code=0,
        title="Example",
        markdown="# Hello\n\nWorld.",
    )


@pytest.mark.asyncio
async def test_scrape_include_links_silently_ignored():
    """Ollama always returns links; we don't surface them. Passing include_links=True
    must not change the request or raise."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"title": "T", "content": "C", "links": []})

    client = _make_client(handler)
    await client.scrape("https://example.org/", timeout_seconds=10, include_links=True)
    assert "include_links" not in captured["body"]
    assert captured["body"] == {"url": "https://example.org/"}


from kc_web.client import WebClientError


@pytest.mark.asyncio
async def test_search_401_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 401
    assert "unauthorized" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_429_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 429


@pytest.mark.asyncio
async def test_search_5xx_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 503


@pytest.mark.asyncio
async def test_search_invalid_json_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 0
    assert "invalid_json" in exc_info.value.message


@pytest.mark.asyncio
async def test_scrape_4xx_raises_web_client_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.scrape("https://x.example", timeout_seconds=10, include_links=False)
    assert exc_info.value.status == 404


@pytest.mark.asyncio
async def test_search_network_error_raises_web_client_error_status_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    client = _make_client(handler)
    with pytest.raises(WebClientError) as exc_info:
        await client.search("q", max_results=5, freshness="any")
    assert exc_info.value.status == 0
    assert "dns failure" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_httpx_timeout_bubbles_as_asyncio_timeout():
    """httpx.TimeoutException must be converted to asyncio.TimeoutError so
    the search.py wait_for wrapper catches it via its existing branch."""
    import asyncio as _asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _make_client(handler)
    with pytest.raises(_asyncio.TimeoutError):
        await client.search("q", max_results=5, freshness="any")


@pytest.mark.asyncio
async def test_scrape_httpx_timeout_bubbles_as_asyncio_timeout():
    import asyncio as _asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _make_client(handler)
    with pytest.raises(_asyncio.TimeoutError):
        await client.scrape("https://x.example", timeout_seconds=5, include_links=False)
