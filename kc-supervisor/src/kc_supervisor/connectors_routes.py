from __future__ import annotations
import logging
import platform
import sys
import threading
import time
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


CONNECTOR_NAMES = ("telegram", "imessage", "gmail", "calendar", "zapier")


class TelegramPatch(BaseModel):
    bot_token: str | None = None
    allowlist: list[str] | None = None


class IMessagePatch(BaseModel):
    allowlist: list[str] | None = None


class ZapierPatch(BaseModel):
    api_key: str | None = None


_PATCH_KEYS: dict[str, dict[str, str]] = {
    "telegram": {"bot_token": "telegram_bot_token", "allowlist": "telegram_allowlist"},
    "imessage": {"allowlist": "imessage_allowlist"},
    "zapier":   {"api_key": "zapier_api_key"},
}


def _restart_connector(name: str, deps: Any) -> None:
    """Best-effort hot-restart for telegram/imessage so PATCH takes effect
    without a supervisor reboot. Errors are logged, not raised — secret was
    saved either way. Wire-up of these hooks lives in main.py (Task 7)."""
    hook = getattr(deps, f"restart_{name}", None)
    if hook is None:
        return
    try:
        hook()
    except Exception as exc:
        # The connector restart hook should log internally; we don't
        # surface failure to the dashboard because the secret is saved.
        logging.getLogger(__name__).warning(
            "restart_%s hook failed: %s", name, exc, exc_info=True,
        )


def _token_hint(value: str | None) -> str | None:
    if not value or len(value) < 4:
        return None
    return "..." + value[-4:]


def _connector_summary(name: str, secrets: dict[str, Any], deps: Any) -> dict[str, Any]:
    if name == "telegram":
        token = secrets.get("telegram_bot_token")
        allowlist = secrets.get("telegram_allowlist") or []
        return {
            "name": name,
            "status": "connected" if token else "not_configured",
            "has_token": bool(token),
            "allowlist_count": len(allowlist),
            "summary": f"{len(allowlist)} chat(s) allowlisted" if token else "no token configured",
        }
    if name == "imessage":
        if platform.system() != "Darwin":
            return {"name": name, "status": "unavailable", "has_token": False,
                    "allowlist_count": 0, "summary": "macOS only"}
        allowlist = secrets.get("imessage_allowlist") or []
        return {"name": name,
                "status": "connected" if allowlist else "not_configured",
                "has_token": False,
                "allowlist_count": len(allowlist),
                "summary": f"{len(allowlist)} handle(s) allowlisted" if allowlist else "no handles allowlisted"}
    if name in ("gmail", "calendar"):
        token_path = (deps and getattr(deps, "google_token_path", None))
        connected = bool(token_path and token_path.exists())
        return {"name": name,
                "status": "connected" if connected else "not_configured",
                "has_token": connected,
                "allowlist_count": 0,
                "summary": "OAuth tokens cached" if connected else "not connected"}
    if name == "zapier":
        api_key = secrets.get("zapier_api_key")
        zap_count = 0
        if deps and getattr(deps, "mcp_manager", None) is not None:
            zap_count = sum(1 for n in deps.mcp_manager.names() if n == "zapier")
            # Actual zap-tool count is computed on the dedicated /zaps endpoint
        return {"name": name,
                "status": "connected" if api_key else "not_configured",
                "has_token": bool(api_key),
                "allowlist_count": 0,
                "summary": "API key set" if api_key else "no API key"}
    raise ValueError(f"unknown connector: {name}")


