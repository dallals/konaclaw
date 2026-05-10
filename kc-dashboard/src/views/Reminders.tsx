import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  listReminders,
  type Reminder,
  type ReminderKind,
  type ReminderStatus,
  type ReminderChannel,
} from "../api/reminders";
import { useReminderEvents } from "../ws/useReminderEvents";

const ALL_STATUSES: ReminderStatus[] = ["pending", "done", "cancelled", "failed", "missed"];
const ALL_CHANNELS: ReminderChannel[] = ["dashboard", "telegram", "imessage"];
const CHANNEL_LABEL: Record<ReminderChannel, string> = {
  dashboard: "DASH", telegram: "TG", imessage: "IMSG",
};

type KindTab = "all" | "reminder" | "cron";

function parseKindTab(s: string | null): KindTab {
  return s === "reminder" || s === "cron" ? s : "all";
}

function formatNextFire(r: Reminder): string {
  if (r.next_fire_at == null) return "—";
  if (r.kind === "cron" && r.cron_spec) {
    // crude; the audit panel will show a friendly version
    return r.cron_spec;
  }
  const delta = r.next_fire_at - Date.now() / 1000;
  if (delta < 0) return "overdue";
  if (delta < 60) return `in ${Math.round(delta)}s`;
  if (delta < 3600) return `in ${Math.round(delta / 60)}m`;
  if (delta < 86400) return `in ${Math.round(delta / 3600)}h`;
  return `in ${Math.round(delta / 86400)}d`;
}

export default function Reminders() {
  useReminderEvents();
  const [params, setParams] = useSearchParams();
  const tab = parseKindTab(params.get("tab"));
  const statuses = (params.getAll("status") as ReminderStatus[]).filter(s => ALL_STATUSES.includes(s));
  const channels = (params.getAll("channel") as ReminderChannel[]).filter(c => ALL_CHANNELS.includes(c));

  const filters = {
    statuses: statuses.length ? statuses : undefined,
    channels: channels.length ? channels : undefined,
    kinds: tab === "all" ? undefined : ([tab] as ReminderKind[]),
  };

  const q = useQuery({
    queryKey: ["reminders", filters],
    queryFn: () => listReminders(filters),
    refetchInterval: 30_000,
  });

  const setTab = (next: KindTab) => {
    if (next === "all") params.delete("tab"); else params.set("tab", next);
    setParams(params, { replace: true });
  };
  const toggleStatus = (s: ReminderStatus) => {
    const cur = params.getAll("status");
    params.delete("status");
    const next = cur.includes(s) ? cur.filter(x => x !== s) : [...cur, s];
    next.forEach(v => params.append("status", v));
    setParams(params, { replace: true });
  };
  const toggleChannel = (c: ReminderChannel) => {
    const cur = params.getAll("channel");
    params.delete("channel");
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    next.forEach(v => params.append("channel", v));
    setParams(params, { replace: true });
  };

  const reminders = q.data?.reminders ?? [];

  return (
    <div className="p-5">
      <div role="tablist" className="flex border-b border-line mb-3">
        {(["all", "reminder", "cron"] as KindTab[]).map(t => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={"px-4 py-2 text-xs uppercase tracking-[0.12em] font-mono border-r border-line "
              + (tab === t ? "text-textStrong border-b-2 border-b-accent" : "text-muted hover:text-text")}
          >
            {t === "all" ? "All" : t === "reminder" ? "One-shot" : "Recurring"}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        <span className="text-xs uppercase tracking-[0.12em] text-muted2 self-center mr-1">Status</span>
        {ALL_STATUSES.map(s => (
          <button
            key={s}
            onClick={() => toggleStatus(s)}
            className={"px-3 py-1 text-xs border "
              + (statuses.includes(s) ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{s}</button>
        ))}
        <span className="text-xs uppercase tracking-[0.12em] text-muted2 self-center mx-2">Channel</span>
        {ALL_CHANNELS.map(c => (
          <button
            key={c}
            onClick={() => toggleChannel(c)}
            className={"px-3 py-1 text-xs border font-mono "
              + (channels.includes(c) ? "border-accent text-text" : "border-line text-muted hover:text-text")}
          >{CHANNEL_LABEL[c]}</button>
        ))}
      </div>

      {q.isLoading && <div className="text-muted text-sm">Loading…</div>}
      {q.isError && <div className="text-bad text-sm">Failed to load reminders.</div>}
      {!q.isLoading && reminders.length === 0 && (
        <div className="text-muted text-sm py-8 text-center">No reminders match these filters.</div>
      )}

      <ul className="divide-y divide-line">
        {reminders.map(r => (
          <li key={r.id} className="flex items-center gap-3 py-2 font-mono text-xs">
            <span className="w-32 text-muted">{formatNextFire(r)}</span>
            <span className={"px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border "
              + (r.kind === "cron" ? "border-accent text-accent" : "border-line text-muted")}>
              {r.kind === "cron" ? "CRON" : "ONE-SHOT"}
            </span>
            <span className="flex-1 text-text truncate" title={r.payload}>{r.payload}</span>
            <span className="text-muted2">{CHANNEL_LABEL[r.channel]}</span>
            {r.status !== "pending" && (
              <span className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-line text-muted">
                {r.status}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
