from __future__ import annotations
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query


_DEFAULT_TIMEOUT_S = 5
_DEFAULT_CACHE_S = 60


def build_portfolio_router(
    *, workspace_dir: Path,
    cache_seconds: int | None = None,
    subprocess_timeout: int = _DEFAULT_TIMEOUT_S,
) -> APIRouter:
    """Builds a router exposing GET /portfolio/snapshot.

    Runs `python3 portfolio.py --silent` in `workspace_dir`. Caches the
    last-good result for `cache_seconds` seconds (default from env
    KC_PORTFOLIO_CACHE_S, fallback 60). `?refresh=true` bypasses cache.
    """
    if cache_seconds is None:
        cache_seconds = int(os.environ.get("KC_PORTFOLIO_CACHE_S", str(_DEFAULT_CACHE_S)))

    router = APIRouter(prefix="/portfolio", tags=["portfolio"])
    state: dict[str, Any] = {"payload": None, "cached_at_ts": 0.0}

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _fetch_subprocess() -> dict[str, Any]:
        proc = subprocess.run(
            ["python3", "portfolio.py", "--silent"],
            cwd=str(workspace_dir), capture_output=True, text=True,
            timeout=subprocess_timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"portfolio.py exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        return json.loads(last_line)

    @router.get("/snapshot")
    def snapshot(refresh: bool = Query(False)):
        now = time.time()
        if (
            not refresh
            and state["payload"] is not None
            and (now - state["cached_at_ts"]) < cache_seconds
        ):
            return {
                "cached_at": datetime.fromtimestamp(state["cached_at_ts"], tz=timezone.utc).isoformat(timespec="seconds"),
                "payload": state["payload"],
                "stale": False,
            }
        try:
            payload = _fetch_subprocess()
            state["payload"] = payload
            state["cached_at_ts"] = now
            return {"cached_at": _now_iso(), "payload": payload, "stale": False}
        except subprocess.TimeoutExpired:
            return {
                "cached_at": _now_iso(),
                "payload": None,
                "stale": True,
                "error": f"timeout after {subprocess_timeout}s",
                "last_good": state["payload"],
            }
        except (RuntimeError, json.JSONDecodeError) as e:
            return {
                "cached_at": _now_iso(),
                "payload": None,
                "stale": True,
                "error": str(e)[:300],
                "last_good": state["payload"],
            }

    return router
