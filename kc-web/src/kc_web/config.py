from __future__ import annotations
import os
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal


Backend = Literal["ollama", "firecrawl"]
_VALID_BACKENDS = ("ollama", "firecrawl")


def _gen_session_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class WebConfig:
    backend: Backend
    ollama_api_key: str | None
    firecrawl_api_key: str | None
    session_soft_cap: int
    daily_hard_cap: int
    fetch_cap_bytes: int
    default_search_max_results: int
    default_fetch_timeout_s: int
    budget_db_path: Path
    extra_blocked_hosts: tuple[str, ...]
    session_id: str = field(default_factory=_gen_session_id)

    @classmethod
    def with_defaults(
        cls,
        *,
        backend: str = "ollama",
        ollama_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
    ) -> "WebConfig":
        return cls(
            backend=backend,  # type: ignore[arg-type]
            ollama_api_key=ollama_api_key,
            firecrawl_api_key=firecrawl_api_key,
            session_soft_cap=100,
            daily_hard_cap=1000,
            fetch_cap_bytes=32 * 1024,
            default_search_max_results=10,
            default_fetch_timeout_s=30,
            budget_db_path=Path.home() / ".kona" / "web_budget.sqlite",
            extra_blocked_hosts=(),
        )

    @classmethod
    def from_env(
        cls,
        *,
        ollama_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
        backend: str | None = None,
    ) -> "WebConfig":
        """Build WebConfig from explicit keys + optional KC_WEB_* env overrides.

        Backend resolution: explicit `backend` kwarg wins, then KC_WEB_BACKEND
        env, then default 'ollama'.

        Keys come from the supervisor's encrypted secrets store
        (~/KonaClaw/config/secrets.yaml.enc) — not env — matching the pattern
        used for newsapi_api_key, telegram_bot_token, etc.

        Raises:
            ValueError: if backend is not 'ollama' or 'firecrawl', or if the
                selected backend's key is missing/whitespace.
        """
        chosen = backend or os.environ.get("KC_WEB_BACKEND", "ollama")
        if chosen not in _VALID_BACKENDS:
            raise ValueError(
                f"invalid KC_WEB_BACKEND: {chosen!r} (expected one of {_VALID_BACKENDS})"
            )
        if chosen == "ollama":
            if not ollama_api_key or not ollama_api_key.strip():
                raise ValueError("ollama_api_key required when backend=ollama")
        else:
            if not firecrawl_api_key or not firecrawl_api_key.strip():
                raise ValueError("firecrawl_api_key required when backend=firecrawl")
        base = cls.with_defaults(
            backend=chosen,
            ollama_api_key=ollama_api_key,
            firecrawl_api_key=firecrawl_api_key,
        )
        blocked_raw = os.environ.get("KC_WEB_BLOCKED_HOSTS", "")
        blocked = tuple(h.strip() for h in blocked_raw.split(",") if h.strip())
        return replace(
            base,
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
