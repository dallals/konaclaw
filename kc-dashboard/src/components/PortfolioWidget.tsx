import type { PortfolioSnapshot } from "../api/portfolio";


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


/**
 * Summary card showing total value, day change, and top 3 movers.
 *
 * Controlled component — the parent (Portfolio view) owns the snapshot
 * state, sync/refresh actions, and refresh policy. This component just
 * renders. Decoupling state lets the same snapshot drive both this card
 * and the full HoldingsTable from a single source of truth.
 */
export function PortfolioWidget({ snapshot }: { snapshot: PortfolioSnapshot }) {
  const movers = topMovers(snapshot);
  const changeColor = snapshot.total_day_change >= 0 ? "text-green-600" : "text-red-600";

  return (
    <div className="portfolio-widget border border-line bg-panel p-6">
      <div className="text-4xl font-semibold">{fmtMoney(snapshot.total_value)}</div>
      <div className={`text-lg ${changeColor}`}>
        {fmtChange(snapshot.total_day_change)} ({fmtPct(snapshot.day_pct)})
      </div>

      <div className="mt-4" data-testid="top-movers">
        <div className="text-sm font-bold mb-1">Top movers</div>
        <ul className="text-sm">
          {movers.map((h) => (
            <li key={h.ticker} className="flex justify-between gap-4">
              <span className="font-mono">{h.ticker}</span>
              <span className={`font-mono ${h.day_change >= 0 ? "text-green-600" : "text-red-600"}`}>
                {fmtChange(h.day_change)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