def _run_google_flow(deps: Any) -> None:
    """Run InstalledAppFlow.run_local_server, then update deps.google_oauth.

    Imported lazily so the supervisor still boots without google-auth-oauthlib.
    On any failure, resets state to "idle" with `last_error` populated so the
    dashboard can surface the message.
    """
    state = deps.google_oauth
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        flow = InstalledAppFlow.from_client_secrets_file(
            str(deps.google_credentials_path),
            scopes=getattr(deps, "google_scopes",
                           ["https://www.googleapis.com/auth/gmail.modify",
                            "https://www.googleapis.com/auth/calendar"]),
        )
        creds = flow.run_local_server(host="localhost", port=0, open_browser=True)
        deps.google_token_path.write_text(creds.to_json())
        state.state = "connected"
        state.since = time.time()
        state.last_error = None
    except Exception as exc:
        state.state = "idle"
        state.last_error = f"{type(exc).__name__}: {exc}"


def install(app, deps: Any) -> None:
    """Mount the connectors router. Called from service.py at app build time.

    The APIRouter is constructed inside install() (not at module scope) so each
    FastAPI app gets a fresh router. Otherwise tests that build multiple apps
    would accumulate duplicate route registrations on a shared module-level
    router, and FastAPI's first-match routing would dispatch to closures
    capturing a stale `deps` from an earlier test.
    """
    router = APIRouter(prefix="/connectors")

    @router.get("")
    def list_connectors():
        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        return {
            "connectors": [_connector_summary(n, secrets, deps) for n in CONNECTOR_NAMES],
        }

    @router.get("/{name}")
    def get_connector(name: str):
        if name not in CONNECTOR_NAMES:
            raise HTTPException(404, detail=f"unknown connector: {name}")
        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        summary = _connector_summary(name, secrets, deps)
        if name == "telegram":
            summary["token_hint"] = _token_hint(secrets.get("telegram_bot_token"))
            summary["allowlist"] = list(secrets.get("telegram_allowlist") or [])
        elif name == "imessage":
            summary["allowlist"] = list(secrets.get("imessage_allowlist") or [])
            summary["flags"] = {"platform_supported": platform.system() == "Darwin"}
        elif name == "zapier":
            summary["token_hint"] = _token_hint(secrets.get("zapier_api_key"))
        elif name in ("gmail", "calendar"):
            summary["flags"] = {"oauth": True}
        return summary

    @router.patch("/{name}")
    def patch_connector(name: str, payload: dict[str, Any]):
        if name not in _PATCH_KEYS:
            raise HTTPException(404, detail=f"unknown connector: {name}")
        if name == "telegram":
            data = TelegramPatch(**payload).model_dump(exclude_none=True)
        elif name == "imessage":
            data = IMessagePatch(**payload).model_dump(exclude_none=True)
        elif name == "zapier":
            data = ZapierPatch(**payload).model_dump(exclude_none=True)
        else:
            raise HTTPException(404)

        secrets = deps.secrets_store.load() if deps.secrets_store else {}
        for body_key, secret_key in _PATCH_KEYS[name].items():
            if body_key in data:
                secrets[secret_key] = data[body_key]
        deps.secrets_store.save(secrets)

        if name in ("telegram", "imessage"):
            _restart_connector(name, deps)
        return {"ok": True}

    @router.post("/google/connect", status_code=202)
    def google_connect():
        state = deps.google_oauth
        if state.state == "pending":
            return {"state": "pending", "since": state.since}
        state.state = "pending"
        state.since = time.time()
        state.last_error = None
        # Resolve the runner via the module namespace so monkeypatching
        # `_run_google_flow` in tests is visible at thread-start time
        # (a closure-captured reference would skip the patched symbol).
        runner = sys.modules[__name__]._run_google_flow
        threading.Thread(target=runner, args=(deps,), daemon=True).start()
        return {"state": "pending", "since": state.since}

    @router.get("/google/status")
    def google_status():
        s = deps.google_oauth
        return {"state": s.state, "since": s.since, "last_error": s.last_error}

    @router.post("/google/disconnect")
    def google_disconnect():
        token_path = getattr(deps, "google_token_path", None)
        if token_path is not None and token_path.exists():
            token_path.unlink()
        deps.google_oauth.state = "idle"
        deps.google_oauth.since = time.time()
        deps.google_oauth.last_error = None
        return {"ok": True}

    app.include_router(router)
