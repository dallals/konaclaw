#!/usr/bin/env python3
"""
update_tesla_offers.py — Fetch Tesla current offers ONLY from
https://www.tesla.com/current-offers (no web search, no Perplexity).

Sources in a single Firecrawl call:
  - screenshot@fullPage → describe_image.py extracts APR + every-new-Tesla
                          offers (KonaClaw-supplied vision backend)
  - rawHtml → we parse lease disclaimer footnotes for lease availability,
              starting purchase price, down payment, term, miles, fees

Monthly lease payments on tesla.com/current-offers are state-specific and
loaded via an authenticated pricing API we cannot call, so those are surfaced
only where Tesla itself hardcodes them on the page (e.g. Cybertruck $849/mo).
For every other model we report the deterministic lease terms and direct the
customer to tesla.com/current-offers for their state-specific monthly quote.
"""
import os
import re
import sys
import json
import html
import pathlib
import subprocess
import urllib.request
from datetime import datetime

# KonaClaw delivers via dashboard, not Telegram — no BOT_TOKEN / CHAT_ID needed.
BOT_TOKEN = None
CHAT_ID = None
OLLAMA_URL = os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("KC_DEFAULT_MODEL", "gemma4:26b-mlx-bf16")

WORKSPACE = pathlib.Path(__file__).resolve().parent
FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY")
if FIRECRAWL_KEY is None:
    print("FIRECRAWL_API_KEY not set", file=sys.stderr)
    sys.exit(2)
TESLA_URL = "https://www.tesla.com/current-offers"
SHOT_PATH = WORKSPACE / "tesla_offers_screenshot.png"
JSON_PATH = WORKSPACE / "tesla_offers.json"
MD_PATH = WORKSPACE / "tesla_offers.md"

MODEL_KEYS = {
    "model_s": ("Model S", "models-lease"),
    "model_3": ("Model 3", "model3-lease"),
    "model_y": ("Model Y", "modely-lease"),
    "model_x": ("Model X", "modelx-lease"),
    "cybertruck": ("Cybertruck", "cybertruck-lease"),
}

