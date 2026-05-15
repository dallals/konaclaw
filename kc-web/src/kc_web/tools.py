from __future__ import annotations
from typing import Any

from kc_core.tools import Tool

from kc_web.budget import BudgetStore
from kc_web.client import FirecrawlClient, OllamaClient, WebClient
from kc_web.config import WebConfig
from kc_web.fetch import build_web_fetch_impl
from kc_web.search import build_web_search_impl


_SEARCH_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search query. Use Google operators like `site:docs.python.org` "
                "to scope to a domain. REQUIRED, non-empty."
            ),
        },
        "max_results": {
            "type": "integer",
            "description": "Optional. Default 10, clamped to [1, 25].",
        },
        "freshness": {
            "type": "string",
            "enum": ["any", "day", "week", "month", "year"],
            "description": (
                "Optional. Restrict to results from the last day/week/month/year. "
                "Default `any`. May be ignored by some backends."
            ),
        },
    },
    "required": ["query"],
}


_SEARCH_DESCRIPTION = (
    "Search the web. Returns a list of {title, url, snippet} results. "
    "Read-only, no approval prompt. Counts against the per-session and per-day "
    "budget caps. Use `site:` operator in the query to scope to a domain."
)


_FETCH_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "Absolute http(s) URL to fetch. Local/private hosts (localhost, "
                "127.0.0.1, RFC1918, *.local, *.internal, GCP metadata) are rejected. "
                "REQUIRED."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Optional. Default 30, clamped to [1, 120].",
        },
        "include_links": {
            "type": "boolean",
            "description": (
                "Optional. If true, ask the backend to extract links alongside "
                "markdown. Default false. May be ignored by some backends."
            ),
        },
    },
    "required": ["url"],
}


_FETCH_DESCRIPTION = (
    "Fetch a public web page and return its content as markdown. "
    "Read-only, no approval prompt. Long pages are head+tail truncated to "
    "fit a configured cap. Counts against the per-session and per-day budget "
    "caps. Will not fetch local or private hosts."
)


def build_web_tools(
    cfg: WebConfig,
    *,
    client: WebClient | None = None,
) -> list[Tool]:
    """Build web_search and web_fetch tools.

    `client` is injectable for tests; in production, omit it and the right
    backend client is constructed based on cfg.backend.
    """
    if client is None:
        if cfg.backend == "ollama":
            client = OllamaClient(api_key=cfg.ollama_api_key or "")
        else:
            client = FirecrawlClient(api_key=cfg.firecrawl_api_key or "")

    budget = BudgetStore(
        db_path=cfg.budget_db_path,
        session_id=cfg.session_id,
        session_soft_cap=cfg.session_soft_cap,
        daily_hard_cap=cfg.daily_hard_cap,
    )

    search_impl = build_web_search_impl(cfg, client, budget)
    fetch_impl = build_web_fetch_impl(cfg, client, budget)

    return [
        Tool(
            name="web_search",
            description=_SEARCH_DESCRIPTION,
            parameters=_SEARCH_PARAMS,
            impl=search_impl,
        ),
        Tool(
            name="web_fetch",
            description=_FETCH_DESCRIPTION,
            parameters=_FETCH_PARAMS,
            impl=fetch_impl,
        ),
    ]
