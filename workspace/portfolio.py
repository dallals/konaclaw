#!/usr/bin/env python3
"""
portfolio.py — Fetch live Yahoo Finance prices for Sammy's full portfolio.
Usage: python3 portfolio.py
       python3 portfolio.py --silent   (prints JSON only)
"""
import sys, json, urllib.request, os

def esc(t): return (t or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

SILENT    = "--silent" in sys.argv

# Sammy's portfolio. Sourced from workspace/holdings.json when present
# (produced by sync_holdings.py from local rPlanner Postgres), with the
# hardcoded fallback below for offline use or first-run before any sync.
# Emergency cash ($233k) is tracked separately and NOT included here.
_FALLBACK_HOLDINGS = {
    "AAPL":  {"basis": 679336,  "shares": 5088},
    "NVDA":  {"basis":  40036,  "shares": 5375},
    "VOO":   {"basis": 485552,  "shares": 1097},
    "VBIL":  {"basis": 201719,  "shares": 2670},
    "GOOGL": {"basis": 128137,  "shares":  625},
    "TSLA":  {"basis": 118840,  "shares":  457},
    "AMZN":  {"basis": 159452,  "shares":  695},
    "VFIAX": {"basis":  35666,  "shares":  170},
    "VUG":   {"basis":  59070,  "shares": 1278},  # 6-for-1 split effective 2026-04-21 (was 213)
    "MSFT":  {"basis":  84965,  "shares":  230},
    "QQQ":   {"basis":  65438,  "shares":  115},
    "AMD":   {"basis":  46554,  "shares":  300},
    "VTI":   {"basis":  22207,  "shares":  149},
    "VYM":   {"basis":  37972,  "shares":  257},
    "IJH":   {"basis":  19565,  "shares":  492},
    "OWL":   {"basis":  12634,  "shares": 1196},
    "VXUS":  {"basis":  80820,  "shares":  970},
}


def _load_holdings():
    """Prefer the synced holdings.json (live rPlanner); fall back to the
    hardcoded dict above. Lets `python3 portfolio.py` work even before the
    first sync, and survives Postgres being unreachable."""
    p = os.path.join(os.path.dirname(__file__), "holdings.json")
    if not os.path.isfile(p):
        return _FALLBACK_HOLDINGS, "fallback"
    try:
        with open(p) as f:
            payload = json.load(f)
        h = payload.get("holdings") or {}
        if not isinstance(h, dict) or not h:
            return _FALLBACK_HOLDINGS, "fallback"
        return h, payload.get("synced_at") or "synced"
    except (OSError, json.JSONDecodeError):
        return _FALLBACK_HOLDINGS, "fallback"


HOLDINGS, _HOLDINGS_SOURCE = _load_holdings()

def fetch(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def get_prev_close(data):
    """Use historical OHLC array for yesterday's close — more reliable than chartPreviousClose."""
    try:
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        valid = [c for c in closes if c is not None]
        return valid[-2] if len(valid) >= 2 else valid[-1]
    except Exception:
        meta = data["chart"]["result"][0]["meta"]
        return meta.get("chartPreviousClose", meta.get("regularMarketPrice", 0))

# Split detection. A stock price jumping by a clean integer ratio (forward split)
# or dropping by a clean integer ratio (reverse split) between prev close and
# current price is almost always a corporate action, not a market move. Index
# ETFs and blue-chips don't move ±50% in a day for any other reason.
# Ratios checked against a 2% tolerance to allow for normal day-1-of-split drift.
_FORWARD_SPLITS = (2, 3, 4, 5, 6, 8, 10, 15, 20)
_REVERSE_SPLITS = (2, 3, 4, 5, 10, 20)

def detect_split_ratio(prev_close, current_price):
    """Return split multiplier (shares-new / shares-old) if a clean integer split
    ratio is detected between prev_close and current_price. Otherwise None.
    Forward N:1 split → returns N (>1). Reverse 1:N split → returns 1/N (<1).
    """
    if not prev_close or not current_price or prev_close <= 0 or current_price <= 0:
        return None
    fwd = prev_close / current_price  # >1 means price dropped (forward split)
    for n in _FORWARD_SPLITS:
        if abs(fwd - n) / n < 0.02:
            return float(n)
    rev = current_price / prev_close  # >1 means price rose (reverse split)
    for n in _REVERSE_SPLITS:
        if abs(rev - n) / n < 0.02:
            return 1.0 / n
    return None

results = []
total_value = 0
total_basis = 0
total_day_change = 0

split_warnings = []  # collect splits to surface in output
for ticker, info in HOLDINGS.items():
    try:
        data = fetch(ticker)
        meta = data["chart"]["result"][0]["meta"]
        price  = meta.get("regularMarketPrice", 0)
        prev   = get_prev_close(data)
        shares = info["shares"]
        basis  = info["basis"]

        # Split guard: if prev_close vs price is a clean integer ratio, Yahoo
        # hasn't yet rebased its historical closes to post-split. Rebase the
        # prev close ourselves so day-change reflects real market movement and
        # not the split artifact. Also warn so the user verifies HOLDINGS.
        # We DO NOT auto-adjust `shares` — if HOLDINGS is already post-split
        # (manual update), auto-adjustment would double-count. The warning
        # prompts the user to update HOLDINGS; today's value is correct as
        # long as they keep HOLDINGS in sync with their brokerage.
        split_ratio = detect_split_ratio(prev, price)
        if split_ratio and split_ratio != 1.0:
            label = f"{int(split_ratio)}:1 forward" if split_ratio > 1 else f"1:{int(round(1/split_ratio))} reverse"
            split_warnings.append(f"{ticker} {label} split detected — verify HOLDINGS reflects new share count")
            prev = prev / split_ratio  # rebase prev to post-split basis

        value  = price * shares
        day_chg = (price - prev) * shares
        gain   = value - basis
        gain_pct = (gain / basis * 100) if basis else 0

        total_value    += value
        total_basis    += basis
        total_day_change += day_chg

        results.append({
            "ticker": ticker,
            "price": price,
            "prev": prev,
            "shares": shares,
            "value": value,
            "day_change": day_chg,
            "gain": gain,
            "gain_pct": gain_pct,
        })
    except Exception as e:
        results.append({"ticker": ticker, "error": str(e)})

total_gain = total_value - total_basis
total_gain_pct = (total_gain / total_basis * 100) if total_basis else 0
prev_value = total_value - total_day_change
day_pct    = (total_day_change / prev_value * 100) if prev_value else 0

if SILENT:
    print(json.dumps({"holdings": results, "total_value": total_value,
                      "total_gain": total_gain, "total_day_change": total_day_change,
                      "day_pct": day_pct, "holdings_source": _HOLDINGS_SOURCE}))
    sys.exit(0)

# Build human-readable message
arrow = lambda v: "▲" if v >= 0 else "▼"
sign  = lambda v: "+" if v >= 0 else ""
dsign = lambda v: "+" if v >= 0 else "-"  # for dollar amounts used with abs()

lines = ["📊 *Sammy's Portfolio — Live Update*", ""]

for r in results:
    if "error" in r:
        lines.append(f"❌ {r['ticker']}: {r['error']}")
        continue
    day_arrow = "📈" if r["day_change"] >= 0 else "📉"
    lines.append(
        f"{day_arrow} *{r['ticker']}* {r['shares']:,} shares @ ${r['price']:.2f} "
        f"({dsign(r['day_change'])}${abs(r['day_change']):,.0f} today) "
        f"| Value: ${r['value']:,.0f} "
        f"| Gain: {sign(r['gain_pct'])}{r['gain_pct']:.1f}%"
    )

# Benchmark: VOO day % for context
voo = next((r for r in results if r.get("ticker") == "VOO" and "error" not in r), None)
voo_pct = ((voo["price"] - voo["prev"]) / voo["prev"] * 100) if voo and voo["prev"] else None

lines += [
    "",
    "─────────────────────",
    f"💰 *Total Portfolio:* ${total_value:,.0f}",
    f"{'📈' if total_day_change >= 0 else '📉'} *Today:* {dsign(total_day_change)}${abs(total_day_change):,.0f} ({sign(day_pct)}{day_pct:.2f}%)",
    *(([f"📊 *S&P 500 (VOO):* {sign(voo_pct)}{voo_pct:.2f}% today"]) if voo_pct is not None else []),
    f"📊 *Total Gain:* {sign(total_gain)}${abs(total_gain):,.0f} ({sign(total_gain_pct)}{total_gain_pct:.1f}%)",
]
if split_warnings:
    lines += ["", "🔄 *Splits detected (auto-adjusted for today):*"]
    lines += [f"  • {w}" for w in split_warnings]
lines += [
    "",
    "_🔍 Source: Yahoo Finance_"
]

msg = "\n".join(lines)
print(msg)
