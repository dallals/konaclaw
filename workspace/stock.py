#!/usr/bin/env python3
import os
"""
import re
Stock lookup via Yahoo Finance.
Usage: python3 stock.py AAPL
       python3 stock.py TSLA AAPL MSFT
"""
import sys, os, re, json, urllib.request

def esc(t): return (t or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

raw = sys.argv[1:] if len(sys.argv) > 1 else ["SPY"]
tickers = [re.sub(r"[^A-Z0-9.^-]", "", t.upper()) for t in raw if t.strip()]
tickers = [t for t in tickers if t]

def fetch(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def arrow(v): return "▲" if v >= 0 else "▼"
def sign(v):  return "+" if v >= 0 else ""

lines = []
for ticker in tickers:
    try:
        data = fetch(ticker.upper())
        meta = data["chart"]["result"][0]["meta"]
        price     = meta.get("regularMarketPrice", 0)
        prev      = meta.get("chartPreviousClose", meta.get("previousClose", price))
        change    = price - prev
        pct       = (change / prev * 100) if prev else 0
        day_high  = meta.get("regularMarketDayHigh", 0)
        day_low   = meta.get("regularMarketDayLow", 0)
        week52_hi = meta.get("fiftyTwoWeekHigh", 0)
        week52_lo = meta.get("fiftyTwoWeekLow", 0)
        volume    = meta.get("regularMarketVolume", 0)
        name      = meta.get("shortName", ticker.upper())
        mkt_state = meta.get("marketState", "")

        emoji = "📈" if change >= 0 else "📉"
        state = "🟢 Market Open" if mkt_state == "REGULAR" else "🔴 After Hours" if mkt_state == "POST" else "⚫ Closed"

        lines.append(f"{emoji} *{ticker.upper()}* — {name}")
        lines.append(f"💵 *${price:.2f}*  {arrow(change)} {sign(change)}{change:.2f} ({sign(pct)}{pct:.2f}%)")
        lines.append(f"📊 Day: ${day_low:.2f} — ${day_high:.2f}")
        lines.append(f"📅 52w: ${week52_lo:.2f} — ${week52_hi:.2f}")
        lines.append(f"📦 Vol: {volume:,}  |  {state}")
        lines.append("")
    except Exception as e:
        lines.append(f"❌ {ticker.upper()}: {e}")
        lines.append("")

lines.append("_🔍 Source: Yahoo Finance_")
msg = "\n".join(lines)
print(msg)
