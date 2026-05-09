import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAudit, undoAudit, type DecisionFilter, type AuditEntry } from "../api/audit";

export default function Audit() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filter = (params.get("decision") as DecisionFilter | null) ?? "all";
  const setFilter = (next: DecisionFilter) => {
    if (next === "all") params.delete("decision");
    else params.set("decision", next);
    setParams(params, { replace: true });
  };

  const q = useQuery({
    queryKey: ["audit", filter],
    queryFn: () => listAudit(undefined, 200, filter),
    refetchInterval: 3000,
  });

  const [rowError, setRowError] = useState<{ id: number; msg: string } | null>(null);

  const undo = useMutation({
    mutationFn: undoAudit,
    onSuccess: () => {
      setRowError(null);
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (e: Error, id) => {
      const m = e.message.match(/→ \d+: (.*)$/s);
      let detail = e.message;
      try { if (m) detail = JSON.parse(m[1]).detail ?? detail; } catch { /* keep raw */ }
      setRowError({ id, msg: detail });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const reasonOf = (entry: AuditEntry): string | null => {
    if (entry.decision !== "denied" || !entry.result) return null;
    try { return (JSON.parse(entry.result) as { reason?: string }).reason ?? null; }
    catch { return null; }
  };

  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Audit log</h2>

      <div className="flex items-center gap-2 mb-3">
        {(["all", "allowed", "denied"] as DecisionFilter[]).map(opt => (
          <button
            key={opt}
            onClick={() => setFilter(opt)}
            className={"px-3 py-1 rounded text-xs border "
              + (filter === opt ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{opt[0].toUpperCase() + opt.slice(1)}</button>
        ))}
      </div>

      <table className="w-full text-xs font-mono">
        <thead className="text-muted text-[10px] uppercase">
          <tr>
            <th className="text-left py-2">Time</th>
            <th className="text-left">Agent</th>
            <th className="text-left">Tool</th>
            <th className="text-left">Decision</th>
            <th className="text-left">Result</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {q.data?.entries.map((e) => {
            const denied = e.decision === "denied";
            const reason = reasonOf(e);
            return (
              <tr key={e.id} className="border-t border-line">
                <td className="py-2 text-muted">{new Date(e.ts * 1000).toLocaleTimeString()}</td>
                <td className="text-good">{e.agent}</td>
                <td className="text-cyan-300">{e.tool}</td>
                <td>
                  <span
                    className={"px-2 py-0.5 rounded inline-block "
                      + (denied
                        ? "bg-bad/20 text-bad border border-bad/40"
                        : "bg-good/20 text-good border border-good/40")}
                    title={denied ? (reason ?? "no reason recorded") : undefined}
                  >{e.decision}</span>
                </td>
                <td className="text-text">{e.result ?? "—"}</td>
                <td>
                  {denied ? (
                    "—"
                  ) : e.undone ? (
                    <span className="text-muted italic">✓ undone</span>
                  ) : e.undoable ? (
                    <button
                      className="text-accent hover:underline disabled:opacity-50"
                      disabled={undo.isPending && undo.variables === e.id}
                      onClick={() => { setRowError(null); undo.mutate(e.id); }}
                    >
                      {undo.isPending && undo.variables === e.id ? "Undoing…" : "↩ Undo"}
                    </button>
                  ) : (
                    "—"
                  )}
                  {rowError && rowError.id === e.id && (
                    <div className="text-[10px] text-bad mt-1 normal-case">{rowError.msg}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
