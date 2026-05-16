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

# Aggregate lots per ticker so portfolio.py sees one row per symbol (matches
# the legacy HOLDINGS dict shape). Only types that move on equity markets
# go through portfolio.py — Bond is excluded since price feeds (Yahoo) don't
# quote fixed-income reliably.
_SQL = """
SELECT ticker,
       SUM(quantity)               AS shares,
       SUM(quantity*purchase_price) AS basis
FROM investments
WHERE user_id = (SELECT id FROM users WHERE email = '{email}')
  AND type IN ('Stock', 'ETF', 'Mutual Fund', 'Bond')
  AND quantity > 0
GROUP BY ticker
ORDER BY basis DESC NULLS LAST
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


def _parse_rows(raw: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        ticker, shares_s, basis_s = parts
        try:
            shares = float(shares_s)
            basis = float(basis_s) if basis_s else 0.0
        except ValueError:
            continue
        if shares <= 0:
            continue
        out[ticker.strip().upper()] = {
            "shares": round(shares, 6),
            "basis": round(basis, 2),
        }
    return out


def sync(email: str, host: str, db: str) -> dict:
    raw = _run_psql(host, db, _SQL.format(email=email))
    holdings = _parse_rows(raw)
    payload = {
        "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": f"rplanner:{db}@{host}",
        "user_email": email,
        "holdings": holdings,
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
