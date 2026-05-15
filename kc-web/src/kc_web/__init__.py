"""KonaClaw web tools (web_search + web_fetch)."""

from kc_web.client import (
    FirecrawlClient,
    FirecrawlError,
    OllamaClient,
    ScrapeResult,
    SearchResult,
    WebClient,
    WebClientError,
)
from kc_web.config import WebConfig
from kc_web.tools import build_web_tools

__all__ = [
    "FirecrawlClient",
    "FirecrawlError",
    "OllamaClient",
    "ScrapeResult",
    "SearchResult",
    "WebClient",
    "WebClientError",
    "WebConfig",
    "build_web_tools",
]
