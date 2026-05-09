import { useState } from "react";

type Props = {
  label: string;
  values: string[];
  onChange: (next: string[]) => Promise<unknown> | void;
};

export default function AllowlistEditor({ label, values, onChange }: Props) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const commit = async (next: string[]) => {
    setBusy(true);
    try { await onChange(next); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-2">
      <label className="text-xs uppercase text-muted tracking-wide">{label}</label>
      <div className="flex flex-wrap gap-1.5">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-panel border border-line text-xs">
            {v}
            <button
              className="text-bad hover:opacity-80"
              onClick={() => commit(values.filter((x) => x !== v))}
              disabled={busy}
            >×</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="flex-1 px-3 py-1.5 rounded bg-bg border border-line text-sm"
          placeholder="add entry..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft) {
              commit([...values, draft]); setDraft("");
            }
          }}
        />
        <button
          className="px-3 py-1.5 rounded bg-accent text-bg text-sm"
          disabled={!draft || busy}
          onClick={() => { commit([...values, draft]); setDraft(""); }}
        >Add</button>
      </div>
    </div>
  );
}
