#!/usr/bin/env python3
"""Sync stock holdings from local rPlanner Postgres → workspace/holdings.json.

portfolio.py reads holdings.json when present; otherwise it falls back to
the HOLDINGS dict in the script. This lets KonaClaw's portfolio view stay
fresh whenever rPlanner is updated, without coupling KonaClaw to Postgres
at read time.

Connects via the `psql` system binary (no Python driver needed). Defaults
to local Postgres at localhost:5432, db=rplanner_dev. Scopes to a single
user by email (default: sammydallal@gmail.com).

Usage:
  python3 sync_holdings.py
  python3 sync_holdings.py --user-email someone@example.com
  python3 sync_holdings.py --db rplanner_dev --host localhost
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOLDINGS_FILE = HERE / "holdings.json"

# Aggregate from the `lots` table using `remaining_quantity` (what's left
# after sales) — NOT `investments.quantity`, which is the lifetime purchase
# total and over-reports any position the user has partially sold. Lots
# with remaining_quantity=0 are skipped so fully-sold positions don't
# appear as zero-share rows.
#
# Basis here is `remaining_quantity * purchase_price` per lot (basis of
# what's currently held), summed per ticker — same shape portfolio.py
# expects for its gain/loss math.
# Per-(ticker, account_type) aggregation. portfolio.py reduces by ticker
# for its price-fetch loop and computes per-account values using the live
# price. The dashboard renders per-account totals and a per-holding
# account-breakdown column.
_SQL = """
SELECT ticker,
       account_type,
       SUM(remaining_quantity)                AS shares,
       SUM(remaining_quantity*purchase_price) AS basis
FROM lots
WHERE user_id = (SELECT id FROM users WHERE email = '{email}')
  AND remaining_quantity > 0
GROUP BY ticker, account_type
ORDER BY ticker, account_type
"""


def _run_psql(host: str, db: str, sql: str, timeout: int = 15) -> str:
    cmd = [
        "psql", "-h", host, "-d", db,
        "-t", "-A", "-F", "|", "--no-align",
        "-c", sql,
    ]
    env = dict(os.environ)
    # Defer to ~/.pgpass / PGPASSWORD if Sammy needs a password later. Local
    # trust auth is the default for his Postgres so no password is required.
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql exit {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def _parse_rows(raw: str) -> tuple[dict[str, dict[str, float]],
                                   dict[str, dict[str, dict[str, float]]]]:
    """Returns (totals_per_ticker, per_ticker_per_account).

    totals_per_ticker mirrors the legacy shape portfolio.py already consumes
    for its per-ticker price lookup loop. per_ticker_per_account adds the
    account-type axis so the dashboard can render Taxable/Traditional/Roth
    breakdowns without re-querying Postgres.
    """
    totals: dict[str, dict[str, float]] = {}
    by_acct: dict[str, dict[str, dict[str, float]]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        ticker_raw, account_raw, shares_s, basis_s = parts
        try:
            shares = float(shares_s)
            basis = float(basis_s) if basis_s else 0.0
        except ValueError:
            continue
        if shares <= 0:
            continue
        ticker = ticker_raw.strip().upper()
        account = account_raw.strip() or "Unknown"
        # Per-account row
        by_acct.setdefault(ticker, {})[account] = {
            "shares": round(shares, 6),
            "basis": round(basis, 2),
        }
        # Roll up into per-ticker totals
        t = totals.setdefault(ticker, {"shares": 0.0, "basis": 0.0})
        t["shares"] = round(t["shares"] + shares, 6)
        t["basis"] = round(t["basis"] + basis, 2)
    return totals, by_acct


def sync(email: str, host: str, db: str) -> dict:
    raw = _run_psql(host, db, _SQL.format(email=email))
    totals, by_acct = _parse_rows(raw)
    payload = {
        "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": f"rplanner:{db}@{host}",
        "user_email": email,
        "holdings": totals,         # per-ticker totals (back-compat shape)
        "by_account": by_acct,      # per-ticker, per-account breakdown
    }
    tmp = HOLDINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.rename(HOLDINGS_FILE)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-email", default="sammydallal@gmail.com")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--db", default="rplanner_dev")
    ap.add_argument("--silent", action="store_true",
                    help="print JSON summary only")
    args = ap.parse_args()

    try:
        payload = sync(args.user_email, args.host, args.db)
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "psql timeout"}), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 3

    summary = {
        "synced_at": payload["synced_at"],
        "user_email": payload["user_email"],
        "tickers": len(payload["holdings"]),
        "total_basis": round(
            sum(h["basis"] for h in payload["holdings"].values()), 2
        ),
        "file": str(HOLDINGS_FILE),
    }
    if args.silent:
        print(json.dumps(summary))
    else:
        print(f"Synced {summary['tickers']} tickers for {summary['user_email']}")
        print(f"Total basis: ${summary['total_basis']:,.2f}")
        print(f"Wrote {summary['file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
