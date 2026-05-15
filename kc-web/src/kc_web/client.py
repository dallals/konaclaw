from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import json as _json_mod  # alias to avoid shadowing in user code

import httpx


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


class WebClientError(Exception):
    """Generic error raised by any WebClient implementation.

    `status` is an HTTP status code when the backend returned a non-2xx
    response, or 0 for network/JSON/other errors that have no HTTP status.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"web backend error status={status}: {message}")
        self.status = status
        self.message = message


# Kept as an alias for one cycle so external imports / isinstance checks
# don't break. New code should use WebClientError directly.
FirecrawlError = WebClientError


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


_OLLAMA_DEFAULT_BASE_URL = "https://ollama.com/api"
_OLLAMA_DEFAULT_TIMEOUT = 60.0


class OllamaClient:
    """WebClient implementation backed by Ollama's hosted web search API.

    Quirks vs the WebClient Protocol:
      - `max_results` is silently clamped to [1, 10] (Ollama hard cap).
      - `freshness` is silently ignored (no Ollama equivalent).
      - `include_links` is silently ignored (Ollama always returns links;
        ScrapeResult has no `links` field).
      - `status_code` in ScrapeResult is always 0 (Ollama does not report it).
      - `final_url` echoes the input url (Ollama does not report redirects).
    """

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = _OLLAMA_DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_OLLAMA_DEFAULT_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def search(
        self,
        query: str,
        max_results: int,
        freshness: str,
    ) -> list[SearchResult]:
        clamped = max(1, min(10, int(max_results)))
        body: dict[str, Any] = {"query": query, "max_results": clamped}
        http = await self._client()
        try:
            resp = await http.post(
                f"{self._base_url}/web_search",
                json=body,
                headers=self._headers(),
            )
        except httpx.TimeoutException:
            # Convert to asyncio.TimeoutError so the search.py wait_for wrapper
            # catches it via its existing `except asyncio.TimeoutError` branch.
            raise asyncio.TimeoutError() from None
        except httpx.HTTPError as e:
            raise WebClientError(0, str(e)) from e
        if resp.status_code >= 400:
            raise WebClientError(resp.status_code, resp.text[:512])
        try:
            data = resp.json()
        except (_json_mod.JSONDecodeError, ValueError) as e:
            raise WebClientError(0, f"invalid_json: {e}") from e
        items = data.get("results") or []
        return [
            SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content", "")),
            )
            for r in items
        ]

    async def scrape(
        self,
        url: str,
        timeout_seconds: int,
        include_links: bool,
    ) -> ScrapeResult:
        body: dict[str, Any] = {"url": url}  # include_links intentionally ignored
        http = await self._client()
        try:
            resp = await http.post(
                f"{self._base_url}/web_fetch",
                json=body,
                headers=self._headers(),
                timeout=float(timeout_seconds),
            )
        except httpx.TimeoutException:
            raise asyncio.TimeoutError() from None
        except httpx.HTTPError as e:
            raise WebClientError(0, str(e)) from e
        if resp.status_code >= 400:
            raise WebClientError(resp.status_code, resp.text[:512])
        try:
            data = resp.json()
        except (_json_mod.JSONDecodeError, ValueError) as e:
            raise WebClientError(0, f"invalid_json: {e}") from e
        return ScrapeResult(
            url=url,
            final_url=url,
            status_code=0,
            title=str(data.get("title", "")),
            markdown=str(data.get("content", "")),
        )
