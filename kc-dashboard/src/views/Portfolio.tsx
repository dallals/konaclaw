import { useCallback, useEffect, useState } from "react";
import {
  getSnapshot, syncHoldings,
  type PortfolioSnapshot, type SnapshotResponse, type SyncSummary,
} from "../api/portfolio";
import { PortfolioWidget } from "../components/PortfolioWidget";
import { HoldingsTable } from "../components/HoldingsTable";

export default function Portfolio() {
  const [snap, setSnap] = useState<SnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
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
      if (refresh) setRefreshing(false); else setLoading(false);
    }
  }, []);

  const sync = useCallback(async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const s: SyncSummary = await syncHoldings();
      setSyncMsg(`Synced ${s.tickers} tickers · ${new Date(s.synced_at).toLocaleTimeString()}`);
      await load(true);
    } catch (e) {
      setSyncMsg(`Sync failed: ${(e as Error)?.message ?? "unknown error"}`);
    } finally {
      setSyncing(false);
    }
  }, [load]);

  // Load ONCE on mount. No auto-refresh — Sammy uses the Sync / Refresh
  // buttons when he wants fresh data (avoids intraday-noise refreshes
  // when the supervisor's cache TTL expires).
  useEffect(() => { void load(false); }, [load]);

  const payload: PortfolioSnapshot | null = snap?.payload ?? snap?.last_good ?? null;
  const errorMsg = snap?.error;

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header: title + prominent action buttons */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Portfolio</h1>
          {snap && (
            <div className="text-xs text-muted mt-1">
              Snapshot: {new Date(snap.cached_at).toLocaleString()}
              {payload?.holdings_source && payload.holdings_source !== "fallback" && (
                <> · holdings synced</>
              )}
              {payload?.holdings_source === "fallback" && (
                <> · using fallback holdings — click Sync to pull from rPlanner</>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => load(true)}
            disabled={refreshing || syncing || loading}
            aria-label="refresh prices"
            className="px-4 py-2 border border-line bg-panel text-text hover:border-accent hover:text-textStrong font-mono text-[12px] uppercase tracking-[0.15em] font-medium disabled:opacity-50 inline-flex items-center gap-2"
          >
            <span className="text-base leading-none">↻</span>
            <span>{refreshing ? "Refreshing…" : "Refresh prices"}</span>
          </button>
          <button
            type="button"
            onClick={sync}
            disabled={syncing || refreshing || loading}
            aria-label="sync holdings from rplanner"
            title="Pull latest holdings from local rPlanner Postgres, then refresh prices"
            className="px-4 py-2 bg-accent text-bgDeep hover:bg-accentBright border border-accent font-mono text-[12px] uppercase tracking-[0.15em] font-bold disabled:opacity-50 inline-flex items-center gap-2"
          >
            <span className="text-base leading-none">⇣</span>
            <span>{syncing ? "Syncing…" : "Sync from rPlanner"}</span>
          </button>
        </div>
      </div>

      {syncMsg && (
        <div className="mb-4 px-3 py-2 border border-line bg-panel text-sm text-muted" role="status">
          {syncMsg}
        </div>
      )}

      {errorMsg && (
        <div className="mb-4 px-3 py-2 border border-red-600/40 bg-red-600/5 text-sm text-red-600" role="alert">
          {errorMsg}
        </div>
      )}

      {/* Summary widget */}
      {loading && !snap ? (
        <div className="text-muted">Loading portfolio…</div>
      ) : payload ? (
        <>
          <PortfolioWidget snapshot={payload} />
          <div className="mt-8">
            <h2 className="text-lg font-semibold mb-3">Holdings</h2>
            <HoldingsTable holdings={payload.holdings} />
          </div>
        </>
      ) : (
        <div className="text-muted">No portfolio data.</div>
      )}
    </div>
  );
}
