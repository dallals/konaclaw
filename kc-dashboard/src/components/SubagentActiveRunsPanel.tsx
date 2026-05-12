import { useEffect, useState } from "react";
import { listActiveSubagents, stopSubagent, type ActiveSubagent } from "../api/subagents";

const POLL_INTERVAL_MS = 1500;

export function SubagentActiveRunsPanel() {
  const [rows, setRows] = useState<ActiveSubagent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const next = await listActiveSubagents();
        if (!cancelled) {
          setRows(next);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function handleStop(sid: string) {
    try {
      await stopSubagent(sid);
      setRows((prev) => prev.filter((r) => r.subagent_id !== sid));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section
      className="mt-8 border border-line bg-panel p-4"
      aria-label="Active subagent runs"
    >
      <header className="flex items-center justify-between mb-3">
        <h2 className="font-display font-semibold uppercase tracking-[0.18em] text-[12.5px] text-textStrong">
          Active Runs
        </h2>
        <span className="font-mono text-[10px] text-muted tracking-[0.1em]">
          polling · {(POLL_INTERVAL_MS / 1000).toFixed(1)}s
        </span>
      </header>
      {error && (
        <div role="alert" className="text-warn text-[11px] font-mono mb-2">
          {error}
        </div>
      )}
      {rows.length === 0 ? (
        <p className="text-muted text-[12px] font-mono">No subagents running.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r) => (
            <li
              key={r.subagent_id}
              className="flex items-center justify-between gap-3 px-3 py-2 border border-line bg-bgDeep font-mono text-[11px]"
              data-testid={`active-run-${r.subagent_id}`}
            >
              <span className="flex items-center gap-3 text-text">
                <code className="text-accent">{r.subagent_id}</code>
                <span>{r.template}</span>
                {r.label && <span className="text-muted">· {r.label}</span>}
                <span className="text-muted">· {r.tool_calls_used} tools</span>
              </span>
              <button
                onClick={() => handleStop(r.subagent_id)}
                className="px-2.5 py-1 border border-warn text-warn text-[10px] uppercase tracking-[0.1em] hover:bg-warn hover:text-bgDeep"
              >
                ⏹ Stop
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
