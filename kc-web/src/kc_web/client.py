from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class ScrapeResult:
    url: str          # echoed input
    final_url: str    # after redirects
    status_code: int  # best-effort; 0 if SDK doesn't report
    title: str
    markdown: str


class WebClient(Protocol):
    """Minimal protocol the tools depend on. Implemented by FirecrawlClient
    for production and by FakeWebClient for tests."""

    async def search(
        self,
        query: str,
        max_results: int,
        freshness: str,
    ) -> list[SearchResult]: ...

    async def scrape(
        self,
        url: str,
        timeout_seconds: int,
        include_links: bool,
    ) -> ScrapeResult: ...


class FirecrawlError(Exception):
    """Wraps any error from the Firecrawl SDK with status + message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"firecrawl status={status}: {message}")
        self.status = status
        self.message = message


class FirecrawlClient:
    """Thin async wrapper around firecrawl-py v2. Delegates blocking SDK calls to
    asyncio.to_thread so they don't stall the event loop.

    v2 API differences from v1 (adapted):
    - scrape_url(url, *, formats=..., timeout=...) — keyword args directly, no params={} dict.
    - search(query, *, limit=..., tbs=...) — keyword args directly, no params={} dict.
    - scrape_url returns a ScrapeResponse (Pydantic model / FirecrawlDocument subclass)
      with .markdown, .metadata, .url as direct attributes — NOT a nested dict.
    - search returns a SearchResponse Pydantic model with .data as List[Dict[str, Any]].
    """

    def __init__(self, api_key: str) -> None:
        # Import lazily so tests / static analysis don't require firecrawl-py.
        from firecrawl import FirecrawlApp  # type: ignore[import-not-found]
        self._app = FirecrawlApp(api_key=api_key)

    async def search(
        self,
        query: str,
        max_results: int,
        freshness: str,
    ) -> list[SearchResult]:
        kwargs: dict[str, Any] = {"limit": max_results}
        if freshness != "any":
            kwargs["tbs"] = _freshness_to_tbs(freshness)
        try:
            # v2: search(query, *, limit=..., tbs=...) — kwargs, no params={} wrapper.
            raw = await asyncio.to_thread(self._app.search, query, **kwargs)
        except Exception as e:  # noqa: BLE001 — SDK exceptions are heterogeneous
            raise FirecrawlError(_extract_status(e), str(e)) from e
        # v2 returns SearchResponse Pydantic model with .data as List[Dict[str, Any]].
        # Each dict has: url, title, description, markdown (and others).
        items: list[dict[str, Any]] = []
        if hasattr(raw, "data") and isinstance(raw.data, list):
            items = raw.data
        elif isinstance(raw, dict):
            items = (raw.get("data") or [])
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", item.get("snippet", ""))),
            )
            for item in items
        ]

    async def scrape(
        self,
        url: str,
        timeout_seconds: int,
        include_links: bool,
    ) -> ScrapeResult:
        formats: list[str] = ["markdown"]
        if include_links:
            formats.append("links")
        try:
            # v2: scrape_url(url, *, formats=..., timeout=...) — kwargs, no params={} wrapper.
            # timeout is in milliseconds in the SDK.
            raw = await asyncio.to_thread(
                self._app.scrape_url,
                url,
                formats=formats,
                timeout=timeout_seconds * 1000,
            )
        except Exception as e:  # noqa: BLE001
            raise FirecrawlError(_extract_status(e), str(e)) from e
        # v2 returns ScrapeResponse (a FirecrawlDocument subclass with Pydantic model).
        # Fields are direct attributes: .markdown, .url, .metadata.
        # .metadata is Optional[Any] — may be a dict with sourceURL, statusCode, title.
        meta: dict[str, Any] = {}
        raw_meta = getattr(raw, "metadata", None)
        if isinstance(raw_meta, dict):
            meta = raw_meta
        # .url on the document is the final (post-redirect) URL; fall back to input url.
        doc_url = getattr(raw, "url", None) or url
        return ScrapeResult(
            url=url,
            final_url=str(meta.get("sourceURL", meta.get("url", doc_url))),
            status_code=int(meta.get("statusCode", 0) or 0),
            title=str(meta.get("title", getattr(raw, "title", "") or "")),
            markdown=str(getattr(raw, "markdown", "") or ""),
        )


_FRESHNESS_TBS = {
    "day":   "qdr:d",
    "week":  "qdr:w",
    "month": "qdr:m",
    "year":  "qdr:y",
}


def _freshness_to_tbs(freshness: str) -> str:
    return _FRESHNESS_TBS[freshness]


def _extract_status(e: Exception) -> int:
    """Best-effort extraction of HTTP status from common exception shapes."""
    for attr in ("status_code", "status", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    return 0
