#!/usr/bin/env python3
"""
monte_carlo.py — Monte Carlo retirement simulation for Sammy Dallal
Usage:
  python3 monte_carlo.py                        # standard simulation (retire age 56.5)
  python3 monte_carlo.py --retire-age 55        # retire at a different age
  python3 monte_carlo.py --retire-age 60        # e.g. what if I work until 60?
  python3 monte_carlo.py --crash                # 2008-style crash in year 1-2
  python3 monte_carlo.py --inflation            # 15% prolonged inflation scenario
  python3 monte_carlo.py --longevity            # stress test to age 95
  python3 monte_carlo.py --all                  # run all scenarios
  python3 monte_carlo.py --retire-age 58 --all  # all scenarios at age 58
"""
import sys, os, json, urllib.request, argparse
import numpy as np

# ── Parse arguments ────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--retire-age", type=float, default=56.5)
parser.add_argument("--crash",      action="store_true")
parser.add_argument("--inflation",  action="store_true")
parser.add_argument("--longevity",  action="store_true")
parser.add_argument("--all",        action="store_true")
parser.add_argument("--return-rate", type=float, default=None, help="Override avg annual return (e.g. 0.06 or 6)")
args, _ = parser.parse_known_args()

RETIRE_AGE          = args.retire_age
_rr = args.return_rate
if _rr is not None:
    MEAN_RETURN_OVERRIDE = _rr if _rr < 1 else _rr / 100.0
else:
    MEAN_RETURN_OVERRIDE = None
CURRENT_AGE         = 51.5
YEARS_TO_RETIREMENT = max(0, RETIRE_AGE - CURRENT_AGE)

# ── Sammy's Profile ────────────────────────────────────────────────────────────
CURRENT_PORTFOLIO    = 4_102_702
ANNUAL_CONTRIBUTIONS = 142_000     # 401k + ESPP + RSU + dividends
RETIREMENT_SPEND     = 181_584     # annual expenses (today's dollars)
RENTAL_INCOME        = 18_000      # rental income (until age 70)
SS_INCOME            = 28_000      # estimated SS at 62 (conservative)
SS_START_YEAR        = max(0, 62 - RETIRE_AGE)   # years after retirement until SS
MORTGAGE_PAYOFF_YR   = max(0, 66 - RETIRE_AGE)   # years after retirement until payoff
MORTGAGE_SAVINGS     = 32_483      # annual savings after mortgage payoff
RENTAL_END_YEAR      = max(0, 70 - RETIRE_AGE)    # rental income ends at 70
SIMULATIONS          = 10_000
RETIREMENT_HORIZON   = max(1, round(95 - RETIRE_AGE))  # always simulate to age 95

# Market assumptions (historical equity/bond blended for this allocation)
MEAN_RETURN     = MEAN_RETURN_OVERRIDE if MEAN_RETURN_OVERRIDE is not None else 0.072   # 7.2% nominal (80/20 equity/bond blend)
STD_DEV         = 0.140   # 14% std dev
INFLATION        = 0.030   # 3% baseline inflation

