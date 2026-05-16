import { useEffect, useState, useCallback } from "react";
import {
  getSnapshot, syncHoldings,
  type PortfolioSnapshot, type SnapshotResponse, type SyncSummary,
} from "../api/portfolio";


function fmtMoney(n: number): string {
  return `$${Math.round(n).toLocaleString("en-US")}`;
}


function fmtChange(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${fmtMoney(Math.abs(n))}`;
}


function fmtPct(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}


function topMovers(p: PortfolioSnapshot): PortfolioSnapshot["holdings"] {
  return [...p.holdings]
    .sort((a, b) => Math.abs(b.day_change) - Math.abs(a.day_change))
    .slice(0, 3);
}


export function PortfolioWidget() {
  const [snap, setSnap] = useState<SnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const r = await getSnapshot(refresh);
      setSnap(r);
    } catch (e) {
      setSnap({
        cached_at: new Date().toISOString(),
        payload: null,
        stale: true,
        error: (e as Error)?.message ?? "fetch failed",
        last_good: null,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  const sync = useCallback(async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const s: SyncSummary = await syncHoldings();
      setSyncMsg(`Synced ${s.tickers} tickers · ${new Date(s.synced_at).toLocaleTimeString()}`);
      // Re-fetch snapshot off the freshly-synced holdings.json.
      await load(true);
    } catch (e) {
      setSyncMsg(`Sync failed: ${(e as Error)?.message ?? "unknown error"}`);
    } finally {
      setSyncing(false);
    }
  }, [load]);

  useEffect(() => {
    load(false);
    const t = setInterval(() => load(false), 5 * 60 * 1000);
    return () => clearInterval(t);
  }, [load]);

  if (loading && !snap) {
    return <div className="portfolio-widget portfolio-widget--loading">Loading portfolio…</div>;
  }

  const payload: PortfolioSnapshot | null = snap?.payload ?? snap?.last_good ?? null;
  const isStale = Boolean(snap?.stale && payload);
  const errorMsg = snap?.error;

  if (!payload) {
    return (
      <div className="portfolio-widget portfolio-widget--error">
        <div>Could not load portfolio.</div>
        {errorMsg && <div className="text-xs text-muted">{errorMsg}</div>}
        <button onClick={() => load(true)} aria-label="refresh">Retry</button>
      </div>
    );
  }

  const movers = topMovers(payload);
  const changeColor = payload.total_day_change >= 0 ? "text-green-600" : "text-red-600";

  return (
    <div
      className={`portfolio-widget ${isStale ? "opacity-60" : ""}`}
      data-testid={isStale ? "portfolio-stale" : "portfolio-fresh"}
    >
      <div className="text-3xl font-semibold">{fmtMoney(payload.total_value)}</div>
      <div className={`text-lg ${changeColor}`}>
        {fmtChange(payload.total_day_change)} ({fmtPct(payload.day_pct)})
      </div>

      <div className="mt-4" data-testid="top-movers">
        <div className="text-sm font-bold mb-1">Top movers</div>
        <ul className="text-sm">
          {movers.map((h) => (
            <li key={h.ticker} className="flex justify-between gap-4">
              <span>{h.ticker}</span>
              <span className={h.day_change >= 0 ? "text-green-600" : "text-red-600"}>
                {fmtChange(h.day_change)}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-2 text-xs text-muted flex items-center gap-3 flex-wrap">
        <span>Updated: {new Date(snap!.cached_at).toLocaleTimeString()}</span>
        <button
          onClick={() => load(true)}
          aria-label="refresh"
          className="hover:text-textStrong"
        >
          ↻ Refresh
        </button>
        <button
          onClick={sync}
          disabled={syncing}
          aria-label="sync holdings from rplanner"
          className="hover:text-textStrong disabled:opacity-50"
          title="Pull latest holdings from local rPlanner Postgres"
        >
          {syncing ? "⟳ Syncing…" : "⇣ Sync from rPlanner"}
        </button>
        {payload.holdings_source && payload.holdings_source !== "fallback" && (
          <span className="text-muted2" title={`Holdings synced ${payload.holdings_source}`}>
            · holdings: synced
          </span>
        )}
        {payload.holdings_source === "fallback" && (
          <span className="text-muted2" title="Using hardcoded fallback holdings — click Sync to pull from rPlanner">
            · holdings: fallback
          </span>
        )}
      </div>

      {syncMsg && (
        <div className="mt-1 text-xs text-muted" role="status">{syncMsg}</div>
      )}

      {errorMsg && (
        <div className="mt-2 text-xs text-red-600" role="alert">
          {errorMsg}
        </div>
      )}
    </div>
  );
}
