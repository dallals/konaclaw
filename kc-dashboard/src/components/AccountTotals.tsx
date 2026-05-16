import type { AccountTotal } from "../api/portfolio";

function fmtMoney(n: number): string {
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

function fmtPct(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(1)}%`;
}

// Stable display order, regardless of how the backend serialized the dict.
const ACCOUNT_ORDER = ["Taxable", "Traditional", "Roth"] as const;

function sortedAccounts(t: { [k: string]: AccountTotal }): [string, AccountTotal][] {
  const known = ACCOUNT_ORDER.filter((k) => k in t).map((k): [string, AccountTotal] => [k, t[k]]);
  const extras = Object.entries(t)
    .filter(([k]) => !(ACCOUNT_ORDER as readonly string[]).includes(k))
    .sort(([, a], [, b]) => b.value - a.value);
  return [...known, ...extras];
}

export function AccountTotals({ totals }: { totals: { [k: string]: AccountTotal } }) {
  const rows = sortedAccounts(totals);
  if (rows.length === 0) return null;

  const grand = rows.reduce((s, [, v]) => s + v.value, 0);

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3" data-testid="account-totals">
      {rows.map(([name, v]) => {
        const pct = grand > 0 ? (v.value / grand) * 100 : 0;
        const gainPct = v.basis > 0 ? (v.gain / v.basis) * 100 : 0;
        return (
          <div key={name} className="border border-line bg-panel p-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted2">
              {name}
            </div>
            <div className="text-2xl font-semibold mt-1">{fmtMoney(v.value)}</div>
            <div className="text-xs text-muted mt-1 flex items-center justify-between gap-2">
              <span>{pct.toFixed(1)}% of total</span>
              <span className={v.gain >= 0 ? "text-green-600" : "text-red-600"}>
                {fmtPct(gainPct)} gain
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
