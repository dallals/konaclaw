#!/usr/bin/env python3
"""
update_tesla_from_screenshot.py — Parse a Tesla offers screenshot via local
Ollama vision and update tesla_offers.md + tesla_offers.json.

Invoked by KonaClaw's Tessy subagent. Takes an absolute file path to an image
(the supervisor's Tessy tool wrapper resolves an attachment id to a path
before calling this script).

Replaces ZeroClaw's Gemini + describe_image.py + rebuild_tessy_prompt.py chain
with a single local Ollama vision call.

Usage:
    KC_OLLAMA_URL=http://127.0.0.1:11434 \
    KC_DEFAULT_MODEL=gemma4:26b-mlx-bf16 \
    python3 update_tesla_from_screenshot.py <image_path>
"""
import base64
import json
import os
import pathlib
import sys
import urllib.request
from datetime import datetime


WORKSPACE = pathlib.Path(__file__).resolve().parent
OFFERS_MD = WORKSPACE / "tesla_offers.md"
OFFERS_JSON = WORKSPACE / "tesla_offers.json"

OLLAMA_URL = os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("KC_DEFAULT_MODEL", "gemma4:26b-mlx-bf16")

EXTRACT_PROMPT = (
    "This is a screenshot of Tesla's current-offers page (USA). "
    "Extract ALL offers, financing rates, lease prices, APR deals, bonuses, and perks visible. "
    "For each vehicle model or 'Every New Tesla' section, list every offer with full details "
    "(price, terms, eligibility, expiration if shown). Be complete and verbatim. "
    "Return the text formatted as readable markdown. "
    "Do NOT add commentary or summaries — just the extracted offers."
)


def extract_offers(image_path: pathlib.Path) -> str:
    """Call local Ollama vision model with the screenshot. Return extracted markdown."""
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    # Ollama's /api/chat accepts `images: [base64_str, ...]` on a user message
    # for vision-capable models. The model handles MIME detection internally.
    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": EXTRACT_PROMPT,
                "images": [b64],
            },
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    content = data.get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"empty response from Ollama: {data!r}")
    return content.strip()


def write_offers(extracted: str, image_path: pathlib.Path) -> str:
    """Persist the extracted offers to tesla_offers.md + tesla_offers.json."""
    stamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    md_content = f"""# Tesla Current Offers (USA)
*Last updated: {stamp} — sourced from screenshot of tesla.com/current-offers*

> This file is auto-updated. Tessy should always read this file for current Tesla promotions.

---

{extracted}
"""
    OFFERS_MD.write_text(md_content, encoding="utf-8")
    # Also persist a minimal JSON snapshot so future code can read structured
    # offers without re-parsing the markdown. v1: store the raw text under
    # `raw_markdown`. Future versions can add per-model parsing.
    OFFERS_JSON.write_text(
        json.dumps({
            "updated_at": stamp,
            "source_image": str(image_path),
            "raw_markdown": extracted,
            "extraction_model": OLLAMA_MODEL,
        }, indent=2),
        encoding="utf-8",
    )
    return stamp


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update_tesla_from_screenshot.py <image_path>", file=sys.stderr)
        sys.exit(1)
    image_path = pathlib.Path(sys.argv[1])
    if not image_path.exists():
        print(f"ERROR: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    extracted = extract_offers(image_path)
    if len(extracted) < 50:
        print(f"ERROR: extracted text too short ({len(extracted)} chars) — model may have failed", file=sys.stderr)
        print(f"raw: {extracted!r}", file=sys.stderr)
        sys.exit(1)

    stamp = write_offers(extracted, image_path)
    # Emit a single JSON line on stdout — Tessy's tool wrapper parses this.
    print(json.dumps({
        "ok": True,
        "updated_at": stamp,
        "chars": len(extracted),
        "model": OLLAMA_MODEL,
    }))


if __name__ == "__main__":
    main()
