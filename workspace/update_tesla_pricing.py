#!/usr/bin/env python3
"""
update_tesla_pricing.py — LLM-assisted updates to tesla_pricing.json.

Flow:
  python3 update_tesla_pricing.py --nlp "raise Model Y RWD to $41,990 and drop FSD to $7000"
    -> Local Ollama parses the message against the current JSON, returns a
       strict path-based patch, validation succeeds, pending file is written,
       and the diff is emitted as JSON on stdout for KonaClaw to render and
       confirm.
  python3 update_tesla_pricing.py --confirm
    -> Re-validate the pending patch, write a timestamped .bak, atomically
       rename in the updated JSON, and emit a success JSON on stdout.
  python3 update_tesla_pricing.py --cancel
    -> Discard the pending patch.

Safety rules enforced before any write:
  - Every "path" must already exist in tesla_pricing.json (path allowlist).
  - Every leaf being updated must be numeric in both old and new values.
  - Every "old_value" the model reported must exactly match the live value.
  - Pending patches expire after 15 minutes.
"""
import os
import sys
import json
import time
import argparse
import urllib.request
import pathlib
from datetime import datetime

WORKSPACE = pathlib.Path(__file__).resolve().parent
PRICING_PATH = WORKSPACE / "tesla_pricing.json"
PENDING_PATH = WORKSPACE / "tesla_pricing.pending.json"
PENDING_TTL_SEC = 15 * 60

BOT_TOKEN = None  # KonaClaw delivers via dashboard, not Telegram
CHAT_ID = None
OLLAMA_URL = os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("KC_DEFAULT_MODEL", "gemma4:26b-mlx-bf16")


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _emit(payload):
    """Emit a JSON status object on stdout for KonaClaw to render."""
    print(json.dumps(payload))


def fmt_money(v):
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{int(v):,}"


def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return (False, None)
        cur = cur[part]
    return (True, cur)


def set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value


def call_llm(user_msg, current_pricing):
    """Use local Ollama to convert a natural-language pricing change request
    into a strict JSON patch against tesla_pricing.json. The prompt is preserved
    verbatim from the original Gemini implementation — only the API endpoint
    changes — because the path allowlist + validation rules below depend on
    its exact wording.
    """
    schema = json.dumps(current_pricing, indent=2)
    prompt = f"""You convert a natural-language Tesla pricing change request into a strict JSON patch against the file tesla_pricing.json.

Current tesla_pricing.json contents:
```json
{schema}
```

The user said:
\"\"\"{user_msg}\"\"\"

Return STRICT JSON ONLY (no prose, no markdown fences) matching:

{{
  "updates": [
    {{"path": "base_msrp.MYRWD.price", "old_value": 39990, "new_value": 41990, "what": "Model Y RWD MSRP"}}
  ],
  "summary": "one-line human-readable description of what is changing"
}}

RULES:
- "path" MUST be a dot-separated path that ALREADY EXISTS in the JSON above. Never invent new keys.
- Only update NUMERIC leaves: base_msrp.*.price, paint.*.upcharge, paint.*.upcharge_ms_mx, wheels.*.upcharge, wheels.*.upcharge_myrwd, interior.*.upcharge, interior.*.upcharge_ms_mx, addons.*.price, residuals.<TRIM>.<MONTHS>.
- Never change "label", "_note", "_calibrated", or any string leaf via this tool.
- "old_value" MUST exactly match the current value at that path in the JSON above.
- For residual percentages: "65%" -> 0.65. "0.6594" or "65.94" -> treat as already in the file's units (0.6594).
- If the request is ambiguous (e.g. "Model Y price" without specifying which trim), return updates=[] and use "summary" to ask which trim.
- If the request is unrelated to changing pricing (e.g. a quote request, a question, an offer query), return updates=[] and explain.
- Multiple changes in one message are fine -- include one entry per leaf.
- Do NOT wrap output in markdown fences or include any prose outside the JSON object.
"""
    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    content = (data.get("message", {}).get("content", "") or "").strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip().rstrip("`").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        import re as _re
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"could not parse JSON from Ollama: {content[:200]!r}")


