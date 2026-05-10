import { apiGet, apiPatch, apiDelete } from "./client";

export type ReminderStatus = "pending" | "done" | "cancelled" | "failed" | "missed";
export type ReminderKind = "reminder" | "cron";
export type ReminderChannel = "dashboard" | "telegram" | "imessage";

export type Reminder = {
  id: number;
  kind: ReminderKind;
  agent: string;
  conversation_id: number;
  channel: ReminderChannel;
  chat_id: string;
  payload: string;
  when_utc: number | null;
  cron_spec: string | null;
  status: ReminderStatus;
  attempts: number;
  last_fired_at: number | null;
  created_at: number;
  mode: "literal" | "agent_phrased";
  next_fire_at: number | null;
};

export type ReminderFilters = {
  statuses?: ReminderStatus[];
  kinds?: ReminderKind[];
  channels?: ReminderChannel[];
};

function buildQuery(f: ReminderFilters): string {
  const p = new URLSearchParams();
  f.statuses?.forEach(s => p.append("status", s));
  f.kinds?.forEach(k => p.append("kind", k));
  f.channels?.forEach(c => p.append("channel", c));
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

export const listReminders = (filters: ReminderFilters = {}) =>
  apiGet<{ reminders: Reminder[] }>(`/reminders${buildQuery(filters)}`);

export const cancelReminder = (id: number) => apiDelete(`/reminders/${id}`);

export const snoozeReminder = (id: number, when_utc: number) =>
  apiPatch<Reminder>(`/reminders/${id}`, { when_utc });
