import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getBaseUrl } from "../api/client";

const REMINDER_EVENT_TYPES = new Set([
  "reminder.created",
  "reminder.cancelled",
  "reminder.snoozed",
  "reminder.fired",
  "reminder.failed",
]);

export function useReminderEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const url = getBaseUrl().replace(/^http/, "ws") + "/ws/reminders";
    const ws = new WebSocket(url);
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (typeof ev?.type === "string" && REMINDER_EVENT_TYPES.has(ev.type)) {
          qc.invalidateQueries({ queryKey: ["reminders"] });
        }
      } catch {
        // ignore non-JSON or malformed payloads
      }
    };
    return () => ws.close();
  }, [qc]);
}
