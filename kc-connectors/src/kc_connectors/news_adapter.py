from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from kc_core.tools import Tool


_API_BASE = "https://newsapi.org/v2"
_CACHE_MAX = 256
ErrorCode = Literal["quota_reached", "unknown_source", "upstream_error"]


@dataclass
class Article:
    title: str
    source: str
    url: str
    published_at: str
    snippet: str


@dataclass
class NewsResult:
    articles: list[Article] = field(default_factory=list)
    cached: bool = False
    error: Optional[ErrorCode] = None
    message: Optional[str] = None


def _default_http(url: str, *, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "kc-connectors/news"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


class NewsClient:
    """NewsAPI.org client with TTL cache. Stdlib-only — no `requests` dependency."""

    def __init__(
        self,
        api_key: str,
        *,
        ttl_seconds: int = 600,
        http: Optional[Callable[..., tuple[int, bytes]]] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.ttl_seconds = ttl_seconds
        self._http = http or _default_http
        self._timeout = timeout
        self._cache: dict[tuple[str, str, int], tuple[float, NewsResult]] = {}

    def search_topic(self, query: str, max_results: int = 5) -> NewsResult:
        return self._fetch("topic", query, max_results)

    def from_source(self, source: str, max_results: int = 5) -> NewsResult:
        return self._fetch("source", source, max_results)

    def _fetch(self, mode: str, value: str, max_results: int) -> NewsResult:
        n = max(1, min(int(max_results), 10))
        key = (mode, value.strip().lower(), n)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and (now - hit[0]) < self.ttl_seconds:
            cached = NewsResult(
                articles=list(hit[1].articles),
                cached=True,
                error=hit[1].error,
                message=hit[1].message,
            )
            return cached

        if mode == "topic":
            qs = {"q": value, "sortBy": "publishedAt", "pageSize": n,
                  "language": "en", "apiKey": self.api_key}
            url = f"{_API_BASE}/everything?" + urllib.parse.urlencode(qs)
        else:
            qs = {"sources": value, "pageSize": n, "apiKey": self.api_key}
            url = f"{_API_BASE}/top-headlines?" + urllib.parse.urlencode(qs)

        try:
            status, body = self._http(url, timeout=self._timeout)
        except Exception as e:
            # Broad except: callers can inject arbitrary `http` callables (incl. tests),
            # so we can't enumerate exception types here.
            return NewsResult(error="upstream_error", message=self._scrub(str(e)))

        try:
            payload = json.loads(body.decode())
        except (ValueError, UnicodeDecodeError) as e:
            return NewsResult(error="upstream_error", message=self._scrub(f"bad JSON: {e}"))

        if status == 429 or payload.get("code") in {"rateLimited", "maximumResultsReached"}:
            result = NewsResult(error="quota_reached", message=self._scrub(payload.get("message")))
        elif payload.get("code") == "sourcesDoesntExist":
            result = NewsResult(error="unknown_source", message=self._scrub(payload.get("message")))
        elif payload.get("status") == "error" or status >= 400:
            result = NewsResult(
                error="upstream_error",
                message=self._scrub(payload.get("message", f"HTTP {status}")),
            )
        else:
            result = NewsResult(
                articles=[
                    Article(
                        title=a.get("title", ""),
                        source=(a.get("source") or {}).get("name", "") or "",
                        url=a.get("url", ""),
                        published_at=a.get("publishedAt", ""),
                        snippet=a.get("description", "") or "",
                    )
                    for a in payload.get("articles", [])
                ],
            )

        # Evict expired entries to keep memory bounded.
        expired = [k for k, (ts, _) in self._cache.items() if now - ts >= self.ttl_seconds]
        for k in expired:
            self._cache.pop(k, None)
        # Hard cap — drop oldest if we'd exceed it.
        if len(self._cache) >= _CACHE_MAX:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[key] = (now, result)
        return result

    def _scrub(self, msg: Optional[str]) -> Optional[str]:
        if not msg:
            return msg
        cleaned = msg.replace(self.api_key, "***") if self.api_key else msg
        if len(cleaned) > 200:
            cleaned = cleaned[:197] + "..."
        return cleaned


def _format(result: NewsResult, *, source_hint: Optional[str] = None) -> str:
    if result.error == "quota_reached":
        return "(News API quota reached)"
    if result.error == "unknown_source":
        return (
            f"(unknown source: '{source_hint}'. "
            "Examples: bbc-news, the-verge, reuters, associated-press)"
        )
    if result.error == "upstream_error":
        return f"(news error: {result.message or 'unknown'})"
    if not result.articles:
        return "(no results)"
    lines = []
    for i, a in enumerate(result.articles, start=1):
        lines.append(f"{i}. {a.title} — {a.source} ({a.published_at})\n   {a.url}")
    return "\n".join(lines)


def build_news_tools(client: NewsClient) -> dict[str, Tool]:
    def search_topic(query: str, max_results: int = 5) -> str:
        r = client.search_topic(query=query, max_results=max_results)
        return _format(r)

    def from_source(source: str, max_results: int = 5) -> str:
        r = client.from_source(source=source, max_results=max_results)
        return _format(r, source_hint=source)

    return {
        "news.search_topic": Tool(
            name="news.search_topic",
            description="Search recent news articles by topic / free-text query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            impl=search_topic,
        ),
        "news.from_source": Tool(
            name="news.from_source",
            description=(
                "Fetch top headlines from a specific publication by NewsAPI source slug "
                "(e.g. 'bbc-news', 'the-verge', 'reuters')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["source"],
            },
            impl=from_source,
        ),
    }