def cmd_nlp(text):
    pricing = json.loads(PRICING_PATH.read_text())
    try:
        plan = call_llm(text, pricing)
    except Exception as e:
        _emit({"ok": False, "error": f"Couldn't parse pricing update: {e}"})
        return

    updates = plan.get("updates") or []
    summary = plan.get("summary") or "(no summary)"

    if not updates:
        _emit({
            "ok": True,
            "pending": False,
            "status": "no_updates",
            "summary": summary,
        })
        return

    validated = []
    errors = []
    for u in updates:
        path = u.get("path", "")
        new_val = u.get("new_value")
        old_val = u.get("old_value")
        what = u.get("what") or path
        ok, cur = get_path(pricing, path)
        if not ok:
            errors.append(f"path not found: {path}")
            continue
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            errors.append(f"path not numeric: {path}")
            continue
        if not isinstance(new_val, (int, float)) or isinstance(new_val, bool):
            errors.append(f"new value not numeric for {path}: {new_val!r}")
            continue
        if old_val is not None and cur != old_val:
            errors.append(
                f"stale read at {path}: model thought {old_val}, actually {cur}"
            )
            continue
        validated.append(
            {"path": path, "old": cur, "new": new_val, "what": what}
        )

    if errors:
        _emit({"ok": False, "status": "rejected", "errors": errors})
        return
    if not validated:
        _emit({"ok": False, "status": "no_valid_updates"})
        return

    ts_now = time.time()
    PENDING_PATH.write_text(
        json.dumps(
            {"ts": ts_now, "summary": summary, "updates": validated}, indent=2
        )
    )

    diff_dict = {
        "summary": summary,
        "updates": [
            {
                "what": v["what"],
                "path": v["path"],
                "old": v["old"],
                "new": v["new"],
            }
            for v in validated
        ],
    }
    expires_iso = datetime.fromtimestamp(ts_now + PENDING_TTL_SEC).isoformat()
    print(json.dumps({"pending": True, "diff": diff_dict, "expires_at": expires_iso}))


def cmd_confirm():
    if not PENDING_PATH.exists():
        _emit({"ok": False, "status": "no_pending"})
        return
    pending = json.loads(PENDING_PATH.read_text())
    if time.time() - pending.get("ts", 0) > PENDING_TTL_SEC:
        PENDING_PATH.unlink()
        _emit({"ok": False, "status": "expired"})
        return

    pricing = json.loads(PRICING_PATH.read_text())
    for u in pending["updates"]:
        ok, cur = get_path(pricing, u["path"])
        if not ok:
            _emit({
                "ok": False,
                "status": "path_missing",
                "path": u["path"],
            })
            return
        if cur != u["old"]:
            _emit({
                "ok": False,
                "status": "drift",
                "path": u["path"],
                "expected_old": u["old"],
                "actual": cur,
            })
            return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = PRICING_PATH.parent / f"tesla_pricing.json.bak.{ts}"
    bak.write_text(PRICING_PATH.read_text())

    for u in pending["updates"]:
        set_path(pricing, u["path"], u["new"])

    tmp = PRICING_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pricing, indent=2))
    os.replace(tmp, PRICING_PATH)
    PENDING_PATH.unlink()

    _emit({
        "ok": True,
        "status": "applied",
        "summary": pending["summary"],
        "updates": pending["updates"],
        "backup": bak.name,
    })


def cmd_cancel():
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()
        _emit({"ok": True, "status": "cancelled"})
    else:
        _emit({"ok": False, "status": "no_pending"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nlp", help="Natural-language pricing change request")
    ap.add_argument("--confirm", action="store_true", help="Apply pending update")
    ap.add_argument("--cancel", action="store_true", help="Discard pending update")
    args = ap.parse_args()
    if args.confirm:
        cmd_confirm()
    elif args.cancel:
        cmd_cancel()
    elif args.nlp:
        cmd_nlp(args.nlp)
    else:
        ap.error("Pass --nlp <text>, --confirm, or --cancel")
