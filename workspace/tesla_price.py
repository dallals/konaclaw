#!/usr/bin/env python3
"""
tesla_price.py — Tesla vehicle pricing + financing breakdown.

Usage:
  # Natural language (local Ollama parses intent):
  python3 tesla_price.py --nlp "Model X Grey 5 seater, 20000 down, 3.99% for 72 months at 91528"

  # Structured params:
  python3 tesla_price.py --trim rwd --paint red --wheels 19 --interior black \
                          --zip 95128 --months 72 --down 7000 [--apr 5.99]

Options:
  --nlp       Natural language description (local Ollama extracts params automatically)
  --trim      rwd, lrawd, awd, performance (MY); m3/m3lr/m3perf (M3); mx5/mx6/mx7/mxplaid (MX); ms/msplaid (MS); ct
  --paint     red, blue, white, black, silver, grey, stealth
  --wheels    19, 20
  --interior  black, white, cream
  --zip       ZIP code for tax calculation
  --months    Loan term in months (36, 48, 60, 72, 84)
  --down      Amount due at signing (down payment)
  --apr       Annual percentage rate (default: 5.99)
  --silent    Return JSON only (no chat delivery -- KonaClaw renders the JSON in the dashboard)
"""
import sys, json, urllib.request, urllib.parse, os, argparse

BOT_TOKEN       = None  # KonaClaw delivers via dashboard, not Telegram
CHAT_ID         = None
FIRECRAWL_KEY   = os.environ.get("FIRECRAWL_API_KEY")
# Firecrawl is only used to refresh live MSRP from Tesla's order page.
# When missing or invalid, fetch_firecrawl_price() returns None and the
# script falls back to the local MSRP table — which is the path Sammy
# normally wants anyway. No hard exit.

# Local Ollama for NLP arg parsing — replaces ZeroClaw's Gemini dep.
OLLAMA_URL = os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("KC_DEFAULT_MODEL", "gemma4:26b-mlx-bf16")
FIRECRAWL_URL   = "https://api.firecrawl.dev/v1/scrape"

# ── Configuration maps ────────────────────────────────────────────────────────

