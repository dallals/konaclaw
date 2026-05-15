from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from kc_web.budget import BudgetStore
from kc_web.client import WebClientError, WebClient
from kc_web.config import WebConfig


_VALID_FRESHNESS = frozenset({"any", "day", "week", "month", "year"})


def build_web_search_impl(
    cfg: WebConfig,
    client: WebClient,
    budget: BudgetStore,
) -> Callable[..., Awaitable[str]]:
    """Returns the async impl for the web_search tool."""

    async def impl(
        query: str = "",
        max_results: int | None = None,
        freshness: str = "any",
    ) -> str:
        # 1. Validate.
        if not isinstance(query, str) or not query.strip():
            return _json({"error": "missing_query"})
        if freshness not in _VALID_FRESHNESS:
            return _json({"error": "invalid_freshness", "value": freshness})

        # 2. Clamp.
        if max_results is None:
            max_results = cfg.default_search_max_results
        max_results = max(1, min(25, int(max_results)))

        # 3. Budget gate.
        ok, err = await budget.check_and_record("web_search")
        if not ok:
            return _json(err or {"error": "budget_unknown"})

        # 4. Call Firecrawl.
        t0 = time.monotonic()
        try:
            results = await asyncio.wait_for(
                client.search(query, max_results, freshness),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return _json({
                "error": "timeout",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            })
        except WebClientError as e:
            return _json({
                "error": "backend_error",
                "status": e.status,
                "message": str(e),
            })

        # 5. Shape return.
        return _json({
            "query": query,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ],
            "result_count": len(results),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })

    return impl


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
