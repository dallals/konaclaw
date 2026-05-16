import { useState, useMemo } from "react";
import type { PortfolioHolding } from "../api/portfolio";

type SortKey = "ticker" | "shares" | "price" | "value" | "day_change" | "gain" | "gain_pct";
type SortDir = "asc" | "desc";

function fmtMoney(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

function fmtSignedMoney(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}$${Math.round(Math.abs(n)).toLocaleString("en-US")}`;
}

function fmtPct(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}

function fmtShares(n: number | undefined): string {
  if (n == null) return "—";
  // Show fractional shares only when present.
  return n % 1 === 0
    ? n.toLocaleString("en-US")
    : n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function fmtPrice(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toFixed(2)}`;
}

function changeClass(n: number | undefined): string {
  if (n == null) return "text-muted";
  return n >= 0 ? "text-green-600" : "text-red-600";
}

// Compact display for the per-row Accounts column.
//   { Taxable: {shares: 216}, Traditional: {shares: 880} }
//     → "Tax 216 · Trad 880"
const _ABBR: Record<string, string> = {
  Taxable: "Tax",
  Traditional: "Trad",
  Roth: "Roth",
};

function fmtAccounts(by_account: PortfolioHolding["by_account"]): string {
  if (!by_account || Object.keys(by_account).length === 0) return "—";
  const parts: string[] = [];
  // Stable display order; unknown accounts sort by share count desc.
  const order = ["Taxable", "Traditional", "Roth"];
  const known = order.filter((k) => k in by_account);
  const extras = Object.keys(by_account)
    .filter((k) => !order.includes(k))
    .sort((a, b) => by_account[b].shares - by_account[a].shares);
  for (const k of [...known, ...extras]) {
    const leg = by_account[k];
    const sh = leg.shares % 1 === 0
      ? leg.shares.toLocaleString("en-US")
      : leg.shares.toLocaleString("en-US", { maximumFractionDigits: 2 });
    parts.push(`${_ABBR[k] ?? k} ${sh}`);
  }
  return parts.join(" · ");
}

export function HoldingsTable({ holdings }: { holdings: PortfolioHolding[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("value");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const rows = [...holdings];
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      const an = (av as number) ?? -Infinity;
      const bn = (bv as number) ?? -Infinity;
      return sortDir === "asc" ? an - bn : bn - an;
    });
    return rows;
  }, [holdings, sortKey, sortDir]);

  const toggle = (k: SortKey) => {
    if (k === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(k);
      setSortDir(k === "ticker" ? "asc" : "desc");
    }
  };

  const Th = ({ k, label, align = "left" }: { k: SortKey; label: string; align?: "left" | "right" }) => {
    const active = sortKey === k;
    return (
      <th
        scope="col"
        onClick={() => toggle(k)}
        className={`px-3 py-2 font-mono text-[10px] uppercase tracking-[0.12em] cursor-pointer select-none border-b border-line ${
          active ? "text-textStrong" : "text-muted2 hover:text-text"
        } ${align === "right" ? "text-right" : "text-left"}`}
      >
        {label}{active && (sortDir === "asc" ? " ▲" : " ▼")}
      </th>
    );
  };

  if (holdings.length === 0) {
    return <div className="text-sm text-muted italic px-3 py-4">No holdings.</div>;
  }

  return (
    <div className="overflow-x-auto border border-line">
      <table className="w-full text-sm">
        <thead className="bg-panel">
          <tr>
            <Th k="ticker"     label="Ticker" />
            <Th k="shares"     label="Shares"      align="right" />
            <Th k="price"      label="Price"       align="right" />
            <Th k="value"      label="Value"       align="right" />
            <Th k="day_change" label="Day Δ"       align="right" />
            <Th k="gain"       label="Gain ($)"    align="right" />
            <Th k="gain_pct"   label="Gain (%)"    align="right" />
            <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted2 border-b border-line text-left">
              Accounts
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((h) => (
            <tr key={h.ticker} className="border-b border-line/60 hover:bg-panel">
              <td className="px-3 py-2 font-mono font-semibold">{h.ticker}</td>
              <td className="px-3 py-2 text-right font-mono">{fmtShares(h.shares)}</td>
              <td className="px-3 py-2 text-right font-mono">{fmtPrice(h.price)}</td>
              <td className="px-3 py-2 text-right font-mono">{fmtMoney(h.value)}</td>
              <td className={`px-3 py-2 text-right font-mono ${changeClass(h.day_change)}`}>
                {fmtSignedMoney(h.day_change)}
              </td>
              <td className={`px-3 py-2 text-right font-mono ${changeClass(h.gain)}`}>
                {fmtSignedMoney(h.gain)}
              </td>
              <td className={`px-3 py-2 text-right font-mono ${changeClass(h.gain_pct)}`}>
                {fmtPct(h.gain_pct)}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-muted">
                {fmtAccounts(h.by_account)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