TRIM_INFO = {
    # Model Y (all aliases)
    # Base RWD — $39,900
    "my":              {"inv_code": "MYRWD",      "label": "Model Y RWD",              "model": "my", "promo_apr": 0.0},
    "rwd":             {"inv_code": "MYRWD",      "label": "Model Y RWD",              "model": "my", "promo_apr": 0.0},
    "myrwd":           {"inv_code": "MYRWD",      "label": "Model Y RWD",              "model": "my", "promo_apr": 0.0},
    "mystandard":      {"inv_code": "MYRWD",      "label": "Model Y RWD",              "model": "my", "promo_apr": 0.0},
    "myrearwheel":     {"inv_code": "MYRWD",      "label": "Model Y RWD",              "model": "my", "promo_apr": 0.0},
    # RWD Premium — $44,900
    "rwdpremium":      {"inv_code": "MYRWDP",     "label": "Model Y RWD Premium",      "model": "my", "promo_apr": 0.99},
    "myrwdpremium":    {"inv_code": "MYRWDP",     "label": "Model Y RWD Premium",      "model": "my", "promo_apr": 0.99},
    "myrwdp":          {"inv_code": "MYRWDP",     "label": "Model Y RWD Premium",      "model": "my", "promo_apr": 0.99},
    # AWD — $41,990 — 0% APR promo
    "awd":             {"inv_code": "MYAWD",      "label": "Model Y AWD",              "model": "my", "promo_apr": 0.0},
    "myawd":           {"inv_code": "MYAWD",      "label": "Model Y AWD",              "model": "my", "promo_apr": 0.0},
    # AWD Premium / Long Range AWD — $48,990 — 0% APR promo
    "lrawd":           {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    "mylr":            {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    "mylrawd":         {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    "mylongrange":     {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    "awdpremium":      {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    "myawdpremium":    {"inv_code": "MYLRAWD",    "label": "Model Y AWD Premium",      "model": "my", "promo_apr": 0.0},
    # Performance — $57,490
    "performance":     {"inv_code": "MYPERF",     "label": "Model Y Performance",      "model": "my", "promo_apr": 5.09},
    "myperformance":   {"inv_code": "MYPERF",     "label": "Model Y Performance",      "model": "my", "promo_apr": 5.09},
    "myperf":          {"inv_code": "MYPERF",     "label": "Model Y Performance",      "model": "my", "promo_apr": 5.09},
    # Model 3 (all aliases)
    # Base RWD — $36,990
    "m3":            {"inv_code": "M3RWD",    "label": "Model 3 RWD",             "model": "m3"},
    "m3rwd":         {"inv_code": "M3RWD",    "label": "Model 3 RWD",             "model": "m3"},
    "m3standard":    {"inv_code": "M3RWD",    "label": "Model 3 RWD",             "model": "m3"},
    # Premium RWD — $42,490 — 0.99% APR promo
    "m3rwdpremium":  {"inv_code": "M3RWDP",   "label": "Model 3 RWD Premium",     "model": "m3", "promo_apr": 0.99},
    "m3premium":     {"inv_code": "M3RWDP",   "label": "Model 3 RWD Premium",     "model": "m3", "promo_apr": 0.99},
    "m3rwdp":        {"inv_code": "M3RWDP",   "label": "Model 3 RWD Premium",     "model": "m3", "promo_apr": 0.99},
    # Premium AWD / Long Range — $47,490 — 0.99% APR promo
    "m3lr":          {"inv_code": "M3LRAWD",  "label": "Model 3 Premium AWD",     "model": "m3", "promo_apr": 0.99},
    "m3lrawd":       {"inv_code": "M3LRAWD",  "label": "Model 3 Premium AWD",     "model": "m3", "promo_apr": 0.99},
    "m3longrange":   {"inv_code": "M3LRAWD",  "label": "Model 3 Premium AWD",     "model": "m3", "promo_apr": 0.99},
    "m3awdpremium":  {"inv_code": "M3LRAWD",  "label": "Model 3 Premium AWD",     "model": "m3", "promo_apr": 0.99},
    # Performance — $54,990 — 0.99% APR promo
    "m3perf":        {"inv_code": "M3PERF",   "label": "Model 3 Performance AWD", "model": "m3", "promo_apr": 0.99},
    "m3performance": {"inv_code": "M3PERF",   "label": "Model 3 Performance AWD", "model": "m3", "promo_apr": 0.99},
    # Model X
    "mx":            {"inv_code": "MXLRAWD",  "label": "Model X Long Range AWD",  "model": "mx", "promo_apr": 5.09},
    "mxlr":          {"inv_code": "MXLRAWD",  "label": "Model X Long Range AWD",  "model": "mx", "promo_apr": 5.09},
    "mx5":           {"inv_code": "MXLRAWD",  "label": "Model X Long Range (5-seat)", "model": "mx", "promo_apr": 5.09},
    "mx6":           {"inv_code": "MXLRAWD6", "label": "Model X Long Range (6-seat)", "model": "mx", "promo_apr": 5.09},
    "mx7":           {"inv_code": "MXLRAWD7", "label": "Model X Long Range (7-seat)", "model": "mx", "promo_apr": 5.09},
    "mxplaid":       {"inv_code": "MXPLAID",  "label": "Model X Plaid",           "model": "mx", "promo_apr": 5.09},
    # Model S
    "ms":            {"inv_code": "MSLRAWD",  "label": "Model S Long Range AWD",  "model": "ms", "promo_apr": 5.09},
    "mslr":          {"inv_code": "MSLRAWD",  "label": "Model S Long Range AWD",  "model": "ms", "promo_apr": 5.09},
    "msplaid":       {"inv_code": "MSPLAID",  "label": "Model S Plaid",           "model": "ms", "promo_apr": 5.09},
    "mscybertruk":   {"inv_code": "CTDMAWD",  "label": "Cybertruck Dual Motor AWD","model": "ct"},
    "ct":            {"inv_code": "CTDMAWD",  "label": "Cybertruck Dual Motor AWD","model": "ct"},
    "ctdm":          {"inv_code": "CTDMAWD",  "label": "Cybertruck Dual Motor AWD","model": "ct"},
    "ctdualmotор":   {"inv_code": "CTDMAWD",  "label": "Cybertruck Dual Motor AWD","model": "ct"},
    "ctpremium":     {"inv_code": "CTPAWD",   "label": "Cybertruck Premium AWD",   "model": "ct", "promo_apr": 3.99},
    "ctpawd":        {"inv_code": "CTPAWD",   "label": "Cybertruck Premium AWD",   "model": "ct", "promo_apr": 3.99},
    "cyberbeast":    {"inv_code": "CTBEAST",  "label": "Cyberbeast",               "model": "ct"},
    "ctbeast":       {"inv_code": "CTBEAST",  "label": "Cyberbeast",               "model": "ct"},
}

PAINT_INFO = {
    "red":     {"label": "Ultra Red",                  "upcharge": 2000},
    "blue":    {"label": "Blue",                  "upcharge": 1000},  # MY/M3; Frost Blue ($2,500) on MS/MX
    "white":   {"label": "Pearl White Multi-Coat",      "upcharge": 1000},
    "black":   {"label": "Solid Black",                 "upcharge": 1500},
    "silver":  {"label": "Quicksilver",                 "upcharge": 2000},
    "grey":    {"label": "Stealth Grey",                "upcharge": 0},     # Midnight Silver replaced
    "stealth": {"label": "Stealth Grey",                "upcharge": 0},
}

WHEEL_INFO = {
    "18": {"label": '18" Wheels (standard)',   "upcharge": 0},
    "19": {"label": '19" Gemini Wheels',       "upcharge": 0},   # Included on some trims
    "20": {"label": '20" Induction Wheels',    "upcharge": 2000},
    "21": {"label": '21" Velarium Wheels',     "upcharge": 4500},
}

INTERIOR_INFO = {
    "black": {"label": "All Black",        "upcharge": 0},
    "white": {"label": "Black and White",  "upcharge": 1000},   # $1,000 on M3/MY; adjusted to $1,500 for MS/MX below
    "cream": {"label": "Cream",            "upcharge": 2000},
}

ADDON_INFO = {
    "fsd":         {"label": "Full Self-Driving (FSD)",    "price": 8000},
    "autopilot":   {"label": "Enhanced Autopilot",         "price": 3500},
    "eap":         {"label": "Enhanced Autopilot",         "price": 3500},
    "tow":         {"label": "Tow Hitch",                  "price": 1000},
    "towhitch":    {"label": "Tow Hitch",                  "price": 1000},
    "7seat":       {"label": "7-Seat Interior",            "price": 3000},
    "sevenseat":   {"label": "7-Seat Interior",            "price": 3000},
    "thirdseat":   {"label": "7-Seat Interior",            "price": 3000},
    "yoke":        {"label": "Yoke Steering",              "price": 1000},
    "yokesteering":{"label": "Yoke Steering",              "price": 1000},
}

# Tesla published MSRP (March 2026 — confirmed by Sammy)
BASE_MSRP = {
    # Model Y
    "MYRWD":    39990,   # RWD base
    "MYRWDP":   44990,   # RWD Premium
    "MYAWD":    41990,   # AWD standard
    "MYLRAWD":  48990,   # AWD Premium / Long Range AWD
    "MYPERF":   57490,   # Performance
    # Model 3
    "M3RWD":    36990,   # Base RWD
    "M3RWDP":   42490,   # Premium RWD
    "M3LRAWD":  47490,   # Premium AWD (Long Range)
    "M3PERF":   54990,   # Performance
    # Model X
    "MXLRAWD":  79990,
    "MXLRAWD6": 84990,
    "MXLRAWD7": 84990,
    "MXPLAID":  99990,
    # Model S
    "MSLRAWD":  94990,   # Long Range AWD (includes Luxe Package + FSD)
    "MSPLAID":  109990,
    # Cybertruck
    "CTDMAWD":  69990,   # Dual Motor AWD
    "CTPAWD":   79990,   # Premium AWD
    "CTBEAST":  99990,   # Cyberbeast
}

# ── Load overrides from tesla_pricing.json (edit prices there, not here) ─────
_PRICING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tesla_pricing.json")
try:
    with open(_PRICING_FILE) as _f:
        _cfg = json.load(_f)
    # Paint
    for _k, _v in _cfg.get("paint", {}).items():
        PAINT_INFO[_k] = {"label": _v["label"], "upcharge": _v["upcharge"]}
        if "label_ms_mx" in _v:
            PAINT_INFO[_k]["label_ms_mx"] = _v["label_ms_mx"]
        if "upcharge_ms_mx" in _v:
            PAINT_INFO[_k]["upcharge_ms_mx"] = _v["upcharge_ms_mx"]
    # Wheels
    for _k, _v in _cfg.get("wheels", {}).items():
        WHEEL_INFO[_k] = {"label": _v["label"], "upcharge": _v["upcharge"]}
        if "upcharge_myrwd" in _v:
            WHEEL_INFO[_k]["upcharge_myrwd"] = _v["upcharge_myrwd"]
        if "label_myrwd" in _v:
            WHEEL_INFO[_k]["label_myrwd"] = _v["label_myrwd"]
    # Interior
    for _k, _v in _cfg.get("interior", {}).items():
        INTERIOR_INFO[_k] = {"label": _v["label"], "upcharge": _v["upcharge"]}
        if "upcharge_ms_mx" in _v:
            INTERIOR_INFO[_k]["upcharge_ms_mx"] = _v["upcharge_ms_mx"]
    # Add-ons
    for _k, _v in _cfg.get("addons", {}).items():
        ADDON_INFO[_k] = {"label": _v["label"], "price": _v["price"]}
    # Base MSRP
    for _k, _v in _cfg.get("base_msrp", {}).items():
        BASE_MSRP[_k] = _v["price"]
    # Residuals (overrides hardcoded values below)
    _residuals_raw = _cfg.get("residuals", {})
    _JSON_RESIDUALS = {k: {int(mo): v for mo, v in terms.items()} for k, terms in _residuals_raw.items() if not k.startswith("_")}
    _residuals_calibrated = _residuals_raw.get("_calibrated", "")
except FileNotFoundError:
    _JSON_RESIDUALS = {}  # fall back to hardcoded values below
    _residuals_calibrated = ""
# ─────────────────────────────────────────────────────────────────────────────

# State sales tax by ZIP prefix
def get_tax_rate(zip_code):
    """Estimate sales tax rate from ZIP code (2-digit prefix for better accuracy)."""
    z = str(zip_code).strip()[:5]
    if len(z) < 2:
        return 0.0725  # US average fallback
    prefix2 = z[:2]
    # State-level tax by 2-digit ZIP prefix (includes avg local)
    TAX_BY_PREFIX = {
        # CA (90-96)
        "90": 0.095, "91": 0.095, "92": 0.0775, "93": 0.0775,
        "94": 0.09125, "95": 0.09125, "96": 0.0775,
        # TX (73,75-79)
        "73": 0.0825, "75": 0.0825, "76": 0.0825, "77": 0.0825,
        "78": 0.0825, "79": 0.0825,
        # FL (32-34)
        "32": 0.07, "33": 0.07, "34": 0.07,
        # NY (10-14)
        "10": 0.08875, "11": 0.08875, "12": 0.08, "13": 0.08, "14": 0.08,
        # NJ (07-08)
        "07": 0.06625, "08": 0.06625,
        # PA (15-19)
        "15": 0.07, "16": 0.06, "17": 0.06, "18": 0.06, "19": 0.08,
        # IL (60-62)
        "60": 0.1025, "61": 0.0725, "62": 0.0725,
        # WA (98-99)
        "98": 0.1025, "99": 0.089,
        # OR — no sales tax
        "97": 0.0,
        # CO (80-81)
        "80": 0.0775, "81": 0.065,
        # AZ (85-86)
        "85": 0.08, "86": 0.075,
        # NV (89)
        "89": 0.0825,
        # GA (30-31)
        "30": 0.0789, "31": 0.07,
        # NC (27-28)
        "27": 0.0475, "28": 0.07,
        # VA (20,22-24)
        "20": 0.06, "22": 0.06, "23": 0.053, "24": 0.053,
        # MA (01-02)
        "01": 0.0625, "02": 0.0625,
        # CT (06)
        "06": 0.0635,
        # MD (20-21)
        "21": 0.06,
        # OH (43-45)
        "43": 0.0725, "44": 0.08, "45": 0.0725,
        # MI (48-49)
        "48": 0.06, "49": 0.06,
        # MN (55-56)
        "55": 0.0773, "56": 0.0673,
        # MT, NH — no sales tax (DE shares "19" prefix with PA; PA rate kept)
        "59": 0.0, "03": 0.0,
    }
    # State name by 2-digit ZIP prefix
    STATE_BY_PREFIX = {
        "90": "CA", "91": "CA", "92": "CA", "93": "CA", "94": "CA", "95": "CA", "96": "CA",
        "73": "TX", "75": "TX", "76": "TX", "77": "TX", "78": "TX", "79": "TX",
        "32": "FL", "33": "FL", "34": "FL",
        "10": "NY", "11": "NY", "12": "NY", "13": "NY", "14": "NY",
        "07": "NJ", "08": "NJ",
        "15": "PA", "16": "PA", "17": "PA", "18": "PA", "19": "PA",
        "60": "IL", "61": "IL", "62": "IL",
        "98": "WA", "99": "WA",
        "97": "OR",
        "80": "CO", "81": "CO",
        "85": "AZ", "86": "AZ",
        "89": "NV",
        "30": "GA", "31": "GA",
        "27": "NC", "28": "NC",
        "20": "VA", "22": "VA", "23": "VA", "24": "VA",
        "01": "MA", "02": "MA",
        "06": "CT",
        "21": "MD",
        "43": "OH", "44": "OH", "45": "OH",
        "48": "MI", "49": "MI",
        "55": "MN", "56": "MN",
        "59": "MT", "03": "NH",
    }
    FALLBACK_STATE = {"0": "NE", "1": "NY", "2": "VA", "3": "FL",
                      "4": "OH", "5": "MO", "6": "IL", "7": "TX",
                      "8": "CO", "9": "CA"}
    # 5-digit exact-match overrides for ZIPs with known local district rates
    ZIP_OVERRIDES = {
        # San Jose, CA (10.5% confirmed April 2026 from Tesla checkout)
        "95101": 0.105, "95108": 0.105, "95110": 0.105,
        "95111": 0.105, "95112": 0.105, "95113": 0.105,
        "95116": 0.105, "95117": 0.105, "95118": 0.105,
        "95119": 0.105, "95120": 0.105, "95121": 0.105,
        "95122": 0.105, "95123": 0.105, "95124": 0.105,
        "95125": 0.105, "95126": 0.105, "95127": 0.105,
        "95128": 0.105, "95129": 0.105, "95130": 0.105,
        "95131": 0.105, "95132": 0.105, "95133": 0.105,
        "95134": 0.105, "95135": 0.105, "95136": 0.105,
        "95138": 0.105, "95139": 0.105, "95140": 0.105,
        # Fremont, CA - Tesla HQ area (10.25%)
        "94536": 0.1025, "94538": 0.1025, "94539": 0.1025, "94555": 0.1025,
        # Seattle, WA core (10.25%)
        "98101": 0.1025, "98102": 0.1025, "98103": 0.1025, "98104": 0.1025,
        "98105": 0.1025, "98107": 0.1025, "98109": 0.1025, "98112": 0.1025,
        "98115": 0.1025, "98117": 0.1025, "98118": 0.1025, "98122": 0.1025,
    }
    state = STATE_BY_PREFIX.get(prefix2, FALLBACK_STATE.get(z[0], "CA"))
    if z in ZIP_OVERRIDES:
        return ZIP_OVERRIDES[z], state
    rate = TAX_BY_PREFIX.get(prefix2)
    if rate is not None:
        return rate, state
    # Fallback to first digit
    FALLBACK = {"0": 0.0625, "1": 0.08, "2": 0.06, "3": 0.07,
                "4": 0.07, "5": 0.065, "6": 0.075, "7": 0.0825,
                "8": 0.08, "9": 0.09}
    return FALLBACK.get(z[0], 0.0725), state

# Removed 2026-05-16: fetch_inventory_price() and fetch_firecrawl_price().
# Both attempted live MSRP scraping; in practice Tesla blocked the inventory
# API (403) and Firecrawl needed an external key. Local BASE_MSRP table is
# kept current via `tesla.update_pricing` — same source of truth, no
# external deps, no latency.

# ── Monthly payment formulas ──────────────────────────────────────────────────

def monthly_payment(principal, apr_pct, months):
    if apr_pct == 0 or months == 0:
        return principal / (months or 1)
    r = (apr_pct / 100) / 12
    return principal * (r * (1 + r)**months) / ((1 + r)**months - 1)

ACQ_FEE = 695  # Tesla acquisition fee — update here if Tesla changes it

def monthly_lease(msrp, sale_price, due_at_signing, months, apr_pct, tax_rate=0.0, residual_pct=None):
    """Standard lease payment formula (closed-end lease).
    Residual: model-specific (default 50% for 36mo, 46% for 48mo, 42% for 60mo)
    Money factor = APR / 2400
    Sales tax is applied to each monthly payment (standard in most states).
    """
    if residual_pct is None:
        residual_pct = {36: 0.50, 48: 0.46, 60: 0.42}.get(months, 0.50)
    residual_value = msrp * residual_pct
    money_factor = apr_pct / 2400 if apr_pct > 0 else 0.00125
    # Tesla charges the acquisition fee upfront at signing (included in DAD),
    # not capitalized. Rolling it into gross cap AND charging upfront in DAD
    # double-counts the fee and inflates monthly payment.
    gross_cap = sale_price
    cap_reduction = due_at_signing  # cap cost reduction = down payment
    net_cap = gross_cap - cap_reduction
    base_payment = (net_cap - residual_value) / months
    finance_charge = (net_cap + residual_value) * money_factor
    pre_tax = base_payment + finance_charge
    # Sales tax applied per payment (most states tax each lease payment)
    return pre_tax * (1 + tax_rate), residual_value, money_factor * 2400


# ── Live money factor from Tesla Design Studio ────────────────────────────────
_MF_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".mf_cache.json")

# Verified money factors from real Tesla/Santander lease documents.
# NOTE: These are 36-month baselines. Update when new deals are confirmed.
# Model 3: MF=0.0000100 confirmed from Santander lease doc, April 2026
_KNOWN_MF = {
    "model3":     0.000672,   # back-calc from Tesla $338/mo M3RWD 36/10k $3k-down 95128, April 2026 (≈1.61% APR)
    "modely":     0.001789,   # back-calc from Tesla $637/mo MYRWDP 36/10k $3k-down 95128, April 2026 (≈4.29% APR)
    "modelx":     0.002100,   # rough estimate — recalibrate when Tesla quote available
    "models":     0.002100,   # rough estimate — recalibrate when Tesla quote available
    "cybertruck": 0.001500,   # rough estimate — recalibrate when Tesla quote available
}

def fetch_lease_money_factor(model_slug="modely"):
    """Back-calculate Tesla's current lease money factor from Design Studio.

    Uses two published data points (with $3,000 down vs $0 down, same 36-mo term)
    to solve for MF without needing the residual:
        Δmonthly = Δcap/term + Δcap × MF
    Cache persists to disk for 1 week to stay within Firecrawl free tier.
    """
    import time
    # model3 & modely: Tesla Design Studio default pricing doesn't match real
    # 95128 quotes (regional CA markup not reflected). Use _KNOWN_MF directly.
    if model_slug in ("model3", "modely") and model_slug in _KNOWN_MF:
        mf = _KNOWN_MF[model_slug]
        print(f"[Live MF] {model_slug}: skipping Firecrawl (regional markup mismatch); using known-good MF={mf:.6f} APR≈{mf*2400:.2f}%")
        return mf
    # Load disk cache
    try:
        with open(_MF_CACHE_FILE) as f:
            _disk_cache = json.load(f)
    except Exception:
        _disk_cache = {}
    cached = _disk_cache.get(model_slug)
    if cached and (time.time() - cached[1]) < 604800:  # 1 week
        print(f"[Live MF] {model_slug}: using cached MF={cached[0]:.6f} APR≈{cached[0]*2400:.2f}%")
        return cached[0]

    try:
        actions = [
            {"type": "wait", "milliseconds": 3000},
            {"type": "click", "selector": "[label=Lease]"},
            {"type": "wait", "milliseconds": 2000},
        ]
        payload = json.dumps({
            "url": f"https://www.tesla.com/{model_slug}/design#payment",
            "formats": ["markdown"],
            "actions": actions,
        }).encode()
        req = urllib.request.Request(
            "https://api.firecrawl.dev/v1/scrape",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {FIRECRAWL_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())

        md = data.get("data", {}).get("markdown", "")

        # Extract the two monthly payment amounts and down amounts
        import re
        # Pattern: "$X /mo" and "$Y down, 36 mo" and "$0 down for $Z /mo"
        # Find base monthly (with $3,000 down) — first /mo after trim list
        pmt_matches = re.findall(r"\$([\d,]+)[\s\xa0]+/mo", md)
        if len(pmt_matches) < 1:
            return None

        # The first trim's payment (RWD, simplest vehicle)
        pmt_with_down = float(pmt_matches[0].replace(",", ""))

        # Find "$0 down for $X /mo"
        zero_down_m = re.search(r"\$0 down for \$([\d,]+)", md)
        if not zero_down_m:
            return None
        pmt_zero_down = float(zero_down_m.group(1).replace(",", ""))

        # Find down amount from "$X down, 36 mo"
        down_m = re.search(r"\$([\d,]+)\s*down,\s*(\d+)\s*mo", md)
        if not down_m:
            return None
        down_amt = float(down_m.group(1).replace(",", ""))
        term = int(down_m.group(2))

        # Back-calculate: Δmonthly = Δcap/term + Δcap × MF
        delta_monthly = pmt_zero_down - pmt_with_down
        delta_cap = down_amt  # $3,000 cap reduction vs $0
        mf = (delta_monthly - delta_cap / term) / delta_cap
        if mf <= 0 or mf > 0.01:
            return None  # sanity check

        print(f"[Live MF] {model_slug}: MF={mf:.6f} APR≈{mf*2400:.2f}% (${pmt_with_down}/mo with ${down_amt} down, ${pmt_zero_down}/mo $0 down, {term}mo)")
        _disk_cache[model_slug] = [mf, time.time()]
        try:
            with open(_MF_CACHE_FILE, "w") as _f:
                json.dump(_disk_cache, _f)
        except Exception:
            pass
        return mf
    except Exception as e:
        print(f"[MF fetch error: {e}]")
        # Fall back to last known-good verified MF (36-month baseline)
        if model_slug in _KNOWN_MF:
            print(f"[Live MF] {model_slug}: using known-good fallback MF={_KNOWN_MF[model_slug]:.7f}")
            return _KNOWN_MF[model_slug]
        return None


def fetch_finance_apr(model_slug="modely"):
    """Fetch Tesla's current promotional finance APR from Design Studio.
    Cache persists to disk for 1 week (same cache file as MF).
    """
    import time
    cache_key = f"{model_slug}_finance_apr"
    try:
        with open(_MF_CACHE_FILE) as f:
            _disk_cache = json.load(f)
    except Exception:
        _disk_cache = {}
    cached = _disk_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < 604800:
        print(f"[Live APR] {model_slug}: using cached APR={cached[0]:.2f}%")
        return cached[0]

    try:
        actions = [
            {"type": "wait", "milliseconds": 3000},
            {"type": "click", "selector": "[label=Finance]"},
            {"type": "wait", "milliseconds": 2000},
        ]
        payload = json.dumps({
            "url": f"https://www.tesla.com/{model_slug}/design#payment",
            "formats": ["markdown"],
            "actions": actions,
        }).encode()
        req = urllib.request.Request(
            "https://api.firecrawl.dev/v1/scrape",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {FIRECRAWL_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())

        md = data.get("data", {}).get("markdown", "")

        import re
        # Match "0% APR" or "5.99% APR" or "0% APR, $X down, Y mo"
        m = re.search(r"([\d.]+)%\s*APR", md)
        if not m:
            return None
        apr = float(m.group(1))
        print(f"[Live APR] {model_slug}: {apr}% APR (fetched from Tesla Design Studio)")

        _disk_cache[cache_key] = [apr, time.time()]
        try:
            with open(_MF_CACHE_FILE, "w") as _f:
                json.dump(_disk_cache, _f)
        except Exception:
            pass
        return apr
    except Exception as e:
        print(f"[Finance APR fetch error: {e}]")
        return None

def fmt_usd(n):
    return f"${n:,.0f}"

def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# (send_telegram removed — KonaClaw renders output via the dashboard)

# ── NLP parser via local Ollama ───────────────────────────────────────────────

def parse_nlp(text):
    """Extract structured Tesla config params from natural-language text via local Ollama."""
    system_prompt = (
        "You extract Tesla vehicle configuration params from natural-language text. "
        "Return ONLY a JSON object with these keys (omit keys not mentioned). "
        "trim — one of these exact values: "
        "Model Y: rwd (base RWD), rwdpremium/myrwdp (RWD Premium), awd/myawd (AWD), "
        "awdpremium/mylr/lrawd (AWD Premium / Long Range), performance/myperf (Performance). "
        "Model 3: m3/m3rwd (base RWD), m3premium/m3rwdp (RWD Premium), m3lr/m3lrawd (Premium AWD), m3perf (Performance). "
        "Model X: mx5 (5-seat LR), mx6 (6-seat LR), mx7 (7-seat LR), mxplaid (Plaid). "
        "Model S: ms/mslr (Long Range), msplaid (Plaid). Cybertruck: ct. "
        "Rules: 'Model Y' with no qualifier = rwd. 'long range' = mylr. 'performance' = myperf. '5 seater/5 seat' Model X = mx5. "
        "paint — one of: red, blue, white, black, silver, grey, stealth "
        "(map color names: gray->grey, ultra red->red, midnight silver->grey). "
        "wheels — the string '19' or '20' (no inch marks). "
        "interior — one of: black, white, cream. "
        "zip — 5-digit ZIP code as string. "
        "months — loan term integer (24/36/48/60/72/84). "
        "down — cap cost reduction / down-payment amount. Extract on phrases: 'X down', "
        "'down payment of X', 'X due at signing', 'X at signing', 'put X down'. "
        "Return null ONLY when the figure is clearly Tesla's quoted due-at-delivery, not the user's intent. "
        "apr — interest rate as a percentage number (NOT divided by 100). "
        "Examples: '0.99%' -> 0.99, '5.99%' -> 5.99, '.99%' -> 0.99. Never return 0.0099 for 0.99%. "
        "miles — annual mileage allowance integer: 10000, 12000, or 15000. "
        "lease — true if the user wants to LEASE, false if financing/purchasing (default false). "
        "addons — array from: fsd, autopilot, tow, 7seat ([] if none mentioned). "
        "Output ONLY the JSON object. No prose. No backticks."
    )
    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    try:
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
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re as _re
            m = _re.search(r"\{.*\}", content, _re.DOTALL)
            if m:
                return json.loads(m.group(0))
            raise ValueError(f"could not parse JSON from Ollama response: {content[:200]!r}")
    except Exception as e:
        print(f"[NLP parse error: {e}]")
        return {}


# ── Main ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Tesla pricing + financing calculator")
parser.add_argument("--nlp",      default=None,   help="Natural language description — local Ollama extracts params")
parser.add_argument("--model",    default="my",    help="Model: my, m3")
parser.add_argument("--trim",     default="rwd",   help="Trim: rwd, lrawd, awd, performance")
parser.add_argument("--paint",    default="grey", help="Paint: red, blue, white, black, silver, grey")
parser.add_argument("--wheels",   default="18",    help="Wheels: 18, 19, 20")
parser.add_argument("--interior", default="black", help="Interior: black, white, cream")
parser.add_argument("--zip",      default="95128", help="ZIP code")
parser.add_argument("--months",   type=int,   default=72,   help="Loan term months")
parser.add_argument("--down",     type=float, default=0,    help="Down payment / due at signing")
parser.add_argument("--apr",      type=float, default=None, help="APR percent (default: promo rate)")
parser.add_argument("--addons",   default="",   help="Comma-separated add-ons: fsd,autopilot,tow,7seat")
parser.add_argument("--lease",    action="store_true", help="Calculate lease payment instead of loan")
parser.add_argument("--miles",    type=int,   default=10000, help="Annual mileage allowance (default 10,000)")
parser.add_argument("--silent",   action="store_true")
args = parser.parse_args()
_apr_user_specified = args.apr is not None
if args.apr is None:
    args.apr = 5.99  # internal default

# ── NLP override: parse natural language into structured params ───────────────
if args.nlp:
    # Guard: reject offer/promotion queries — those should be answered from the promotions reference
    _nlp_lower = args.nlp.lower()
    _offer_words = {"offer", "offers", "promotion", "promotions", "deal", "deals", "incentive", "incentives", "promotional"}
    _price_words = {"price", "cost", "how much", "monthly", "payment", "financing", "lease", "out-the-door", "otd", "buy", "purchase"}
    _has_offer = any(w in _nlp_lower for w in _offer_words)
    if _has_offer:
        import os as _os, re
        _offers_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tesla_offers.md")
        try:
            with open(_offers_path) as _f:
                _offers = _f.read()

            # Detect which model was requested
            _model_map = {
                "model 3": ["model 3", "m3"],
                "model y": ["model y", "my"],
                "model s": ["model s", "ms"],
                "model x": ["model x", "mx"],
                "cybertruck": ["cybertruck", "ct"],
            }
            _requested_model = None
            for _model, _aliases in _model_map.items():
                if any(a in _nlp_lower for a in _aliases):
                    _requested_model = _model
                    break

            if _requested_model:
                # Filter offers to only lines relevant to requested model
                # Keep: header, general financing/intro, lines mentioning the model, section headers
                _other_models = [m for m in _model_map if m != _requested_model]
                _filtered_lines = []
                for _line in _offers.splitlines():
                    _line_lower = _line.lower()
                    # Skip lines that mention OTHER models but not the requested one
                    _mentions_other = any(m in _line_lower for m in _other_models)
                    _mentions_requested = _requested_model in _line_lower
                    if _mentions_other and not _mentions_requested:
                        continue
                    _filtered_lines.append(_line)
                _offers = chr(10).join(_filtered_lines)

            _label = f"Tesla {_requested_model.title()}" if _requested_model else "Tesla"
            print(f"Current {_label} offers:")
            print(_offers)
            # KonaClaw reads stdout; the offers markdown was already printed above.
        except Exception as _e:
            print(f"Could not load Tesla offers: {_e}", file=sys.stderr)
        import sys; sys.exit(0)
    print(f"Parsing request via local Ollama NLP: {args.nlp}")
    nlp = parse_nlp(args.nlp)
    print(f"Extracted params: {nlp}")
    if nlp.get("trim"):    args.trim     = nlp["trim"]
    if nlp.get("paint"):   args.paint    = nlp["paint"]
    if nlp.get("wheels"):  args.wheels   = str(nlp["wheels"])
    if nlp.get("interior"):args.interior = nlp["interior"]
    if nlp.get("zip"):     args.zip      = str(nlp["zip"])
    if nlp.get("months"):  args.months   = int(nlp["months"])
    if nlp.get("down"):    args.down     = float(nlp["down"])
    if nlp.get("apr"):
        apr_val = float(nlp["apr"])
        # Guard: if the model divided by 100 (e.g. returned 0.0099 for 0.99%), correct it
        if apr_val < 0.1:
            apr_val = apr_val * 100
        args.apr = apr_val
        _apr_user_specified = True
    if nlp.get("miles"):  args.miles = int(nlp["miles"])
    if nlp.get("lease"):
        args.lease = True
    if nlp.get("addons"):
        addon_list = nlp["addons"]
        if isinstance(addon_list, list):
            args.addons = ",".join(str(a) for a in addon_list)
        elif isinstance(addon_list, str):
            args.addons = addon_list

trim_key     = args.trim.lower()
paint_key    = args.paint.lower()
wheel_key    = args.wheels
interior_key = args.interior.lower()

trim     = TRIM_INFO.get(trim_key, TRIM_INFO["rwd"])
paint    = PAINT_INFO.get(paint_key, PAINT_INFO["grey"])
wheel    = WHEEL_INFO.get(wheel_key, WHEEL_INFO["19"])
interior = INTERIOR_INFO.get(interior_key, INTERIOR_INFO["black"])
inv_code = trim["inv_code"]
model    = trim["model"]
# M3 Performance: 20" Warp Wheels are always standard (no upcharge, no alternative)
if inv_code == "M3PERF":
    wheel = {"label": '20" Warp Wheels', "upcharge": 0}
# MY Performance: 21" Arachnid 2.0 Wheels are standard — included in base price
if inv_code == "MYPERF":
    wheel = {"label": '21" Arachnid 2.0 Wheels', "upcharge": 0}
# Apply model-specific overrides (values come from tesla_pricing.json)
if model in ("ms", "mx"):
    if "upcharge_ms_mx" in interior:
        interior = dict(interior, upcharge=interior["upcharge_ms_mx"])
    if "upcharge_ms_mx" in paint:
        paint = dict(paint, upcharge=paint["upcharge_ms_mx"])
    if "label_ms_mx" in paint:
        paint = dict(paint, label=paint["label_ms_mx"])
# Model Y RWD: 19" upgrade is Crossflow Wheels at $1,500 (not Gemini, not included)
if inv_code == "MYRWD" and wheel_key == "19":
    if "upcharge_myrwd" in wheel:
        wheel = dict(wheel, upcharge=wheel["upcharge_myrwd"])
    if "label_myrwd" in wheel:
        wheel = dict(wheel, label=wheel["label_myrwd"])

tax_rate, state = get_tax_rate(args.zip)

# --- Get base vehicle price ---
price_source = ""
base_price = None

# Base price comes from the local BASE_MSRP table (Sammy keeps it current
# via `tesla.update_pricing`). Tesla's prices change a few times a year —
# scraping live (inventory API or Firecrawl) added external deps, latency,
# and a failure mode without changing the answer 99% of the time. Removed
# 2026-05-16.
base_price_clean = BASE_MSRP.get(inv_code, 44990)
price_source = "Local MSRP table"

# Add option upcharges
# MYPERF + M3PERF: all options included (paint, wheels, interior — no upcharges).
_incl_paint    = (inv_code in ("MYPERF", "M3PERF"))
_incl_wheels   = (inv_code in ("MYPERF", "M3PERF"))
_incl_interior = (inv_code in ("MYPERF", "M3PERF"))
options = []
if paint["upcharge"] > 0 and not _incl_paint:
    options.append((paint["label"], paint["upcharge"]))
if wheel["upcharge"] > 0 and not _incl_wheels:
    options.append((wheel["label"], wheel["upcharge"]))
if interior["upcharge"] > 0 and not _incl_interior:
    options.append((interior["label"], interior["upcharge"]))
# Add-ons
for addon_key in [a.strip().lower().replace("-","").replace(" ","") for a in args.addons.split(",") if a.strip()]:
    addon = ADDON_INFO.get(addon_key)
    if addon:
        options.append((addon["label"], addon["price"]))

options_total = sum(p for _, p in options)
vehicle_price = base_price_clean + options_total

# Taxes, fees, total
destination  = 1390
order_fee    = 250
taxable      = vehicle_price + destination + order_fee
sales_tax    = round(taxable * tax_rate)

# Government / DMV fees — CA only (ZIP-derived state detection above)
# Non-CA states: fees vary widely; we do not estimate them
_ca_gov_fees_available = (state == "CA")
if _ca_gov_fees_available:
    if vehicle_price < 5000:       tif = 25
    elif vehicle_price < 25000:    tif = 50
    elif vehicle_price < 35000:    tif = 100
    elif vehicle_price < 60000:    tif = 175
    else:                          tif = 204
    gov_reg   = tif + 68 + 27 + 25 + 35  # TIF + base reg + CHP + smog + county/misc
    gov_vlf   = round(vehicle_price * 0.00668)
    gov_tire  = 7
    gov_efile = 37
else:
    gov_reg = gov_vlf = gov_tire = gov_efile = 0
gov_fees_total = gov_reg + gov_vlf + gov_tire + gov_efile

total_oop    = vehicle_price + destination + order_fee + sales_tax + gov_fees_total

# Financing or Lease
loan_amount    = max(0, total_oop - order_fee - args.down)
if args.lease:
    lease_months = args.months if args.months in (24, 36) else 36
    if args.down == 0:
        args.down = 5000 if model == "ct" else 3000  # CT uses $5k down, others $3k
    # Use live money factor from Tesla Design Studio unless APR was explicitly set
    _model_slug_map = {"my": "modely", "m3": "model3", "mx": "modelx", "ms": "models", "ct": "cybertruck"}
    _live_apr = None
    if not _apr_user_specified:  # user did not specify APR — try live rate
        _slug = _model_slug_map.get(model, "modely")
        _live_mf = fetch_lease_money_factor(_slug)
        if _live_mf is None and _slug in _KNOWN_MF:
            _live_mf = _KNOWN_MF[_slug]
            print(f"[Lease MF] {_slug}: using known-good fallback MF={_live_mf:.7f} APR≈{_live_mf*2400:.2f}%")
        if _live_mf:
            _live_apr = round(_live_mf * 2400, 4)
    # args.apr default (5.99) is a FINANCE rate, not a lease rate.
    # Only use it as the lease MF if the user explicitly set --apr.
    if _live_apr is not None:
        lease_apr = _live_apr
    elif _apr_user_specified:
        lease_apr = args.apr
    else:
        # Last resort: conservative lease baseline (~3% APR), better than 5.99% finance rate.
        lease_apr = 3.0
        print(f"[Lease MF] no known-good MF for {model}; using conservative 3.0% APR baseline")
    # Trim-specific residual percentages (back-calculated from Tesla published payments)
    # Model Y: ~48.5% for 36mo (April 2026)
    # Model 3: per-trim (Tesla lease promo April 3 2026 — $100/mo cut across all trims)
    # Residuals back-calculated from NA Pricing Matrix April 3, 2026 (pre-tax)
    _RESIDUALS_BY_TRIM = _JSON_RESIDUALS  # loaded from tesla_pricing.json
    _RESIDUALS_BY_MODEL = {
        "my": {24: 0.57, 36: 0.485},
        "m3": {24: 0.60, 36: 0.50},
        "mx": {24: 0.60, 36: 0.50},
        "ms": {24: 0.60, 36: 0.50},
        "ct": {24: 0.60, 36: 0.50},
    }
    _res_lookup = _RESIDUALS_BY_TRIM.get(inv_code) or _RESIDUALS_BY_MODEL.get(model, {24: 0.58, 36: 0.50})
    _res_pct_val = _res_lookup.get(lease_months, 0.50)
    # Mileage adjustment: higher miles = lower residual = higher payment
    # Tesla standard: 10k/yr base. Each 2k miles/yr ≈ -2% residual
    _miles_adj = {10000: 0.00, 12000: -0.02, 15000: -0.04}
    _res_pct_val += _miles_adj.get(args.miles, 0.00)
    monthly_pmt, residual_val, effective_apr = monthly_lease(
        vehicle_price, vehicle_price, args.down, lease_months, lease_apr, tax_rate,
        residual_pct=_res_pct_val,
    )
    res_pct        = round(_res_pct_val * 100)
    total_interest = 0
    acq_fee = ACQ_FEE
    # Upfront tax: CA taxes down payment + acq fee + doc/order fees at signing
    upfront_tax = round((args.down + acq_fee) * tax_rate, 2)
    # Government / DMV fees (CA-specific estimates; 0 for other states)
    due_at_delivery = round(args.down + monthly_pmt + acq_fee + upfront_tax + gov_fees_total)
    # Total cash out = DAD at signing + (lease_months - 1) remaining monthly pmts.
    # 1st month is already included in due_at_delivery, so we don't double-count it.
    total_paid     = due_at_delivery + monthly_pmt * (lease_months - 1)
else:
    lease_months   = args.months
    residual_val   = 0
    # Use promotional APR from trim data if user didn't specify one
    _promo_apr = trim.get("promo_apr")
    if not _apr_user_specified and _promo_apr is not None:
        finance_apr = _promo_apr
    else:
        finance_apr = args.apr
    effective_apr  = finance_apr
    monthly_pmt    = monthly_payment(loan_amount, finance_apr, args.months)
    total_interest = (monthly_pmt * args.months) - loan_amount
    total_paid     = args.down + (monthly_pmt * args.months)

result = {
    "trim": trim["label"],
    "paint": paint["label"],
    "wheels": wheel["label"],
    "interior": interior["label"],
    "base_price": base_price_clean,
    "options_total": options_total,
    "vehicle_price": vehicle_price,
    "destination": destination,
    "sales_tax": sales_tax,
    "total_oop": total_oop,
    "down_payment": args.down,
    "loan_amount": 0 if args.lease else loan_amount,
    "apr": args.apr,
    "months": args.months,
    "monthly_payment": round(monthly_pmt, 2),
    "total_interest": round(total_interest, 2),
    "total_paid": round(total_paid, 2),
    "price_source": price_source,
}

if args.silent:
    print(json.dumps(result, indent=2))
    sys.exit(0)

# ── Build human-readable message (stdout) ────────────────────────────────────
lines = [
    f"<b>Tesla {model.upper()} — Pricing Breakdown</b>",
    "",
    "<b>Configuration:</b>",
    f"  Trim:     {esc(trim['label'])}",
    f"  Paint:    {esc(paint['label'])}",
    f"  Wheels:   {esc(wheel['label'])}",
    f"  Interior: {esc(interior['label'])}",
    f"  ZIP:      {args.zip} ({state})",
    "",
    "<b>Price Breakdown:</b>",
    f"  Base vehicle price:     {fmt_usd(base_price_clean)}",
]
for label, price in options:
    lines.append(f"  + {esc(label):30s} {fmt_usd(price)}")
lines += [
    f"  Destination fee:          {fmt_usd(destination)}",
    f"  Order fee:                {fmt_usd(order_fee)}",
]
if args.lease:
    # Lease tax is per-payment, not upfront on full price.
    # Don't show "Out-of-pocket total" in lease mode — it's purchase-only and misleading.
    lines += [
        "  ─────────────────────────────────",
        f"  <b>MSRP + dest + order:    {fmt_usd(vehicle_price + destination + order_fee)}</b>",
        f"  <i>(sales tax applied per lease payment; DMV fees shown in due-at-signing)</i>",
        "",
    ]
else:
    lines += [
        f"  Sales tax ({tax_rate*100:.3f}% {state}):   {fmt_usd(sales_tax)}",
        (f"  Registration &amp; DMV fees:  {fmt_usd(gov_fees_total)}" if _ca_gov_fees_available else f"  Registration &amp; DMV fees:  varies by {state} — not estimated"),
        "  ─────────────────────────────────",
        f"  <b>Out-of-pocket total:    {fmt_usd(total_oop)}{('' if _ca_gov_fees_available else ' (excl. reg fees)')}</b>",
        "",
    ]
if args.lease:
    lines += [
        f"<b>Lease ({lease_months}-mo / {args.miles:,} mi/yr @ {lease_apr:.2f}% APR):</b>",
        f"  Due at signing:        ~{fmt_usd(due_at_delivery)} (${int(args.down):,} down + 1st mo + acq fee + tax &amp; gov fees)",
        f"  Residual value (~{res_pct:.0f}%): {fmt_usd(residual_val)}",
        f"  <b>Monthly payment:       {fmt_usd(monthly_pmt)}/mo</b>",
        f"  Total lease cost:      {fmt_usd(total_paid)}",
        "",
        f"<i>Price source: {esc(price_source)}</i>",
        f"<i>Lease estimate — residual &amp; money factor set by Tesla monthly{(' | ⚠️ Residuals calibrated ' + _residuals_calibrated + ' — recalibrate if payments drift') if _residuals_calibrated and (__import__('datetime').date.today() - __import__('datetime').date.fromisoformat(_residuals_calibrated)).days > 35 else ''}</i>",
    ]
else:
    lines += [
        f"<b>Financing ({args.months}-mo @ {finance_apr:.2f}% APR{' — current promo rate' if (not _apr_user_specified and trim.get('promo_apr') == finance_apr) else ''}):</b>",
        f"  Due at signing:        {fmt_usd(args.down)}",
        f"  Loan amount:           {fmt_usd(loan_amount)}",
        f"  <b>Monthly payment:       {fmt_usd(monthly_pmt)}/mo</b>",
        f"  Total interest paid:   {fmt_usd(total_interest)}",
        f"  Total cost (life):     {fmt_usd(total_paid)}",
        "",
        f"<i>Price source: {esc(price_source)}</i>",
        "<i>Promotional APR — subject to credit approval, min 5% down</i>" if (not _apr_user_specified and trim.get("promo_apr") == finance_apr) else "<i>APR estimate — actual rate depends on credit &amp; lender</i>",
        "" if _apr_user_specified else "<i>\U0001f4ac Reply with your actual financing rate for a personalized estimate.</i>",
    ]
lines += [
]

msg = "\n".join(lines)
# ZeroClaw emitted HTML for Telegram; KonaClaw renders plain text in the
# dashboard. Strip the bold/italic tags and decode the two HTML entities
# the script uses, so the output displays cleanly.
import re as _re, html as _html
msg = _re.sub(r"</?[bi]>", "", msg)
msg = _html.unescape(msg)
print(msg)
