from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from kc_web.budget import BudgetStore
from kc_web.client import FirecrawlError, WebClient
from kc_web.config import WebConfig
from kc_web.truncate import head_tail
from kc_web.url_guard import is_public_url


def build_web_fetch_impl(
    cfg: WebConfig,
    client: WebClient,
    budget: BudgetStore,
) -> Callable[..., Awaitable[str]]:
    """Returns the async impl for the web_fetch tool."""

    async def impl(
        url: str = "",
        timeout_seconds: int | None = None,
        include_links: bool = False,
    ) -> str:
        # 1. URL guard.
        if not isinstance(url, str) or not url.strip():
            return _json({"error": "url_invalid", "url": url})
        allowed, reason = is_public_url(url, cfg.extra_blocked_hosts)
        if not allowed:
            err_code = "url_not_http" if reason == "non_http_scheme" else "url_blocked"
            payload: dict[str, Any] = {"error": err_code, "url": url}
            if err_code == "url_blocked":
                payload["reason"] = reason
            return _json(payload)

        # 2. Clamp timeout.
        if timeout_seconds is None:
            timeout_seconds = cfg.default_fetch_timeout_s
        timeout_seconds = max(1, min(120, int(timeout_seconds)))

        # 3. Budget gate.
        ok, err = await budget.check_and_record("web_fetch")
        if not ok:
            return _json(err or {"error": "budget_unknown"})

        # 4. Call Firecrawl.
        t0 = time.monotonic()
        try:
            result = await client.scrape(url, timeout_seconds, bool(include_links))
        except FirecrawlError as e:
            return _json({
                "error": "firecrawl_error",
                "status": e.status,
                "message": str(e),
            })
        except asyncio.TimeoutError:
            return _json({
                "error": "timeout",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            })

        # 5. Truncate + shape return.
        content, truncated = head_tail(result.markdown, cfg.fetch_cap_bytes)
        return _json({
            "url": result.url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "title": result.title,
            "content": content,
            "content_truncated": truncated,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })

    return impl


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
