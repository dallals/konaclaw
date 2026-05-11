from __future__ import annotations
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def _gen_session_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class WebConfig:
    firecrawl_api_key: str
    session_soft_cap: int
    daily_hard_cap: int
    fetch_cap_bytes: int
    default_search_max_results: int
    default_fetch_timeout_s: int
    budget_db_path: Path
    extra_blocked_hosts: tuple[str, ...]
    session_id: str = field(default_factory=_gen_session_id)

    @classmethod
    def with_defaults(cls, *, api_key: str) -> "WebConfig":
        return cls(
            firecrawl_api_key=api_key,
            session_soft_cap=50,
            daily_hard_cap=500,
            fetch_cap_bytes=32 * 1024,
            default_search_max_results=10,
            default_fetch_timeout_s=30,
            budget_db_path=Path.home() / ".kona" / "web_budget.sqlite",
            extra_blocked_hosts=(),
        )

    @classmethod
    def from_env(cls) -> "WebConfig":
        api_key = os.environ.get("KC_FIRECRAWL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "KC_FIRECRAWL_API_KEY is required when KC_WEB_ENABLED=true"
            )
        base = cls.with_defaults(api_key=api_key)
        blocked_raw = os.environ.get("KC_WEB_BLOCKED_HOSTS", "")
        blocked = tuple(h.strip() for h in blocked_raw.split(",") if h.strip())
        return cls(
            firecrawl_api_key=api_key,
            session_soft_cap=int(
                os.environ.get("KC_WEB_SESSION_SOFT_CAP", base.session_soft_cap)
            ),
            daily_hard_cap=int(
                os.environ.get("KC_WEB_DAILY_HARD_CAP", base.daily_hard_cap)
            ),
            fetch_cap_bytes=int(
                os.environ.get("KC_WEB_FETCH_CAP_BYTES", base.fetch_cap_bytes)
            ),
            default_search_max_results=int(
                os.environ.get(
                    "KC_WEB_SEARCH_DEFAULT_N", base.default_search_max_results
                )
            ),
            default_fetch_timeout_s=int(
                os.environ.get(
                    "KC_WEB_FETCH_DEFAULT_TIMEOUT", base.default_fetch_timeout_s
                )
            ),
            budget_db_path=Path(
                os.environ.get("KC_WEB_BUDGET_DB", str(base.budget_db_path))
            ).expanduser(),
            extra_blocked_hosts=blocked,
        )
