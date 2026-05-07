from __future__ import annotations
import platform
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/connectors")

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
    except Exception:
        pass


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


def install(app, deps: Any) -> None:
    """Mount the connectors router. Called from service.py at app build time."""

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

    app.include_router(router)
