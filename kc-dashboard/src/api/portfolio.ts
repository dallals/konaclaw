import { getBaseUrl } from "./client";

export interface AccountLeg {
  shares: number;
  basis: number;
  value: number;
  gain: number;
}

export interface PortfolioHolding {
  ticker: string;
  /** Last trade price (today). */
  price?: number;
  /** Prior session close, for day-change math. */
  prev?: number;
  shares?: number;
  value: number;
  day_change: number;
  /** Absolute lifetime gain ($). */
  gain?: number;
  gain_pct: number;
  /** Per-account-type breakdown for this ticker (Taxable / Traditional / Roth).
   *  Empty when running off fallback HOLDINGS (pre-sync). */
  by_account?: { [accountType: string]: AccountLeg };
  /** Set when Yahoo fetch failed for this ticker. */
  error?: string;
}

export interface AccountTotal {
  value: number;
  basis: number;
  gain: number;
}

export interface PortfolioSnapshot {
  total_value: number;
  total_gain: number;
  total_day_change: number;
  day_pct: number;
  holdings: PortfolioHolding[];
  /** ISO timestamp when holdings.json was last synced from rPlanner, or
   *  "fallback" when running off the hardcoded HOLDINGS dict. */
  holdings_source?: string;
  /** Per-account-type totals (Taxable / Traditional / Roth). Empty before
   *  the first sync. */
  account_totals?: { [accountType: string]: AccountTotal };
}

export interface SyncSummary {
  synced_at: string;       // ISO
  user_email: string;
  tickers: number;
  total_basis: number;
  file: string;
}

export interface SnapshotResponse {
  cached_at: string;            // ISO timestamp
  payload: PortfolioSnapshot | null;
  stale: boolean;
  error?: string;
  last_good?: PortfolioSnapshot | null;
}

export async function getSnapshot(refresh = false): Promise<SnapshotResponse> {
  const url = `${getBaseUrl()}/portfolio/snapshot${refresh ? "?refresh=true" : ""}`;
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`portfolio snapshot failed (${r.status}): ${await r.text()}`);
  }
  return r.json();
}

export async function syncHoldings(): Promise<SyncSummary> {
  const r = await fetch(`${getBaseUrl()}/portfolio/sync`, { method: "POST" });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`sync failed (${r.status}): ${body}`);
  }
  return r.json();
}