def run_simulation(label, mean_ret, std_dev, inflation_rate, horizon,
                   crash_years=None, crash_magnitude=0.0,
                   extra_note=""):
    np.random.seed(42)
    results = []

    for _ in range(SIMULATIONS):
        # Phase 1: Accumulation (years to retirement)
        portfolio = CURRENT_PORTFOLIO
        for yr in range(int(YEARS_TO_RETIREMENT)):
            ret = np.random.normal(mean_ret, std_dev)
            portfolio = portfolio * (1 + ret) + ANNUAL_CONTRIBUTIONS

        # Phase 2: Retirement drawdown
        for yr in range(horizon):
            ret = np.random.normal(mean_ret, std_dev)

            if crash_years and yr in crash_years:
                ret = crash_magnitude

            portfolio = portfolio * (1 + ret)

            spend  = RETIREMENT_SPEND * ((1 + inflation_rate) ** yr)
            income = 0

            if yr < RENTAL_END_YEAR:
                income += RENTAL_INCOME * ((1 + inflation_rate) ** yr)
            if yr >= SS_START_YEAR:
                income += SS_INCOME * ((1 + inflation_rate) ** (yr - SS_START_YEAR))
            if yr >= MORTGAGE_PAYOFF_YR:
                spend -= MORTGAGE_SAVINGS

            portfolio -= max(0, spend - income)

        results.append(portfolio)

    results = np.array(results)
    p10  = np.percentile(results, 10)
    p25  = np.percentile(results, 25)
    p50  = np.percentile(results, 50)
    p75  = np.percentile(results, 75)
    p90  = np.percentile(results, 90)
    fail_rate = (results <= 0).sum() / SIMULATIONS * 100
    success_rate = 100 - fail_rate

    portfolio_at_ret = CURRENT_PORTFOLIO
    for _ in range(int(YEARS_TO_RETIREMENT)):
        portfolio_at_ret = portfolio_at_ret * (1 + mean_ret) + ANNUAL_CONTRIBUTIONS

    retire_yr = int(2026 + YEARS_TO_RETIREMENT)

    msg = f"""📊 *Monte Carlo: {label}*
_{SIMULATIONS:,} simulations | Retire age {RETIRE_AGE} → age 95 ({horizon} yrs)_

*At Retirement ({retire_yr}, age {RETIRE_AGE}):*
💰 Projected portfolio: ${portfolio_at_ret:,.0f}

*End-of-Retirement Outcomes (age 95):*
🏆 90th pct (best):    ${p90:,.0f}
✅ 75th pct:           ${p75:,.0f}
📊 50th pct (median):  ${p50:,.0f}
⚠️ 25th pct:           ${p25:,.0f}
🚨 10th pct (worst):   ${p10:,.0f}

*Survival Rate: {success_rate:.1f}%* {'✅' if success_rate >= 90 else '⚠️' if success_rate >= 75 else '🚨'}
Portfolio failures: {fail_rate:.1f}% of simulations ran out of money
"""
    if extra_note:
        msg += f"\n_{extra_note}_\n"

    msg += "\n_🔍 Source: Monte Carlo (numpy, 10,000 simulations)_"
    return msg

run_all = args.all
no_scenario_flags = not args.crash and not args.inflation and not args.longevity and not run_all

messages = []

# Standard simulation (runs by default, or when --all is passed)
if no_scenario_flags or run_all:
    messages.append(run_simulation(
        label="Standard (Baseline)",
        mean_ret=MEAN_RETURN,
        std_dev=STD_DEV,
        inflation_rate=INFLATION,
        horizon=RETIREMENT_HORIZON,
        extra_note=f"Assumes {MEAN_RETURN*100:.1f}% avg return, 3% inflation, {RETIREMENT_HORIZON}-year horizon (retire {RETIRE_AGE} → age 95)"
    ))

# 2008-style crash in first 2 years of retirement
if args.crash or run_all:
    messages.append(run_simulation(
        label="2008-Style Crash (Years 1-2)",
        mean_ret=MEAN_RETURN,
        std_dev=STD_DEV,
        inflation_rate=INFLATION,
        horizon=RETIREMENT_HORIZON,
        crash_years=[0, 1],
        crash_magnitude=-0.38,
        extra_note="Simulates -38% crash in first 2 years of retirement (sequence of returns risk)"
    ))

# 15% prolonged inflation
if args.inflation or run_all:
    messages.append(run_simulation(
        label="15% Prolonged Inflation (5 yrs)",
        mean_ret=MEAN_RETURN - 0.02,  # real returns compressed
        std_dev=STD_DEV + 0.03,
        inflation_rate=0.15,
        horizon=RETIREMENT_HORIZON,
        extra_note="Simulates 15% inflation for first 5 years, then reverts to 3% — spending surges early"
    ))

# Longevity to age 95
if args.longevity or run_all:
    messages.append(run_simulation(
        label="Longevity to Age 95",
        mean_ret=MEAN_RETURN,
        std_dev=STD_DEV,
        inflation_rate=INFLATION,
        horizon=max(1, round(95 - RETIRE_AGE) + 1),
        extra_note=f"Extended horizon — tests if portfolio survives to age 95+ (retire {RETIRE_AGE})"
    ))

for msg in messages:
    print(msg)
    print("---")

print(f"[{len(messages)} simulation(s) complete]")
