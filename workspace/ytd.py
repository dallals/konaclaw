#!/usr/bin/env python3
"""
ytd.py — Year-to-date portfolio performance for Sammy's holdings.
Usage: python3 ytd.py
       python3 ytd.py --silent   (JSON output only)
"""
import sys, json, urllib.request, datetime, os

SILENT    = "--silent" in sys.argv

HOLDINGS = {
    "AAPL":  {"basis": 645796,  "shares": 4957},
    "NVDA":  {"basis":  40037,  "shares": 5379},
    "VOO":   {"basis": 485552,  "shares": 1097},
    "VBIL":  {"basis": 201718,  "shares": 2670},
    "GOOGL": {"basis": 128138,  "shares": 625},
    "TSLA":  {"basis": 118840,  "shares": 458},
    "AMZN":  {"basis": 159452,  "shares": 695},
    "VFIAX": {"basis":  35666,  "shares": 170},
    "VUG":   {"basis":  59071,  "shares": 213},
    "MSFT":  {"basis":  84965,  "shares": 230},
    "QQQ":   {"basis":  65438,  "shares": 115},
    "AMD":   {"basis":  46554,  "shares": 300},
    "VTI":   {"basis":  22207,  "shares": 149},
    "VYM":   {"basis":  37971,  "shares": 257},
    "IJH":   {"basis":  19565,  "shares": 492},
    "OWL":   {"basis":  12637,  "shares": 1199},
}
# Jan 1 of current year for YTD calculation
today = datetime.date.today()

def fetch(ticker):
    # range=ytd gives price from Jan 1 to today
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=ytd"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

results = []
total_now = 0
total_jan1 = 0
total_basis = 0
errors = []

for ticker, info in HOLDINGS.items():
    try:
        data = fetch(ticker)
        chart = data["chart"]["result"][0]
        meta = chart["meta"]
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        timestamps = chart.get("timestamp", [])

        price_now = meta.get("regularMarketPrice", 0)
        # First close of the year (Jan 2 trading day, since Jan 1 is closed)
        price_jan1 = closes[0] if closes and closes[0] is not None else price_now if closes else price_now

        shares = info["shares"]
        basis  = info["basis"]

        val_now  = price_now  * shares
        val_jan1 = price_jan1 * shares
        ytd_chg  = val_now - val_jan1
        ytd_pct  = (ytd_chg / val_jan1 * 100) if val_jan1 else 0

        total_now   += val_now
        total_jan1  += val_jan1
        total_basis += basis

        results.append({
            "ticker":    ticker,
            "price_now": price_now,
            "price_jan1": price_jan1,
            "shares":    shares,
            "val_now":   val_now,
            "val_jan1":  val_jan1,
            "ytd_chg":   ytd_chg,
            "ytd_pct":   ytd_pct,
        })
    except Exception as e:
        errors.append(f"{ticker}: {e}")
        results.append({"ticker": ticker, "error": str(e)})

total_ytd_chg = total_now - total_jan1
total_ytd_pct = (total_ytd_chg / total_jan1 * 100) if total_jan1 else 0
total_gain    = total_now - total_basis
total_gain_pct = (total_gain / total_basis * 100) if total_basis else 0

if SILENT:
    print(json.dumps({
        "holdings": results,
        "total_now": total_now,
        "total_jan1": total_jan1,
        "total_ytd_chg": total_ytd_chg,
        "total_ytd_pct": total_ytd_pct,
        "year": today.year,
    }))
    sys.exit(0)

sign  = lambda v: "+" if v >= 0 else ""
arrow = lambda v: "📈" if v >= 0 else "📉"

lines = [
    f"📊 <b>YTD Performance — {today.year}</b>",
    f"<i>Jan 1 → {today.strftime('%b %d, %Y')}</i>",
    "",
]

for r in results:
    if "error" in r:
        lines.append(f"❌ {r['ticker']}: {r['error']}")
        continue
    em = arrow(r["ytd_chg"])
    lines.append(
        f"{em} <b>{r['ticker']}</b>  "
        f"{sign(r['ytd_pct'])}{r['ytd_pct']:.1f}%  "
        f"({sign(r['ytd_chg'])}${abs(r['ytd_chg']):,.0f})"
        f"  |  Now: ${r['val_now']:,.0f}"
    )

lines += [
    "",
    "─────────────────────",
    f"{'📈' if total_ytd_chg >= 0 else '📉'} <b>Portfolio YTD:</b> {sign(total_ytd_pct)}{total_ytd_pct:.1f}%  ({sign(total_ytd_chg)}${abs(total_ytd_chg):,.0f})",
    f"💰 <b>Current Value:</b> ${total_now:,.0f}",
    f"📅 <b>Jan 1 Value:</b>   ${total_jan1:,.0f}",
    f"📊 <b>Total Gain (vs basis):</b> {sign(total_gain_pct)}{total_gain_pct:.1f}%  (${total_gain:,.0f})",
    "",
    "<i>🔍 Source: Yahoo Finance (YTD range)</i>"
]

msg = "\n".join(lines)
print(msg)
