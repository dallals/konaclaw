import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/health";

export default function Monitor() {
  const q = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 5000 });
  return (
    <div className="p-5 space-y-4">
      <h2 className="text-base font-semibold">Monitor</h2>
      <div className="grid grid-cols-3 gap-3.5">
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Status</div>
          <div className="text-2xl font-bold text-good">{q.data?.status ?? "…"}</div>
        </div>
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Uptime</div>
          <div className="text-2xl font-bold">{q.data ? `${Math.round(q.data.uptime_s)}s` : "…"}</div>
        </div>
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Agents</div>
          <div className="text-2xl font-bold">{q.data?.agents ?? "…"}</div>
        </div>
      </div>
    </div>
  );
}
