from __future__ import annotations
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from kc_core.tools import Tool


_PRICE_PARAMS = {
    "type": "object",
    "properties": {
        "nlp": {"type": "string", "description": "Natural language config description, e.g. 'Model Y RWD, 7k down, 72 months, 95128'."},
        "trim": {"type": "string", "description": "rwd, lrawd, awd, performance, m3, m3lr, mx5, mxplaid, ms, ct, etc."},
        "paint": {"type": "string", "description": "red, blue, white, black, silver, grey, stealth"},
        "wheels": {"type": "integer", "description": "19 or 20"},
        "interior": {"type": "string", "description": "black, white, cream"},
        "zip": {"type": "string", "description": "ZIP code for tax calculation"},
        "months": {"type": "integer", "description": "36, 48, 60, 72, 84"},
        "down": {"type": "number", "description": "Amount due at signing"},
        "apr": {"type": "number", "description": "Annual percentage rate (default 5.99)"},
    },
}

_UPDATE_PRICING_PARAMS = {
    "type": "object",
    "properties": {
        "nlp": {"type": "string", "description": "Natural language pricing change, e.g. 'raise Model Y RWD to $41,990'."},
    },
    "required": ["nlp"],
}

_CONFIRM_PRICING_PARAMS = {"type": "object", "properties": {}}

_UPDATE_OFFERS_PARAMS = {
    "type": "object",
    "properties": {
        "attachment_id": {"type": "string", "description": "KonaClaw attachment id (att_xxxxxxxxxxxx) of a Tesla offers screenshot."},
    },
    "required": ["attachment_id"],
}


def _run_subprocess(workspace_dir: Path, args: list[str], timeout: int = 30) -> str:
    """Run a workspace script; return stdout. Returns a JSON error string on failure."""
    try:
        proc = subprocess.run(
            ["python3", *args],
            cwd=str(workspace_dir), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "timeout", "args": args, "timeout_s": timeout})
    if proc.returncode != 0:
        return json.dumps({
            "error": "nonzero_exit",
            "code": proc.returncode,
            "stderr": proc.stderr.strip()[:300] if proc.stderr else "",
        })
    return proc.stdout.strip()


def build_tessy_tools(
    *, workspace_dir: Path, attachment_store: Any,
) -> dict[str, Tool]:
    """Returns the four Tessy tools.

    `attachment_store` is the kc_attachments.AttachmentStore singleton from
    Deps; only `tesla.update_offers_from_image` uses it (to resolve an
    attachment id into a file path).
    """

    async def _price_impl(**kwargs) -> str:
        args = ["tesla_price.py", "--silent"]
        nlp = kwargs.get("nlp")
        if nlp:
            args.extend(["--nlp", nlp])
        for k in ("trim", "paint", "wheels", "interior", "zip", "months", "down", "apr"):
            v = kwargs.get(k)
            if v is not None:
                args.extend([f"--{k}", str(v)])
        return await asyncio.to_thread(_run_subprocess, workspace_dir, args, 60)

    async def _update_pricing_impl(nlp: str) -> str:
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_pricing.py", "--nlp", nlp], 60,
        )

    async def _confirm_pricing_impl() -> str:
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_pricing.py", "--confirm"], 30,
        )

    async def _update_offers_impl(attachment_id: str) -> str:
        if attachment_store is None:
            return json.dumps({"error": "no_attachment_store"})
        try:
            path = attachment_store.original_path(attachment_id)
        except Exception as e:  # noqa: BLE001
            return json.dumps({
                "error": "attachment_not_found",
                "attachment_id": attachment_id,
                "detail": str(e),
            })
        return await asyncio.to_thread(
            _run_subprocess, workspace_dir,
            ["update_tesla_from_screenshot.py", str(path)], 300,
        )

    return {
        "tesla.price": Tool(
            name="tesla.price",
            description="Calculate Tesla pricing/financing for a config. Pass either `nlp` (free-text) or structured params (trim, paint, wheels, interior, zip, months, down, apr).",
            parameters=_PRICE_PARAMS,
            impl=_price_impl,
        ),
        "tesla.update_pricing": Tool(
            name="tesla.update_pricing",
            description="Propose a pricing change via natural language. Returns the proposed diff; call tesla.confirm_pricing to apply.",
            parameters=_UPDATE_PRICING_PARAMS,
            impl=_update_pricing_impl,
        ),
        "tesla.confirm_pricing": Tool(
            name="tesla.confirm_pricing",
            description="Apply the last proposed pricing change from tesla.update_pricing.",
            parameters=_CONFIRM_PRICING_PARAMS,
            impl=_confirm_pricing_impl,
        ),
        "tesla.update_offers_from_image": Tool(
            name="tesla.update_offers_from_image",
            description="Parse a Tesla offers screenshot (by attachment id) and update tesla_offers.md/json.",
            parameters=_UPDATE_OFFERS_PARAMS,
            impl=_update_offers_impl,
        ),
    }
