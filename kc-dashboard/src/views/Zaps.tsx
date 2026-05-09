import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { listZaps, refreshZaps, getConnector, patchConnector, type Zap } from "../api/connectors";
import SecretInput from "../components/connectors/SecretInput";

function fmtAgo(ts: number | null): string {
  if (!ts) return "never";
  const sec = Math.floor(Date.now() / 1000) - ts;
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export default function Zaps() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const zaps = useQuery({ queryKey: ["zaps"], queryFn: listZaps });
  const detail = useQuery({ queryKey: ["connectors", "zapier"], queryFn: () => getConnector("zapier") });

  const refresh = useMutation({
    mutationFn: refreshZaps,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["zaps"] }),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("zapier", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors", "zapier"] }),
  });

  const items: Zap[] = zaps.data?.zaps ?? [];
  const filtered = useMemo(() => {
    const needle = q.toLowerCase();
    if (!needle) return items;
    return items.filter(z =>
      z.tool.toLowerCase().includes(needle)
      || z.description.toLowerCase().includes(needle));
  }, [items, q]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-3 text-sm">
        <Link to="/connectors" className="text-muted hover:text-text">← Connectors</Link>
        <span className="text-line">/</span>
        <h2 className="text-lg font-semibold">⚡ Zapier</h2>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Zaps available</div>
          <div className="text-2xl font-semibold mt-1">{items.length}</div>
        </div>
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Last refresh</div>
          <div className="text-sm mt-2">just now</div>
        </div>
        <div className="p-3 rounded bg-panel border border-line">
          <div className="text-xs uppercase text-muted">Transport</div>
          <div className="text-sm mt-2">Streamable HTTP</div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          className="flex-1 px-3 py-2 rounded bg-bg border border-line text-sm"
          placeholder="Search zaps..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button
          className="px-3 py-2 rounded border border-line text-sm hover:border-accent"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >↻ Refresh</button>
        <a className="px-3 py-2 text-accent text-sm" href="https://zapier.com/app/dashboard" target="_blank" rel="noopener">
          Add zap on zapier.com ↗
        </a>
      </div>

      <div className="rounded border border-line overflow-hidden">
        <div className="grid grid-cols-[1fr_2fr_120px_80px] gap-2 px-3 py-2 bg-panel text-xs uppercase text-muted">
          <div>Tool</div><div>Description</div><div>Last used</div><div>Calls</div>
        </div>
        {filtered.length === 0 ? (
          <div className="px-3 py-6 text-center text-muted text-sm">No zaps {q ? "match this filter" : "configured"}.</div>
        ) : (
          filtered.map(z => (
            <div key={z.tool} className="grid grid-cols-[1fr_2fr_120px_80px] gap-2 px-3 py-2 border-t border-line text-sm">
              <code className="text-accent">{z.tool}</code>
              <span className="text-muted">{z.description}</span>
              <span className="text-muted">{fmtAgo(z.last_used_ts)}</span>
              <span className="text-muted">{z.call_count}</span>
            </div>
          ))
        )}
      </div>

      <div className="max-w-xl pt-4">
        <SecretInput
          label="Zapier API key"
          hasValue={detail.data?.has_token ?? false}
          tokenHint={detail.data?.token_hint}
          onSave={(value) => patch.mutateAsync({ api_key: value })}
        />
      </div>
    </div>
  );
}
