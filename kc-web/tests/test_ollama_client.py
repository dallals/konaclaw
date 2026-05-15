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
