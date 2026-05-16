import { getBaseUrl } from "./client";

export interface PortfolioHolding {
  ticker: string;
  value: number;
  day_change: number;
  gain_pct: number;
}

export interface PortfolioSnapshot {
  total_value: number;
  total_gain: number;
  total_day_change: number;
  day_pct: number;
  holdings: PortfolioHolding[];
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