# ── 1. Firecrawl: screenshot + rawHtml in one call ─────────────────────────
req = urllib.request.Request(
    "https://api.firecrawl.dev/v1/scrape",
    data=json.dumps({
        "url": TESLA_URL,
        "formats": ["screenshot@fullPage", "rawHtml"],
        "location": {"country": "US", "languages": ["en-US"]},
        "actions": [{"type": "wait", "milliseconds": 6000}],
    }).encode(),
    headers={
        "Authorization": f"Bearer {FIRECRAWL_KEY}",
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        fc = json.loads(r.read())
except Exception as e:
    print(f"ERROR: Firecrawl request failed: {e}")
    sys.exit(1)

data = fc.get("data") or {}
shot_url = data.get("screenshot")
raw_html = data.get("rawHtml") or ""
if not shot_url or not raw_html:
    print(f"ERROR: Firecrawl response missing screenshot or rawHtml: {fc}")
    sys.exit(1)
urllib.request.urlretrieve(shot_url, SHOT_PATH)

# ── 2. Parse rawHtml disclaimer footnotes for lease terms per model ───────
disclaimer_blob = " ".join(raw_html.split("disclaimerId"))
# Escaped unicode sequences like \u0026 can appear; decode them
raw_text = raw_html.encode("utf-8").decode("unicode_escape", errors="ignore")
# Strip HTML tags and decode entities for matching
flat = html.unescape(re.sub(r"<[^>]+>", " ", raw_text))
flat = re.sub(r"\s+", " ", flat)


def parse_lease_terms(flat_text: str, model_label: str) -> dict | None:
    """Return a dict of deterministic lease terms for `model_label` from the
    Tesla disclaimer footnote, or None if no lease disclaimer is present."""
    # Core sentence we key on: "Lease price based on <Model X> <trim> with a
    # starting purchase price of $NN,NNN"
    m = re.search(
        rf"Lease price based on ({re.escape(model_label)}[^.]{{0,120}}?) "
        rf"with a starting purchase price of \$([0-9,]+)",
        flat_text,
    )
    if not m:
        return None
    trim = m.group(1).replace(model_label, "").strip(" ,")
    start_price = f"${m.group(2)}"
    terms: dict[str, str | None] = {
        "trim": trim or None,
        "starting_price": start_price,
        "down": None,
        "months": None,
        "miles_per_year": None,
        "acquisition_fee": None,
        "overage_rate": None,
        "application_date": None,
        "monthly_payment": None,
    }
    # After the starting-price sentence, the same disclaimer lists the terms
    tail = flat_text[m.end(): m.end() + 1200]
    if (mo := re.search(r"Requires \$([0-9,]+) down", tail)):
        terms["down"] = f"${mo.group(1)}"
    if (mo := re.search(r"for (\d+) months", tail)):
        terms["months"] = mo.group(1)
    if (mo := re.search(r"(\d{1,3}(?:,\d{3})?|\d+[Kk])\s+miles per year", tail)):
        terms["miles_per_year"] = mo.group(1).replace("K", ",000").replace("k", ",000")
    if (mo := re.search(r"\$([0-9,]+) acquisition fee", tail)):
        terms["acquisition_fee"] = f"${mo.group(1)}"
    if (mo := re.search(r"\$0?\.?(\d{1,2})\s*/\s*mile", tail)):
        terms["overage_rate"] = f"$0.{mo.group(1)}/mile"
    head = flat_text[max(0, m.start() - 400):m.start()]
    if (mo := re.search(
        r"applications submitted after ([A-Z][a-z]+ \d{1,2},? \d{4})", head
    )):
        terms["application_date"] = mo.group(1)
    return terms


lease_by_model: dict[str, dict | None] = {}
for key, (label, disc_id) in MODEL_KEYS.items():
    terms = parse_lease_terms(flat, label)
    if terms is None:
        continue
    # Hardcoded monthly price (rare — only present where Tesla pins it, e.g.
    # Cybertruck $849/mo). Look in raw_html anchored to this model's
    # disclaimer id so we can't cross-assign prices between models.
    anchor = re.search(
        rf"Lease from \$([0-9,]+)\\?/mo[^<]{{0,80}}"
        rf'<sup data-disclaimer-id=\\?"{re.escape(disc_id)}\\?"',
        raw_html,
    )
    if anchor:
        terms["monthly_payment"] = f"${anchor.group(1)}/mo"
    lease_by_model[key] = terms

# ── 3. Vision LLM: extract APR + every-new-Tesla perks ────────────────────
prompt = (
    "This is a full-page screenshot of tesla.com/current-offers (USA). "
    "Extract every currently-listed offer. Return STRICT JSON only — no prose, "
    "no markdown fences — exactly matching this schema:\n\n"
    "{\n"
    '  "every_new_tesla": ["<bullet>", ...],\n'
    '  "apr": {\n'
    '    "model_s": ["<bullet>", ...],\n'
    '    "model_3": ["<bullet>", ...],\n'
    '    "model_y": ["<bullet>", ...],\n'
    '    "model_x": ["<bullet>", ...],\n'
    '    "cybertruck": ["<bullet>", ...]\n'
    "  }\n"
    "}\n\n"
    "Each APR bullet should be a concise customer-facing summary of ONE "
    "financing offer, e.g. "
    '"0% APR for 72 months on Model Y RWD and AWD (min 5% down)". '
    "Include APR rate, term length, trim eligibility, and down-payment "
    "requirements. DO NOT include lease offers — those are handled separately. "
    "Put perks that apply to all models (trade-in Supercharging bonus, FSD "
    "trial/subscription, $500 off for heroes, loan-interest deduction, "
    "Section 179, Premium Connectivity trial) under 'every_new_tesla'. "
    "If a model has no APR offer visible, use an empty array. "
    "Output ONLY the JSON object."
)
result = subprocess.run(
    ["python3", str(WORKSPACE / "describe_image.py"), str(SHOT_PATH), prompt],
    capture_output=True, text=True, timeout=120,
)
raw = result.stdout.strip()
if raw.startswith("```"):
    raw = raw.split("```", 2)[1]
    if raw.lower().startswith("json"):
        raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()
if not raw.startswith("{"):
    brace = raw.find("{")
    if brace >= 0:
        raw = raw[brace:]
try:
    vision = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"ERROR: vision LLM did not return valid JSON: {e}")
    print(raw[:1500])
    sys.exit(1)

apr_by_model = vision.get("apr") or {}
every_new = vision.get("every_new_tesla") or []

# ── 4. Build structured offers ────────────────────────────────────────────
def fmt_lease_bullet(terms: dict) -> str:
    if terms.get("monthly_payment"):
        head = f"Lease from {terms['monthly_payment']}"
        if terms.get("down"):
            head += f" with {terms['down']} down"
    else:
        head = (
            "Lease available — monthly payment varies by state "
            "(see tesla.com/current-offers for your state's quote)"
        )
    term_bits = []
    if terms.get("months"):
        term_bits.append(f"{terms['months']} months")
    if terms.get("miles_per_year"):
        term_bits.append(f"{terms['miles_per_year']} mi/yr")
    if terms.get("acquisition_fee"):
        term_bits.append(f"{terms['acquisition_fee']} acquisition fee")
    trim = terms.get("trim")
    starting = terms.get("starting_price")
    tail_bits = []
    if term_bits:
        tail_bits.append("; ".join(term_bits))
    if trim and starting:
        tail_bits.append(f"{trim} starts at {starting}")
    elif starting:
        tail_bits.append(f"starts at {starting}")
    if tail_bits:
        return f"{head} ({' — '.join(tail_bits)})"
    return head


offers: dict[str, object] = {"every_new_tesla": every_new}
for key in MODEL_KEYS:
    bullets: list[str] = list(apr_by_model.get(key) or [])
    terms = lease_by_model.get(key)
    if terms:
        bullets.append(fmt_lease_bullet(terms))
    offers[key] = bullets

# ── 5. Persist JSON + markdown ────────────────────────────────────────────
JSON_PATH.write_text(json.dumps(offers, indent=2))

updated = datetime.now().strftime("%B %d, %Y at %I:%M %p")
md = [
    "# Tesla Current Offers (USA)",
    f"*Last updated: {updated} — sourced exclusively from {TESLA_URL}*",
    "",
    "> Auto-updated weekly. Tessy reads the INSTANT ANSWERS block in "
    "config.toml, which is regenerated from this data.",
    "",
]
md.append("## Every New Tesla")
md += [f"- {b}" for b in every_new] or ["- No every-new-Tesla offers listed"]
md.append("")
for key, (label, _d) in MODEL_KEYS.items():
    md.append(f"## {label}")
    bullets = offers.get(key) or []
    if bullets:
        md += [f"- {b}" for b in bullets]
    else:
        md.append("- No current offers listed on tesla.com/current-offers")
    md.append("")
MD_PATH.write_text("\n".join(md))
print(f"[tesla_offers.md updated: {updated}]")

# ── 6. Rebuild Tessy's INSTANT ANSWERS block ──────────────────────────────
r = subprocess.run(
    ["python3", str(WORKSPACE / "rebuild_tessy_prompt.py")],
    capture_output=True, text=True,
)
print(r.stdout.strip())
if r.returncode != 0:
    print(r.stderr.strip(), file=sys.stderr)
    sys.exit(r.returncode)
